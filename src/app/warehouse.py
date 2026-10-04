"""SQL Warehouse access for the analytics views (ADR-0003: analytics read Delta through the warehouse).

One short connection per query: the views query on demand, and a cached connection would be
shared across Streamlit sessions, which the connector doesn't support.
"""

import os

from databricks import sql
from databricks.sdk.core import Config


class Warehouse:
    def __init__(self):
        self.cfg = Config()
        self.http_path = f"/sql/1.0/warehouses/{os.environ['DATABRICKS_WAREHOUSE_ID']}"
        catalog = os.getenv("UC_CATALOG", "my_databricks_workspace")
        schema = os.getenv("UC_SCHEMA", "hsl_live_transit")
        self.prefix = f"`{catalog}`.`{schema}`"

    def table(self, name):
        return f"{self.prefix}.`{name}`"

    def query(self, statement, params=None):
        """Run a query with named parameters and return a pandas DataFrame."""
        with (
            sql.connect(
                server_hostname=self.cfg.host,
                http_path=self.http_path,
                credentials_provider=lambda: self.cfg.authenticate,
            ) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(statement, params or {})
            return cur.fetchall_arrow().to_pandas()
