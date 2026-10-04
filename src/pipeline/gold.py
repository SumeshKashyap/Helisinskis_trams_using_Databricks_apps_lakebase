"""Gold: current vehicle state (FR-4.1), departures (FR-4.2) and minute-level Coverage (FR-4.3).

silver_stops / silver_routes come from the daily GTFS job, not this pipeline. Their schema is
set by `hsl.reference_schema`, so a replay target can reuse the live reference data.
"""

from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

HELSINKI_TZ = "Europe/Helsinki"
RETENTION_DAYS = 30  # FR-4.4
# Heartbeats come every 10 s. A longer silence inside one session counts as a Data Gap.
MAX_HEARTBEAT_SPACING_S = 30

REFERENCE = f"{spark.conf.get('hsl.catalog')}.{spark.conf.get('hsl.reference_schema')}"


@dp.temporary_view(name="vehicle_positions_for_current")
def vehicle_positions_for_current():
    return (
        spark.readStream.option("skipChangeCommits", "true")
        .table("silver_position_events")
        .select(
            "vehicle_id",
            "mode",
            "route",
            "direction",
            "journey_id",
            "lat",
            "long",
            "heading",
            "speed_ms",
            "lateness_s",
            F.col("tst").alias("last_seen_at"),
        )
    )


dp.create_streaming_table(
    name="gold_vehicle_current",
    comment="Latest Position Event per Vehicle (SCD type 1). Source for the Lakebase synced table.",
    table_properties={"delta.enableChangeDataFeed": "true"},  # FR-4.1 / ADR-0003
)

dp.create_auto_cdc_flow(
    target="gold_vehicle_current",
    source="vehicle_positions_for_current",
    keys=["vehicle_id"],
    sequence_by="last_seen_at",
    stored_as_scd_type=1,
)


@dp.table(
    name="gold_departures",
    comment="One row per tram departure Stop Event, with stop and route names from GTFS. "
    "Punctuality is computed at query time (FR-4.2).",
)
def gold_departures():
    stops = spark.read.table(f"{REFERENCE}.silver_stops").select(
        "stop_id",
        "stop_name",
        F.col("lat").alias("stop_lat"),
        F.col("long").alias("stop_long"),
    )
    routes = spark.read.table(f"{REFERENCE}.silver_routes")
    route_by_id = routes.select("hsl_route_id", "route_name")
    # Fallback for HFP route ids GTFS doesn't list (temporary variants like 1010B, "1007 9"):
    # match on the rider-facing route instead.
    route_by_name = routes.groupBy("route").agg(F.min("route_name").alias("route_name_by_route"))
    return (
        spark.readStream.option("skipChangeCommits", "true")
        .table("silver_stop_events")
        # ADR-0004: metro publishes no Stop Events or Lateness; keep it out explicitly.
        .where("event_type = 'dep' AND mode = 'tram'")
        .join(stops, "stop_id", "left")
        .join(route_by_id, "hsl_route_id", "left")
        .join(route_by_name, "route", "left")
        .select(
            "operating_day",
            "route",
            F.coalesce("route_name", "route_name_by_route").alias("route_name"),
            "direction",
            "journey_id",
            "vehicle_id",
            "stop_id",
            "stop_name",
            "stop_lat",
            "stop_long",
            F.col("tst").alias("departed_at"),
            F.from_utc_timestamp("tst", HELSINKI_TZ).alias("departed_at_local"),
            "scheduled_departure",
            "lateness_s",
            "timetable_lateness_s",  # ADR-0005
        )
    )


@dp.materialized_view(
    name="gold_coverage_minutely",
    comment="Seconds of each UTC minute covered by an Ingestion Session (FR-4.3). "
    "Minutes between the first and last heartbeat with coverage 0 are Data Gaps.",
)
def gold_coverage_minutely():
    hb = spark.read.table("silver_heartbeats").where(
        F.col("heartbeat_at") >= F.current_timestamp() - F.expr(f"INTERVAL {RETENTION_DAYS} DAYS")
    )
    # Covered interval = two consecutive heartbeats of one session, close enough together.
    intervals = (
        hb.withColumn(
            "next_at",
            F.lead("heartbeat_at").over(Window.partitionBy("session_id").orderBy("heartbeat_at")),
        )
        .where(
            F.col("next_at").isNotNull()
            & (F.unix_timestamp("next_at") - F.unix_timestamp("heartbeat_at") <= MAX_HEARTBEAT_SPACING_S)
        )
        .select(F.col("heartbeat_at").alias("from_at"), F.col("next_at").alias("to_at"))
    )
    minute = F.expr("INTERVAL 1 MINUTE")
    covered = (
        intervals.withColumn(
            "minute_start",
            F.explode(F.sequence(F.date_trunc("minute", "from_at"), F.date_trunc("minute", "to_at"), minute)),
        )
        .withColumn(
            "overlap_s",
            (
                F.unix_micros(F.least("to_at", F.col("minute_start") + minute))
                - F.unix_micros(F.greatest("from_at", "minute_start"))
            )
            / 1e6,
        )
        .groupBy("minute_start")
        .agg(F.sum(F.greatest("overlap_s", F.lit(0.0))).alias("covered_s"))
    )
    spine = hb.agg(
        F.date_trunc("minute", F.min("heartbeat_at")).alias("first_minute"),
        F.date_trunc("minute", F.max("heartbeat_at")).alias("last_minute"),
    ).select(F.explode(F.sequence("first_minute", "last_minute", minute)).alias("minute_start"))
    return (
        spine.join(covered, "minute_start", "left")
        .select(
            "minute_start",
            F.round(F.least(F.coalesce("covered_s", F.lit(0.0)), F.lit(60.0)), 1).alias("covered_s"),
        )
        .withColumn("coverage", F.round(F.col("covered_s") / 60, 3))
    )
