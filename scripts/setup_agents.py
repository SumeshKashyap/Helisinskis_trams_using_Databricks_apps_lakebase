"""Set up the agent tools: Bike, walk or wait (FR-15) and the Bunching spotter (FR-16), ADR-0007/0008.

1. Unity Catalog HTTP connections `hsl_fmi` (weather) and `hsl_digitransit` (journey planner, city bikes).
2. The tool functions in src/agents/functions.sql, plus `decide_trip`, built from src/agents/rules.py.
3. An Agent Bricks Supervisor Agent with those functions and the FR-12 Genie space as tools.

The bundle can't declare connections, functions or Supervisor Agents, so run this after
`databricks bundle deploy -t dev`, and again after changing the SQL, the rules or the instructions:

    uv run --with databricks-sdk scripts/setup_agents.py --profile dev [--check] [--skip-agent]

The Digitransit key must be in secret scope `hsl_live_transit`, key `digitransit_key` (NFR-6):

    databricks secrets put-secret hsl_live_transit digitransit_key -p dev
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.common.types.fieldmask import FieldMask
from databricks.sdk.service import supervisoragents as sa
from databricks.sdk.service.sql import StatementState

ROOT = Path(__file__).parents[1]
WAREHOUSE_NAME = "hsl-live-transit-analytics"
GENIE_TITLE = "HSL Live Transit"
AGENT_NAME = "HSL Live Transit agent"
SECRET_SCOPE, SECRET_KEY = "hsl_live_transit", "digitransit_key"

# Digitransit wants its key as a header or URL parameter, not a bearer token; the function sends it as
# a parameter from the secret scope. HTTP connections require a bearer token, so they get a dummy.
CONNECTIONS = {
    "hsl_fmi": ("https://opendata.fmi.fi", "/"),
    "hsl_digitransit": ("https://api.digitransit.fi", "/routing/v2/hsl"),
}

DECIDE_ARGS = (
    "tram_arrive_s INT, walk_arrive_s INT, bike_arrive_s INT, precipitation_mm_h DOUBLE, "
    "rain_probability_pct DOUBLE, gust_ms DOUBLE, temperature_c DOUBLE, in_bike_season BOOLEAN"
)

# One prompt for both hosts: the in-app loop (ADR-0008) and the Supervisor Agent.
sys.path.insert(0, str(ROOT / "src" / "app"))
from agent import SYSTEM_PROMPT as INSTRUCTIONS

EXAMPLES = [
    (
        "I'm at Kaivopuisto and need to get to Rautatientori. The tram seems late, should I grab a city bike?",
        ["Call bike_walk_or_wait(from_stop='Kaivopuisto', to_stop='Rautatientori')",
         "State the choice and reason as returned, then the weather and bike numbers"],
    ),
    (
        "Is tram 4 running late?",
        ["Call tram_lateness('4')", "If no row is fresh or fading, say Lateness is unknown, not on time"],
    ),
    (
        "Which tram routes were most late today?",
        ["Use the Genie space"],
    ),
]


def sql_runner(w, catalog, schema):
    warehouse = next(wh for wh in w.warehouses.list() if wh.name == WAREHOUSE_NAME)

    def run(statement):
        r = w.statement_execution.execute_statement(
            statement=statement, warehouse_id=warehouse.id, catalog=catalog, schema=schema, wait_timeout="50s"
        )
        while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
            r = w.statement_execution.get_statement(r.statement_id)
        if r.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(f"{r.status.error.message}\n--- in ---\n{statement[:400]}")
        cols = [c.name for c in r.manifest.schema.columns] if r.manifest and r.manifest.schema else []
        return [dict(zip(cols, row)) for row in (r.result.data_array or [])] if r.result else []

    return run


def function_statements(target):
    sql = (ROOT / "src/agents/functions.sql").read_text().replace("__S__", target)
    chunks = [c.strip() for c in sql.split("-" * 96)]
    statements = []
    for c in chunks:
        body = "\n".join(line for line in c.splitlines() if not line.startswith("--")).strip().rstrip(";")
        if body:
            statements.append(body)

    rules = (ROOT / "src/agents/rules.py").read_text()
    call = "decide(" + ", ".join(a.split()[0] for a in DECIDE_ARGS.split(", ")) + ")"
    statements.insert(0, (
        f"CREATE OR REPLACE FUNCTION {target}.decide_trip({DECIDE_ARGS})\n"
        "RETURNS STRUCT<choice STRING, reason STRING>\nLANGUAGE PYTHON\n"
        "COMMENT 'Bike, walk or wait rule (FR-15.3): picks wait, walk, bike or none from arrival times "
        "(seconds from now) and the weather. Source: src/agents/rules.py.'\n"
        f"AS $$\n{rules}\nreturn {call}\n$$"
    ))
    return statements


def setup_functions(w, run, target, only=None):
    for name, (host, base_path) in CONNECTIONS.items():
        run(f"CREATE CONNECTION IF NOT EXISTS {name} TYPE HTTP OPTIONS "
            f"(host '{host}', port '443', base_path '{base_path}', bearer_token 'unused')")
        print(f"connection {name}")

    # CREATE FUNCTION checks that the secret exists. Until the real key is stored, a placeholder lets
    # the functions be created; Digitransit then answers 401 and trip_options returns an error row.
    if not any(s.name == SECRET_SCOPE for s in w.secrets.list_scopes()):
        w.secrets.create_scope(SECRET_SCOPE)
    if SECRET_KEY not in [s.key for s in w.secrets.list_secrets(SECRET_SCOPE)]:
        w.secrets.put_secret(SECRET_SCOPE, SECRET_KEY, string_value="missing")
        print(f"WARNING: stored a placeholder for {SECRET_SCOPE}/{SECRET_KEY}; put the real Digitransit key there.")

    for statement in function_statements(target):
        name = statement.split("(")[0].split(".")[-1].strip("` ")
        if only and name not in only:
            continue
        run(statement)
        print(statement.split("(")[0].replace("CREATE OR REPLACE ", "").strip())


def sample_digitransit_body():
    """A planConnection response shaped like Digitransit's, with times relative to now."""
    tz = dt.timezone(dt.timedelta(hours=3))
    now = dt.datetime.now(tz).replace(microsecond=0)

    def at(minutes):
        return (now + dt.timedelta(minutes=minutes)).isoformat()

    return json.dumps({"data": {
        "tram": {"edges": [
            {"node": {"start": at(0), "end": at(9), "legs": [{"mode": "WALK"}]}},
            {"node": {"start": at(1), "end": at(16), "legs": [
                {"mode": "WALK"},
                {"mode": "TRAM", "serviceDate": now.date().isoformat(),
                 "start": {"scheduledTime": at(3), "estimated": {"time": at(7)}},
                 "route": {"shortName": "3"}, "trip": {"directionId": "1",
                 "departureStoptime": {"scheduledDeparture": 66720}}, "from": {"name": "Kaivopuisto"}},
            ]}},
        ]},
        "walk": {"edges": [{"node": {"start": at(0), "end": at(24)}}]},
        "bike": {"edges": [{"node": {"start": at(0), "end": at(11), "legs": [
            {"mode": "WALK"},
            {"mode": "BICYCLE", "from": {"vehicleRentalStation": {"name": "Kaivopuisto",
             "availableVehicles": {"total": 6}}}, "to": {"vehicleRentalStation": {
             "name": "Rautatientori / länsi", "availableSpaces": {"total": 12}}}},
            {"mode": "WALK"},
        ]}}]},
    }})


def check(run, target):
    print("\n-- decide_trip (late tram, dry, bike faster)")
    print(run(f"SELECT {target}.decide_trip(1200, 1500, 600, 0.0, 10, 5.0, 12.0, true) AS d"))
    print("-- find_stop('Kaivopuisto')")
    print(run(f"SELECT stop_id, stop_name, is_tram_stop FROM {target}.find_stop('Kaivopuisto')")[:2])
    print("-- weather_outlook(Rautatientori)")
    for row in run(f"SELECT * FROM {target}.weather_outlook(60.1709, 24.9441)"):
        print(row)
    print("-- parse_trip_options(sample body)")
    body = sample_digitransit_body().replace("'", "''")
    for row in run(f"SELECT * FROM {target}.parse_trip_options('{body}')"):
        print({k: v for k, v in row.items() if v is not None})
    print("-- parse_trip_options(error body)")
    print(run(f"""SELECT option, error FROM {target}.parse_trip_options('{{"errors":[{{"message":"bad"}}]}}')"""))
    print("-- bike_walk_or_wait('Kaivopuisto', 'Rautatientori') (live; needs the Digitransit key)")
    for row in run(f"SELECT * FROM {target}.bike_walk_or_wait('Kaivopuisto', 'Rautatientori')"):
        print({k: v for k, v in row.items() if v is not None})


def setup_agent(w, target):
    genie = next(s for s in w.genie.list_spaces().spaces if s.title == GENIE_TITLE)
    agent = next((a for a in w.supervisor_agents.list_supervisor_agents() if a.display_name == AGENT_NAME), None)
    spec = sa.SupervisorAgent(
        display_name=AGENT_NAME,
        description="Helsinki tram helper: bike, walk or wait advice, live tram Lateness, weather, and "
        "questions about Punctuality history.",
        instructions=INSTRUCTIONS,
    )
    if agent is None:
        agent = w.supervisor_agents.create_supervisor_agent(spec)
        print(f"created {agent.name}, endpoint {agent.endpoint_name}")
    else:
        agent = w.supervisor_agents.update_supervisor_agent(
            agent.name, spec, update_mask=FieldMask(field_mask=["display_name", "description", "instructions"]))
        print(f"updated {agent.name}, endpoint {agent.endpoint_name}")

    def fn(name, description):
        return sa.Tool(tool_type="uc_function", description=description,
                       uc_function=sa.UcFunction(name=f"{target.replace('`', '')}.{name}"))

    tools = {
        "bike_walk_or_wait": fn("bike_walk_or_wait", "Should a rider at a tram Stop wait for the tram, walk "
                                "or take a city bike to another Stop? Returns the advice, the reason and "
                                "the numbers behind it (tram times, measured Lateness, bikes, weather)."),
        "tram_lateness": fn("tram_lateness", "Current Lateness of each Vehicle on a tram or metro Route "
                            "from our live HSL feed, with how fresh each reading is."),
        "weather_outlook": fn("weather_outlook", "FMI weather forecast (rain, chance of rain, wind, gusts, "
                              "temperature) for the next two hours at a latitude/longitude."),
        "find_stop": fn("find_stop", "Find HSL Stops by name, with coordinates."),
        "bunching_now": fn("bunching_now", "Tram Bunching right now: pairs of trams on a Route leaving the "
                           "same Stop far closer together than planned (FR-16)."),
        "punctuality_history": sa.Tool(
            tool_type="genie_space", genie_space=sa.GenieSpace(id=genie.space_id),
            description="SQL analytics on tram Punctuality, departures, Stop Lateness, Coverage and Rider "
            "Reports over the last 30 days."),
    }
    existing = {t.tool_id: t for t in w.supervisor_agents.list_tools(agent.name)}
    for tool_id, tool in tools.items():
        if tool_id in existing:
            w.supervisor_agents.delete_tool(existing[tool_id].name)
        w.supervisor_agents.create_tool(agent.name, tool, tool_id)
        print(f"tool {tool_id}")

    for e in w.supervisor_agents.list_examples(agent.name):
        w.supervisor_agents.delete_example(e.name)
    for question, guidelines in EXAMPLES:
        w.supervisor_agents.create_example(agent.name, sa.Example(question=question, guidelines=guidelines))
    print(f"{len(EXAMPLES)} examples")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", required=True)
    p.add_argument("--catalog", default="my_databricks_workspace")
    p.add_argument("--schema", default="hsl_live_transit")
    p.add_argument("--check", action="store_true", help="run each function once after creating it")
    p.add_argument("--skip-agent", action="store_true", help="functions only, no Supervisor Agent")
    p.add_argument("--only", nargs="+", help="create just these functions, e.g. tram_bunching in the replay schema")
    args = p.parse_args()

    w = WorkspaceClient(profile=args.profile)
    target = f"`{args.catalog}`.`{args.schema}`"
    run = sql_runner(w, args.catalog, args.schema)
    setup_functions(w, run, target, args.only)
    if args.only:
        return
    if args.check:
        check(run, target)
    if not args.skip_agent:
        setup_agent(w, target)


if __name__ == "__main__":
    main()
