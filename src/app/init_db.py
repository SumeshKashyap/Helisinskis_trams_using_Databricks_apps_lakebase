"""Create the app-owned Lakebase schemas at app start (FR-13, FR-14, ADR-0006).

Runs as the app's service principal before Streamlit, so the SP owns `hsl_reports` and `hsl_users`
from the first deploy, before any viewer opens the app, and Lakebase Change Data Feed has a schema to follow.
Never fails the app start: app.py retries on first use and degrades if Lakebase is unavailable.
"""

import os
import sys

from db import Lakebase
from store import Store


def main():
    table = f'"{os.getenv("LAKEBASE_SCHEMA", "hsl_live_transit")}"."gold_vehicle_current_online"'
    try:
        Store(Lakebase(), table).ensure_schema()
        print("init_db: hsl_reports and hsl_users ready", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"init_db: could not prepare Lakebase schemas: {e}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
