"""Lakebase connection for the app: the live map's synced table (FR-6.2) and the app-owned
tables for Rider Reports and My Routes (FR-13, FR-14, ADR-0006).

The app's service principal logs in with a short-lived OAuth token (valid ~1 h), so the
connection is rebuilt before the token expires or after any error. One connection is shared by
all Streamlit sessions, so a lock keeps one session's transaction from swallowing another's query.
"""

import os
import threading
import time

import psycopg
from databricks.sdk import WorkspaceClient

TOKEN_TTL_S = 45 * 60


class Lakebase:
    def __init__(self):
        self.endpoint = os.environ["LAKEBASE_ENDPOINT"]
        self.w = WorkspaceClient()
        self._conn = None
        self._opened_at = 0.0
        self._lock = threading.RLock()

    def _connect(self):
        token = self.w.postgres.generate_database_credential(endpoint=self.endpoint).token
        return psycopg.connect(
            host=os.environ["PGHOST"],
            port=os.getenv("PGPORT", "5432"),
            dbname=os.getenv("PGDATABASE", "databricks_postgres"),
            user=os.getenv("PGUSER") or self.w.config.client_id,
            password=token,
            sslmode=os.getenv("PGSSLMODE", "require"),
            autocommit=True,
            connect_timeout=10,
        )

    def _run(self, fn):
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None or self._conn.closed or time.time() - self._opened_at > TOKEN_TTL_S:
                        self._conn = self._connect()
                        self._opened_at = time.time()
                    return fn(self._conn)
                except psycopg.OperationalError:
                    # Scaled-to-zero compute waking up, or an expired token: reconnect once.
                    self._conn = None
                    if attempt == 2:
                        raise

    def query(self, sql, params=None):
        def run(conn):
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d.name for d in cur.description] if cur.description else []
                return cols, (cur.fetchall() if cur.description else [])

        return self._run(run)

    def transaction(self, fn):
        """Run fn(cursor) in one transaction; commits on return, rolls back on any exception."""

        def run(conn):
            with conn.transaction(), conn.cursor() as cur:
                return fn(cur)

        return self._run(run)
