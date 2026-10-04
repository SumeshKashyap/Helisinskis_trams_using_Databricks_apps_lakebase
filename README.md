# HSL Live Transit

A near-real-time Databricks App for Helsinki trams and metro, built end to end on Databricks:

```
HSL HFP (MQTT) ──► custom PySpark streaming source ──► Lakeflow pipeline: bronze → silver → gold
                                                                   │
                         ┌─────────────────────────────────────────┼──────────────────────────┐
                         ▼                                         ▼                          ▼
            Lakebase synced table                     Serverless SQL Warehouse       AI/BI dashboard
                         │                                         │
                         ▼                                         ▼
                  Live map (4 s refresh)          Punctuality · Stop Lateness · Health strip · Ask (Genie)
                         └──────────────── Streamlit Databricks App ────────────────┘
```

- **Live map:** every tram and metro vehicle, coloured by Lateness, fading as its last Position Event gets older. It reads Lakebase, because polling every few seconds needs millisecond lookups ([ADR-0003](docs/adr/0003-dual-serving-path-lakebase-and-warehouse.md)).
- **Punctuality, Stop Lateness, health strip:** read the gold Delta tables through the SQL Warehouse. Every number shows its **Coverage**, so missing data is never mistaken for missing trams.
- **Ask:** a Genie space answers questions like "Is tram 4 on time right now?" and shows the SQL it ran.
- **AI/BI dashboard:** the same departures data with no app code.
- **Rider Reports and My Routes:** signed-in viewers report a problem with a vehicle and save the routes they watch. Both are written to Lakebase tables the app owns, the Postgres side of Lakebase rather than a synced copy ([ADR-0006](docs/adr/0006-lakebase-as-app-write-store.md)). Reports are meant to flow back to Unity Catalog through Lakebase Change Data Feed.

Words like Lateness, Punctuality, Coverage and Data Gap have exact meanings: see [CONTEXT.md](claude_notes/CONTEXT.md). Requirements are in [REQUIREMENTS.md](claude_notes/REQUIREMENTS.md), decisions in [docs/adr/](docs/adr/).

## What you need

- A Databricks workspace with Unity Catalog, serverless compute, Lakebase and Databricks Apps. Genie needs Databricks Assistant enabled.
- Outbound access from serverless compute to `mqtt.hsl.fi` on port 8883 (or 443 with `transport: wss`).
- [Databricks CLI](https://docs.databricks.com/dev-tools/cli/) v1.0+ with a profile for the workspace, and [uv](https://docs.astral.sh/uv/) to build the wheel and run the scripts.

## Set it up for your workspace

1. **Point the bundle at your workspace.** In `databricks.yml`, change `targets.dev.workspace.host` and `profile`, and the variables you want different:
   - `catalog` (here `my_databricks_workspace`): an existing catalog you can create schemas in.
   - `schema` (here `hsl_live_transit`): created by the bundle.
   - `lakebase_project`: the Lakebase project id; it must be unique in the workspace.

   App and job names also live in `resources/*.yml`; change them if they clash with something you have.
2. **Deploy:** `databricks bundle deploy -t dev`. This creates the schema, pipeline, jobs, Lakebase project, synced table, warehouse, Genie space, dashboard and app.
3. **Load the reference data once:** `databricks bundle run hsl_daily -t dev` (stop and route names from HSL's GTFS). After that it runs daily at 09:00 Helsinki.
4. **Grant the app read access:** `uv run --with "psycopg[binary]" --with databricks-sdk scripts/grant_app_access.py --profile <your-profile>`. The bundle can't declare these grants: SELECT on the synced Postgres schema, and UC SELECT on the six tables the app reads.

5. **Optional: send Rider Reports back to the lakehouse.** After the app has started once (it creates its Postgres schemas itself on start), run `uv run --with databricks-sdk scripts/setup_lakebase_cdf.py --profile <your-profile>`, then rerun step 4 so the app can read `lb_rider_reports_history`. Lakebase Change Data Feed is in Public Preview: a workspace admin enables **Lakebase Change Data Feed** on the workspace Previews page (allow a few minutes before the API accepts calls). The history table appears after the first report.

> **Fresh-workspace caveat:** this project was built in one workspace where the gold tables already existed before the synced table and the Genie space were added. On a brand-new schema, step 2 may fail on the synced table or the Genie space, because `gold_vehicle_current` doesn't exist yet. If it does, comment out `resources/lakebase.yml`, `resources/genie.yml` and the matching app resources, deploy, run the pipeline for a minute (`databricks bundle run hsl_live_transit -t dev`, then stop it), uncomment and deploy again.

## Run a demo

```bash
databricks bundle run demo_session -t dev                        # 20 minutes by default
databricks bundle run demo_session -t dev --params max_minutes=45
databricks bundle run demo_stop -t dev                           # end a demo early
```

`demo_session` starts the ingestion pipeline, the Lakebase sync and the app, waits, then stops all three, even if something failed on the way. The first vehicles appear on the map a few minutes after the start, once the pipeline is up. The warehouse and Lakebase compute stop by themselves when idle.

Nothing runs between demos: the pipeline is continuous only while a demo runs. If you start parts by hand, stop them with `demo_stop`.

## Repository

```
databricks.yml        bundle root: variables and the dev / replay targets
resources/            pipeline, jobs (daily GTFS, demo), Lakebase, warehouse, Genie, dashboard, app
src/hsl_mqtt_source/  the custom MQTT streaming source, shipped as a wheel
src/pipeline/         bronze.py, silver.py, gold.py (Lakeflow Declarative Pipeline)
src/jobs/             gtfs_loader.py, retention.py, demo.py
src/app/              Streamlit app: app.py, db.py (Lakebase), store.py (Rider Reports, My Routes), init_db.py,
                      warehouse.py, analytics.py, genie.py
src/dashboards/       departures.lvdash.json
scripts/              grant_app_access.py, setup_lakebase_cdf.py
tests/                unit tests, app smoke test, recorded HFP payloads
docs/                 ADRs and findings
```

Run the tests with the Streamlit version of the Apps runtime:

```bash
uv run --no-project --with pytest --with streamlit==1.38.0 --with pydeck --with pandas --with databricks-sdk python -m pytest tests/
```

The `replay` target (`databricks bundle deploy -t replay`) feeds recorded payloads through the same pipeline into its own schema, so you can work without the live feed.

## Things that bit us

The full list, with workarounds, is in [PROGRESS.md](claude_notes/PROGRESS.md#gotchas-already-paid-for-dont-rediscover-them). The ones you'll most likely meet:

- paho-mqtt hangs inside the Databricks sandbox until its loopback wake-up socket is replaced (`src/hsl_mqtt_source/client.py`).
- A Python Data Source can't import workspace files from the worker, so it ships as a wheel.
- Each tram position arrives four times on HFP; silver deduplicates.
- The Apps runtime runs Streamlit 1.38, so newer Streamlit arguments crash the app.
- Let the app create its own Postgres schemas (here at start, in `init_db.py`). If you create them first as yourself, you own them and the app's service principal is locked out.

## Data and licence

Vehicle positions and timetables: [Helsinki Region Transport (HSL)](https://www.hsl.fi/en/hsl/open-data), licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), via [Digitransit](https://digitransit.fi/en/developers/).
