# Agent tools are Unity Catalog functions that call outside APIs with http_request; an Agent Bricks Supervisor uses them

> The agent-host part (Supervisor Agent) is superseded by [ADR-0008](0008-agent-loop-in-the-app.md): the Supervisor Agent isn't available on our workspace, so the app runs the loop. The tool design below still holds.

The Bike, walk or wait agent (FR-15) needs live data from outside the lakehouse: HSL's journey planner and city bikes (Digitransit) and the FMI weather forecast. We put every tool in Unity Catalog as a function (`find_stop`, `weather_outlook`, `tram_lateness`, `trip_options`, `bike_walk_or_wait`, `decide_trip`) and let an Agent Bricks Supervisor Agent call them, with the FR-12 Genie space as another tool. The SQL functions reach the APIs through UC HTTP connections (`hsl_fmi`, `hsl_digitransit`) and `http_request`. The Digitransit key comes from secret scope `hsl_live_transit` through `secret()` inside the function. The choice of wait, walk or bike is made by `decide_trip`, a Python UC function whose source is `src/agents/rules.py`, which is also unit-tested locally. The model only calls tools and explains the result.

## Considered Options

- **A tool-calling loop inside the Streamlit app** on a Foundation Model API endpoint, with the tools as plain Python. It's simpler to test and deploy, but the tools are hidden in the app, nobody else can reuse them, and the blog shows less of the platform.
- **Python UC functions doing the HTTP calls.** UC Python functions run in a sandbox without network access, so this isn't possible.
- **The UC connection proxy endpoint** (`/api/2.0/unity-catalog/connections/<name>/proxy`), which Databricks recommends over `http_request` for new code. A UC function can't call it, so it doesn't fit tools that live in Unity Catalog.

## Consequences

- `http_request` is marked deprecated. If it is removed, the HTTP tools move to an app or model-serving endpoint calling the proxy, and this ADR gets superseded.
- Digitransit takes its key as a header or URL parameter, not a bearer token. The connection holds a dummy bearer token, and the function adds the key as a parameter from the secret scope. Anyone with EXECUTE on `trip_options` uses the key without being able to read it.
- `CREATE FUNCTION` fails if the secret doesn't exist yet, so the setup script stores a placeholder until the real key is in place.
- Connections, functions and the Supervisor Agent can't be declared in the bundle. `scripts/setup_agents.py` creates them (NFR-5 exception, like the Lakebase grants).
- The Supervisor Agent is a beta feature and may not be enabled on a workspace. On 2026-10-08 it wasn't on ours ("Supervisor Agent is not available"). The functions work without it, so the in-app loop above is the fallback.
- Every tool call runs on the analytics SQL Warehouse, so asking the agent starts the warehouse (NFR-4: it auto-stops after 5 min).
