"""App-owned Lakebase tables: Rider Reports (FR-13) and My Routes (FR-14), see ADR-0006.

The app's service principal creates both schemas on first use, so it owns them. `hsl_reports` is
copied to Unity Catalog by Lakebase Change Data Feed (scripts/setup_lakebase_cdf.py), which needs
REPLICA IDENTITY FULL; `hsl_users` holds emails and is never synced (FR-14.4).
"""

import hashlib

CATEGORIES = {
    "late": "Late",
    "early": "Early",
    "crowded": "Crowded",
    "skipped_stop": "Skipped a stop",
    "breakdown": "Breakdown",
    "other": "Other",
}
NOTE_MAX = 280
PER_VEHICLE_WINDOW_MIN = 2  # FR-13.4
PER_HOUR_LIMIT = 20
RECENT_MIN = 30  # FR-13.5

_category_list = ", ".join(f"'{c}'" for c in CATEGORIES)
SCHEMA_SQL = [
    "CREATE SCHEMA IF NOT EXISTS hsl_reports",
    "CREATE SCHEMA IF NOT EXISTS hsl_users",
    f"""
    CREATE TABLE IF NOT EXISTS hsl_reports.rider_reports (
        report_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        reported_at           timestamptz NOT NULL DEFAULT now(),
        reporter_id           text NOT NULL,
        vehicle_id            text NOT NULL,
        mode                  text NOT NULL,
        route                 text NOT NULL,
        direction             text,
        category              text NOT NULL CHECK (category IN ({_category_list})),
        note                  text CHECK (char_length(note) <= {NOTE_MAX}),
        measured_lateness_s   integer,
        measured_last_seen_at timestamptz,
        lat                   double precision,
        long                  double precision
    )""",
    # Lakebase Change Data Feed needs the full old row on every change.
    "ALTER TABLE hsl_reports.rider_reports REPLICA IDENTITY FULL",
    "CREATE INDEX IF NOT EXISTS rider_reports_recent ON hsl_reports.rider_reports (reported_at DESC)",
    "CREATE INDEX IF NOT EXISTS rider_reports_by_reporter ON hsl_reports.rider_reports (reporter_id, reported_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS hsl_users.watched_routes (
        user_email text NOT NULL,
        route      text NOT NULL,
        added_at   timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (user_email, route)
    )""",
]


class ReportRejected(Exception):
    """A report the viewer can fix or retry later (rate limit, vehicle gone, bad input)."""


def reporter_id(email):
    """FR-13.3: reports carry a hash of the email, never the email."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def clean_note(note):
    note = (note or "").strip()
    return note[:NOTE_MAX] or None


def rate_limit_message(last_hour, same_vehicle_recent):
    if same_vehicle_recent:
        return f"You reported this vehicle in the last {PER_VEHICLE_WINDOW_MIN} minutes. Try again shortly."
    if last_hour >= PER_HOUR_LIMIT:
        return f"You've sent {PER_HOUR_LIMIT} reports in the last hour, the limit. Try again later."
    return None


class Store:
    def __init__(self, lakebase, vehicles_table):
        self.db = lakebase
        self.vehicles = vehicles_table

    def ensure_schema(self):
        def run(cur):
            for stmt in SCHEMA_SQL:
                cur.execute(stmt)

        self.db.transaction(run)

    # ---------------------------------------------------------------- FR-13 Rider Reports

    def add_report(self, email, vehicle_id, category, note=None):
        if category not in CATEGORIES:
            raise ReportRejected(f"Unknown category {category!r}.")
        rid = reporter_id(email)

        def run(cur):
            # One reporter at a time, so two quick clicks can't both pass the limits.
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (rid,))
            cur.execute(
                """
                SELECT count(*),
                       count(*) FILTER (WHERE vehicle_id = %s
                                          AND reported_at > now() - make_interval(mins => %s))
                FROM hsl_reports.rider_reports
                WHERE reporter_id = %s AND reported_at > now() - interval '1 hour'
                """,
                (vehicle_id, PER_VEHICLE_WINDOW_MIN, rid),
            )
            last_hour, same_vehicle = cur.fetchone()
            message = rate_limit_message(last_hour, same_vehicle)
            if message:
                raise ReportRejected(message)
            # FR-13.2: keep what we measured for this vehicle at this moment.
            cur.execute(
                f"""
                INSERT INTO hsl_reports.rider_reports
                    (reporter_id, vehicle_id, mode, route, direction, category, note,
                     measured_lateness_s, measured_last_seen_at, lat, long)
                SELECT %s, v.vehicle_id, v.mode, v.route, v.direction, %s, %s,
                       v.lateness_s, v.last_seen_at, v.lat, v.long
                FROM {self.vehicles} v
                WHERE v.vehicle_id = %s
                RETURNING report_id
                """,
                (rid, category, clean_note(note), vehicle_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ReportRejected("That vehicle is no longer on the map.")
            return row[0]

        return self.db.transaction(run)

    def recent_reports(self, minutes=RECENT_MIN):
        return self.db.query(
            """
            SELECT report_id, reported_at, vehicle_id, mode, route, direction, category, note,
                   measured_lateness_s, lat, long,
                   extract(epoch FROM now() - reported_at)::float AS age_s
            FROM hsl_reports.rider_reports
            WHERE reported_at > now() - make_interval(mins => %s)
            ORDER BY reported_at DESC
            LIMIT 200
            """,
            (minutes,),
        )

    # ---------------------------------------------------------------- FR-14 My Routes

    def watched_routes(self, email):
        _, rows = self.db.query(
            "SELECT route FROM hsl_users.watched_routes WHERE user_email = %s ORDER BY added_at",
            (email,),
        )
        return [r[0] for r in rows]

    def set_watched_routes(self, email, routes):
        routes = sorted({r.strip() for r in routes if r and r.strip()})

        def run(cur):
            cur.execute(
                "DELETE FROM hsl_users.watched_routes WHERE user_email = %s AND NOT (route = ANY(%s))",
                (email, routes),
            )
            for route in routes:
                cur.execute(
                    "INSERT INTO hsl_users.watched_routes (user_email, route) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (email, route),
                )

        self.db.transaction(run)
        return routes
