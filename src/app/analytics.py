"""SQL and pure helpers for the analytics views (FR-8, FR-9, FR-10). No Streamlit, no database.

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
