"""Turn on Lakebase Change Data Feed for Rider Reports (FR-13.6, ADR-0006): Lakebase schema `hsl_reports`
→ Unity Catalog `<catalog>.<schema>.lb_rider_reports_history` (CDC history; Public Preview, enabled under the workspace Previews page).

The bundle can't declare it. Run once, after the app has started at least once (its init_db.py
creates `hsl_reports` as the app's service principal):

    uv run --with databricks-sdk scripts/setup_lakebase_cdf.py --profile dev

Safe to rerun: an existing config for the schema is kept. `hsl_users` is never synced (FR-14.4).
"""

import argparse
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.postgres import CdfConfig

POSTGRES_SCHEMA = "hsl_reports"
CONFIG_ID = "hsl_reports"  # must match [a-z][a-z0-9_]{0,62}: no hyphens


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="dev")
    p.add_argument("--project", default="hsl-live-transit")
    p.add_argument("--branch", default="production")
    p.add_argument("--catalog", default="my_databricks_workspace")
    p.add_argument("--schema", default="hsl_live_transit")
    p.add_argument("--wait-minutes", type=float, default=10)
    args = p.parse_args()

    w = WorkspaceClient(profile=args.profile)
    branch = f"projects/{args.project}/branches/{args.branch}"
    # The parent is the database *resource* path, not the Postgres name databricks_postgres.
    database = next(
        d.name
        for d in w.postgres.list_databases(parent=branch)
        if d.status.postgres_database == "databricks_postgres"
    )

    try:
        configs = list(w.postgres.list_cdf_configs(parent=database))
    except NotFound:  # none yet: the API answers 404, not an empty list
        configs = []
    existing = [c for c in configs if c.postgres_schema == POSTGRES_SCHEMA]
    if existing:
        print(f"Lakebase Change Data Feed already configured: {existing[0].name}")
    else:
        op = w.postgres.create_cdf_config(
            parent=database,
            cdf_config=CdfConfig(catalog=args.catalog, schema=args.schema, postgres_schema=POSTGRES_SCHEMA),
            cdf_config_id=CONFIG_ID,
        )
        print(f"created: {op.wait().name}")

    deadline = time.time() + args.wait_minutes * 60
    while True:
        try:
            statuses = list(w.postgres.list_cdf_statuses(parent=database))
        except NotFound:
            statuses = []
        for s in statuses:
            print(s.as_dict())
        if any("rider_reports" in str(s.as_dict()) for s in statuses) or time.time() > deadline:
            break
        time.sleep(20)
    print(f"History table: {args.catalog}.{args.schema}.lb_rider_reports_history")


if __name__ == "__main__":
    main()
