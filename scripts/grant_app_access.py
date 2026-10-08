"""Give the app's service principal read access it needs, and nothing more (NFR-6).

1. Lakebase: read the synced schema (live map, FR-6.2).
2. Unity Catalog: SELECT on the tables the analytics views, health strip and Genie read
   (FR-8 to FR-10, FR-12). Genie runs its SQL as the app's service principal, so it needs these too.
3. The Bike, walk or wait agent (FR-15, ADR-0007/0008): EXECUTE on its UC functions, USE CONNECTION on
   its HTTP connections, and READ on the secret scope holding the Digitransit key. Run
   scripts/setup_agents.py first.

The bundle can't declare Postgres GRANTs or table-level UC grants, so run this once after the first
`databricks bundle deploy -t dev` (and again if the synced schema or a table is ever recreated):

    uv run --with "psycopg[binary]" --with databricks-sdk scripts/grant_app_access.py --profile dev
"""

import argparse

import psycopg
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState
from databricks.sdk.service.workspace import AclPermission

# Everything the app reads through the warehouse. Not bronze, not the other silver tables.
UC_TABLES = [
    "gold_vehicle_current",
    "gold_departures",
    "gold_coverage_minutely",
    "silver_stops",
    "silver_routes",
    "silver_heartbeats",  # health strip: session state and events/s (FR-10.1)
    "lb_rider_reports_history",  # Rider Reports via Lakebase Change Data Feed, for Genie (FR-13.6)
]
WAREHOUSE_NAME = "hsl-live-transit-analytics"
# Created by scripts/setup_agents.py. Nested functions need EXECUTE too.
AGENT_FUNCTIONS = [
    "find_stop", "weather_outlook", "tram_lateness", "parse_trip_options", "trip_options",
    "decide_trip", "bike_walk_or_wait", "tram_bunching", "bunching_now",
]
AGENT_CONNECTIONS = ["hsl_fmi", "hsl_digitransit"]
SECRET_SCOPE = "hsl_live_transit"


def grant_lakebase(w, args, sp):
    endpoint = f"projects/{args.project}/branches/{args.branch}/endpoints/primary"
    host = w.postgres.get_endpoint(name=endpoint).status.hosts.host
    token = w.postgres.generate_database_credential(endpoint=endpoint).token
    me = w.current_user.me().user_name

    with psycopg.connect(
        host=host, dbname="databricks_postgres", user=me, password=token, sslmode="require", autocommit=True
    ) as conn:
        for stmt in (
            f'GRANT USAGE ON SCHEMA "{args.schema}" TO "{sp}"',
            f'GRANT SELECT ON ALL TABLES IN SCHEMA "{args.schema}" TO "{sp}"',
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{args.schema}" GRANT SELECT ON TABLES TO "{sp}"',
        ):
            conn.execute(stmt)
            print(stmt)
        n = conn.execute(f'SELECT count(*) FROM "{args.schema}".gold_vehicle_current_online').fetchone()[0]
        print(f"{n} vehicles in {args.schema}.gold_vehicle_current_online")


def grant_unity_catalog(w, args, sp):
    warehouse = next(wh for wh in w.warehouses.list() if wh.name == WAREHOUSE_NAME)
    statements = [
        f"GRANT USE CATALOG ON CATALOG `{args.catalog}` TO `{sp}`",
        f"GRANT USE SCHEMA ON SCHEMA `{args.catalog}`.`{args.schema}` TO `{sp}`",
    ]
    for t in UC_TABLES:
        if w.tables.exists(f"{args.catalog}.{args.schema}.{t}").table_exists:
            statements.append(f"GRANT SELECT ON TABLE `{args.catalog}`.`{args.schema}`.`{t}` TO `{sp}`")
        else:
            print(f"skipped {t}: doesn't exist yet (rerun after it does)")
    statements += [
        f"GRANT EXECUTE ON FUNCTION `{args.catalog}`.`{args.schema}`.`{f}` TO `{sp}`" for f in AGENT_FUNCTIONS
    ]
    statements += [f"GRANT USE CONNECTION ON CONNECTION `{c}` TO `{sp}`" for c in AGENT_CONNECTIONS]
    for stmt in statements:
        resp = w.statement_execution.execute_statement(
            statement=stmt, warehouse_id=warehouse.id, wait_timeout="50s"
        )
        if resp.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(f"{stmt}: {resp.status.state} {resp.status.error}")
        print(stmt)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="dev")
    p.add_argument("--project", default="hsl-live-transit")
    p.add_argument("--branch", default="production")
    p.add_argument("--catalog", default="my_databricks_workspace")
    p.add_argument("--schema", default="hsl_live_transit")
    p.add_argument("--app", default="hsl-live-transit")
    p.add_argument("--skip-lakebase", action="store_true", help="Only run the Unity Catalog grants.")
    args = p.parse_args()

    w = WorkspaceClient(profile=args.profile)
    sp = w.apps.get(name=args.app).service_principal_client_id
    if not args.skip_lakebase:
        grant_lakebase(w, args, sp)
    grant_unity_catalog(w, args, sp)
    w.secrets.put_acl(SECRET_SCOPE, sp, AclPermission.READ)
    print(f"secret scope {SECRET_SCOPE}: READ for {sp}")


if __name__ == "__main__":
    main()
