"""Silver: typed Position Events and Stop Events (FR-3.1 … FR-3.5), heartbeats and
Ingestion Sessions (FR-3.6).

Streaming reads use skipChangeCommits: the retention job deletes old rows (FR-2.2, FR-3.7).
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

# Every HFP payload is {"<EVENT_TYPE>": {...}}; same field set for VP/ARR/PDE/DEP.
# stop is read as a string so it joins to GTFS stop_id later.
PAYLOAD_SCHEMA = """MAP<STRING, STRUCT<
    desi STRING, dir STRING, oper INT, veh INT, tst STRING, spd DOUBLE, hdg INT,
    lat DOUBLE, long DOUBLE, dl INT, drst INT, oday STRING, start STRING, loc STRING,
    stop STRING, route STRING, occu INT, ttarr STRING, ttdep STRING>>"""

DEDUP_WATERMARK = "2 minutes"  # FR-3.4; ~4 vp msg/s per tram, so keep state small
DEDUP_KEYS = ["vehicle_id", "event_type", "tst"]

ROW_REQUIRED = {
    "tst_present": "tst IS NOT NULL",
    "vehicle_id_present": "vehicle_id IS NOT NULL",
}
LATENESS_PLAUSIBLE = "lateness_s IS NULL OR abs(lateness_s) <= 3600"  # FR-3.5: warn only


@dp.temporary_view(name="hfp_events_parsed")
def hfp_events_parsed():
    e = F.col("e")
    return (
        spark.readStream.option("skipChangeCommits", "true")
        .table("bronze_hfp_events")
        .where("kind = 'event'")
        .select(
            F.split("topic", "/")[5].alias("event_type"),
            F.split("topic", "/")[6].alias("mode"),
            F.map_values(F.from_json("payload", PAYLOAD_SCHEMA))[0].alias("e"),
            "received_at",
            "session_id",
        )
        .select(
            "event_type",
            "mode",
            e.tst.cast("timestamp").alias("tst"),
            F.concat(e.oper, F.lit("/"), e.veh).alias("vehicle_id"),  # null if either part is
            e.desi.alias("route"),
            e.dir.alias("direction"),
            F.to_date(e.oday).alias("operating_day"),
            e.start.alias("journey_start"),
            # FR-3.2: one scheduled run = route + direction + operating day + start time
            F.concat_ws("|", e.desi, e.dir, e.oday, e.start).alias("journey_id"),
            e.route.alias("hsl_route_id"),
            e.lat.alias("lat"),
            e.long.alias("long"),
            e.spd.alias("speed_ms"),
            e.hdg.alias("heading"),
            # ADR-0002: Lateness = -dl, positive = late. dl is not carried forward (FR-3.3).
            (-e.dl).alias("lateness_s"),
            e.stop.alias("stop_id"),
            e.ttarr.cast("timestamp").alias("scheduled_arrival"),
            e.ttdep.cast("timestamp").alias("scheduled_departure"),
            e.loc.alias("location_source"),
            e.drst.alias("door_status"),
            e.occu.alias("occupancy"),
            "received_at",
            "session_id",
        )
        # ADR-0005: Timetable Lateness, next to Lateness, never instead of it. Stop Events only:
        # arrivals against ttarr, pre-departures and departures against ttdep.
        .withColumn(
            "timetable_lateness_s",
            F.round(
                (
                    F.unix_micros("tst")
                    - F.unix_micros(
                        F.when(F.col("event_type") == "arr", F.col("scheduled_arrival")).otherwise(
                            F.col("scheduled_departure")
                        )
                    )
                )
                / 1e6
            ).cast("int"),
        )
    )


def _deduped(event_types):
    return (
        spark.readStream.table("hfp_events_parsed")
        .where(F.col("event_type").isin(event_types))
        .withWatermark("tst", DEDUP_WATERMARK)
        .dropDuplicatesWithinWatermark(DEDUP_KEYS)
    )


@dp.table(
    name="silver_position_events",
    comment="Position Events (HSL vp) for trams and metro, typed and deduplicated.",
)
@dp.expect_all_or_drop(
    {
        **ROW_REQUIRED,
        # Helsinki region bounding box (FR-3.5)
        "inside_helsinki_region": "lat BETWEEN 59.9 AND 60.6 AND long BETWEEN 24.2 AND 25.6",
    }
)
@dp.expect("lateness_plausible", LATENESS_PLAUSIBLE)
def silver_position_events():
    return _deduped(["vp"]).drop(
        "stop_id", "scheduled_arrival", "scheduled_departure", "timetable_lateness_s"
    )


@dp.table(
    name="silver_stop_events",
    comment="Stop Events (HSL arr, pde, dep), typed and deduplicated. Trams only in practice (ADR-0004).",
)
@dp.expect_all_or_drop(ROW_REQUIRED)
@dp.expect("lateness_plausible", LATENESS_PLAUSIBLE)
def silver_stop_events():
    return _deduped(["arr", "pde", "dep"])


@dp.table(
    name="silver_heartbeats",
    comment="One row per heartbeat (every 10 s while subscribed), with running message totals.",
)
def silver_heartbeats():
    totals = F.from_json("payload", "received_total BIGINT, dropped_total BIGINT")
    return (
        spark.readStream.option("skipChangeCommits", "true")
        .table("bronze_hfp_events")
        .where("kind = 'heartbeat'")
        .select(
            "session_id",
            F.col("received_at").alias("heartbeat_at"),
            totals.received_total.alias("received_total"),
            totals.dropped_total.alias("dropped_total"),
        )
    )


@dp.materialized_view(
    name="silver_ingestion_sessions",
    comment="One row per Ingestion Session (FR-3.6). Totals are as of the last heartbeat.",
)
def silver_ingestion_sessions():
    return (
        spark.read.table("silver_heartbeats")
        .groupBy("session_id")
        .agg(
            F.min("heartbeat_at").alias("started_at"),
            F.max("heartbeat_at").alias("last_heartbeat_at"),
            F.max("received_total").alias("events_received"),
            F.max("dropped_total").alias("events_dropped"),
        )
    )

