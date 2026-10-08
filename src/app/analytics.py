"""SQL and pure helpers for the analytics views (FR-8, FR-9, FR-10, FR-16). No Streamlit, no database.

Every query takes named parameters (`:since`, `:until`, …) for the Databricks SQL connector.
Punctuality is computed here, at query time, because the On-time window is the viewer's (FR-4.2).
"""

import datetime as dt
from zoneinfo import ZoneInfo

HELSINKI = ZoneInfo("Europe/Helsinki")
# HSL runs night journeys after midnight on the previous Operating Day; the new one starts here.
OPERATING_DAY_STARTS_AT = dt.time(4, 30)
COVERAGE_WARNING_BELOW = 0.9  # FR-8.3
LIVE_HEARTBEAT_S = 30  # same spacing that counts as a Data Gap in gold_coverage_minutely
CHART_BUCKET_MIN = 5

WINDOWS = {"last_hour": "Last hour", "operating_day": "Today (Operating Day)"}


def operating_day(now_utc):
    """Operating Day for a UTC instant: the Helsinki date, until 04:30 the next morning."""
    local = now_utc.astimezone(HELSINKI)
    if local.time() < OPERATING_DAY_STARTS_AT:
        local -= dt.timedelta(days=1)
    return local.date()


def window_bounds(window, now_utc):
    """(since_utc, until_utc, operating_day or None) for a window key."""
    if window == "last_hour":
        return now_utc - dt.timedelta(hours=1), now_utc, None
    if window == "operating_day":
        day = operating_day(now_utc)
        start = dt.datetime.combine(day, OPERATING_DAY_STARTS_AT, tzinfo=HELSINKI)
        return start.astimezone(dt.UTC), now_utc, day
    raise ValueError(f"unknown window {window!r}")


def window_params(window, now_utc):
    """SQL filter on gold_departures (alias d) plus its parameters."""
    since, until, day = window_bounds(window, now_utc)
    params = {"since": since, "until": until}
    if day is None:
        return "d.departed_at >= :since AND d.departed_at < :until", params
    # Today = the current Operating Day, which also keeps after-midnight journeys together.
    return "d.operating_day = :operating_day AND d.departed_at < :until", {
        **params,
        "operating_day": day,
    }


def coverage_fraction(covered_s, since, until):
    seconds = (until - since).total_seconds()
    if seconds <= 0:
        return None
    return max(0.0, min(1.0, (covered_s or 0.0) / seconds))


def gap_intervals(gap_minutes):
    """Merge sorted UTC minute starts with zero Coverage into (start, end) Data Gap intervals."""
    out = []
    step = dt.timedelta(minutes=1)
    for m in sorted(gap_minutes):
        if out and m <= out[-1][1]:
            out[-1] = (out[-1][0], m + step)
        else:
            out.append((m, m + step))
    return out


def route_sort_key(route):
    """Natural order for route short names: 1, 2, 3, 4T, 10, 10H."""
    digits = "".join(c for c in route if c.isdigit())
    return (int(digits) if digits else 10**6, route)


def punctuality_sql(table, window_filter):
    # FR-8.1. Unknown Lateness stays in the denominator: it is never on-time (CONTEXT.md).
    return f"""
        SELECT d.route,
               max(d.route_name) AS route_name,
               d.direction,
               count(*) AS departures,
               count_if(d.lateness_s BETWEEN :early_s AND :late_s) / count(*) AS punctuality,
               avg(d.lateness_s) AS avg_lateness_s,
               percentile_approx(d.lateness_s, 0.9) AS p90_lateness_s,
               count_if(d.lateness_s IS NULL) AS lateness_unknown
        FROM {table("gold_departures")} d
        WHERE {window_filter}
        GROUP BY d.route, d.direction
    """


def stop_lateness_sql(table, window_filter, by_route):
    # FR-9.1. Stops without GTFS coordinates can't be drawn.
    route = "AND d.route = :route" if by_route else ""
    return f"""
        SELECT d.stop_id,
               max(d.stop_name) AS stop_name,
               any_value(d.stop_lat) AS lat,
               any_value(d.stop_long) AS long,
               count(*) AS departures,
               avg(d.lateness_s) AS avg_lateness_s,
               array_join(sort_array(collect_set(d.route)), ', ') AS routes
        FROM {table("gold_departures")} d
        WHERE {window_filter} AND d.stop_lat IS NOT NULL {route}
        GROUP BY d.stop_id
    """


def departures_over_time_sql(table, window_filter):
    # FR-10.2: the event-time chart that shades Data Gaps.
    bucket_s = CHART_BUCKET_MIN * 60
    return f"""
        SELECT timestamp_seconds(floor(unix_timestamp(d.departed_at) / {bucket_s}) * {bucket_s}) AS bucket_start,
               count(*) AS departures,
               count_if(d.lateness_s BETWEEN :early_s AND :late_s) AS on_time
        FROM {table("gold_departures")} d
        WHERE {window_filter}
        GROUP BY 1
        ORDER BY 1
    """


def coverage_sql(table):
    # FR-8.3 / FR-9.3: covered seconds in [since, until).
    return f"""
        SELECT coalesce(sum(c.covered_s), 0) AS covered_s
        FROM {table("gold_coverage_minutely")} c
        WHERE c.minute_start >= date_trunc('MINUTE', :since) AND c.minute_start < :until
    """


def gap_minutes_sql(table):
    return f"""
        SELECT c.minute_start
        FROM {table("gold_coverage_minutely")} c
        WHERE c.minute_start >= date_trunc('MINUTE', :since) AND c.minute_start < :until AND c.coverage = 0
        ORDER BY 1
    """


def health_sql(table):
    """FR-10.1 in one round trip: latest session, events/s over its last minute, newest event, last gap."""
    return f"""
        WITH latest AS (
            SELECT h.session_id, max(h.heartbeat_at) AS last_heartbeat_at
            FROM {table("silver_heartbeats")} h
            GROUP BY h.session_id
            ORDER BY last_heartbeat_at DESC
            LIMIT 1
        ),
        last_minute AS (
            SELECT (max(h.received_total) - min(h.received_total))
                   / nullif(unix_timestamp(max(h.heartbeat_at)) - unix_timestamp(min(h.heartbeat_at)), 0) AS events_per_s
            FROM {table("silver_heartbeats")} h
            JOIN latest l ON h.session_id = l.session_id
            WHERE h.heartbeat_at >= l.last_heartbeat_at - INTERVAL 60 SECONDS
        ),
        gaps AS (
            SELECT c.minute_start,
                   c.minute_start - make_dt_interval(0, 0, row_number() OVER (ORDER BY c.minute_start)) AS island
            FROM {table("gold_coverage_minutely")} c
            WHERE c.coverage = 0
        ),
        last_gap AS (
            SELECT min(g.minute_start) AS gap_start, count(*) AS gap_minutes
            FROM gaps g
            GROUP BY g.island
            ORDER BY gap_start DESC
            LIMIT 1
        )
        SELECT (SELECT l.last_heartbeat_at FROM latest l) AS last_heartbeat_at,
               (SELECT m.events_per_s FROM last_minute m) AS events_per_s,
               (SELECT max(v.last_seen_at) FROM {table("gold_vehicle_current")} v) AS last_event_at,
               (SELECT g.gap_start FROM last_gap g) AS last_gap_start,
               (SELECT g.gap_minutes FROM last_gap g) AS last_gap_minutes,
               current_timestamp() AS now
    """


def my_routes_summary(vehicles, reports, routes):
    """FR-14.2: per watched Route, vehicles on the map, worst current Lateness and recent Rider Reports.

    `vehicles` and `reports` are DataFrames (possibly empty) with a `route` column; vehicles also
    has `lateness_s`. Worst Lateness is None when no vehicle on the route publishes it (metro).
    """
    out = []
    for route in routes:
        on_route = vehicles[vehicles["route"] == route] if not vehicles.empty else vehicles
        known = on_route["lateness_s"].dropna() if not on_route.empty else []
        n_reports = int((reports["route"] == route).sum()) if not reports.empty else 0
        out.append(
            {
                "route": route,
                "vehicles": len(on_route),
                "worst_lateness_s": int(max(known)) if len(known) else None,
                "reports": n_reports,
            }
        )
    return out


# FR-16 Bunching: Headway and the Bunching flag come from the UC function tram_bunching (ADR-0007), so
# the app, the agent and SQL users share one definition.
BUNCHING_SHARE = 0.25  # FR-16.2 default


def bunching_sql(prefix):
    return f"SELECT * FROM {prefix}.tram_bunching(:since, :until, :max_share)"


def bunching_now_sql(prefix):
    return f"SELECT * FROM {prefix}.bunching_now(:route)"


def bunching_by_route(pairs):
    """FR-16.5: per tram Route and direction, how often and by how many trams Bunching happened.

    Each row of `pairs` is one departure compared with the tram that left the same Stop just before it,
    so one pair of trams running together counts once at every Stop it passes:
    - departures: departures compared (with a planned frequency; the others can't be judged),
    - bunched_departures: those below the Bunching threshold, and `share` of them,
    - tram_pairs: distinct (leader, follower) Vehicle pairs among them,
    - median_headway_s: median Headway of the bunched departures (None when there are none).
    """
    judged = pairs[pairs["scheduled_headway_s"].notna()] if not pairs.empty else pairs
    if judged.empty:
        return []
    out = []
    for (route, direction), g in judged.groupby(["route", "direction"], sort=False):
        bunched = g[g["bunching"].astype(bool)]
        out.append(
            {
                "route": route,
                "direction": direction,
                "departures": len(g),
                "bunched_departures": len(bunched),
                "share": len(bunched) / len(g),
                "tram_pairs": len(bunched[["leader_vehicle_id", "follower_vehicle_id"]].drop_duplicates()),
                "median_headway_s": int(bunched["headway_s"].median()) if len(bunched) else None,
            }
        )
    return sorted(out, key=lambda r: (-r["bunched_departures"], route_sort_key(r["route"]), r["direction"]))


def bunching_by_stop(pairs):
    """FR-16.5: Stops where Bunching happened, with how often and on which Routes."""
    if pairs.empty:
        return []
    b = pairs[pairs["bunching"].astype(bool)]
    out = []
    for (stop_id, name, lat, long), g in b.groupby(["stop_id", "stop_name", "stop_lat", "stop_long"]):
        routes = ", ".join(sorted(g["route"].unique(), key=route_sort_key))
        out.append(
            {"stop_id": stop_id, "stop_name": name, "lat": lat, "long": long, "bunching": len(g), "routes": routes}
        )
    return sorted(out, key=lambda r: -r["bunching"])


def pair_lines(now_pairs, vehicles):
    """FR-16.3: a map line from each bunched leader to its follower, at their current positions.

    Pairs where either Vehicle isn't on the live map are left out.
    """
    if now_pairs.empty or vehicles.empty:
        return []
    pos = {v: (lo, la) for v, lo, la in zip(vehicles["vehicle_id"], vehicles["long"], vehicles["lat"])}
    out = []
    for r in now_pairs.to_dict("records"):
        a, b = pos.get(r["leader_vehicle_id"]), pos.get(r["follower_vehicle_id"])
        if a and b:
            out.append({"route": r["route"], "headway_s": int(r["headway_s"]), "from": list(a), "to": list(b)})
    return out
