# Progress & resume guide

> **Resume here.** Read this file top to bottom, then do the first unchecked item under [Next step](#next-step).
> Update it at the end of every working session: move finished items to the log, rewrite "Next step".

**Last updated:** 2026-10-03 (end of day) · **Current phase:** 5 — Package + publish: everything is built and deployed; open work is the ingestion cost measurement, your checks and the blog review · **Phases 0–4:** ✅ done

---

## Where things live

### Documents (`claude_notes/`; CLAUDE.md, docs/ and scratchpad/ stay at the repo root)

| File | What it is | When to read it |
|---|---|---|
| [REQUIREMENTS.md](./REQUIREMENTS.md) | Goals, requirements (FR/NFR), repo layout, delivery plan, risks | Before starting any phase |
| [CONTEXT.md](./CONTEXT.md) | Glossary: the words to use (Lateness, Journey, Coverage…) and the words to avoid | When naming tables, columns, UI labels |
| [docs/adr/](../docs/adr/) | Decisions and why (0001 MQTT source, 0002 Lateness sign, 0003 Lakebase + warehouse, 0004 metro without lateness) | Before changing anything they cover |
| [docs/findings/](../docs/findings/) | Phase 0 results, Q2 Stop Event Lateness | Before changing ingestion or lateness |
| [scratchpad/](../scratchpad/) | Spike notebooks and ad-hoc analysis (`analysis_phase2.sql`); never deployed | When re-checking a finding |
| [CLAUDE.md](../CLAUDE.md) | Instructions for Claude Code sessions in this folder | Loaded automatically by Claude |

### Databricks workspace

| Item | Value |
|---|---|
| Workspace | `https://adb-7405611495879743.3.azuredatabricks.net` |
| CLI profile | `dev` (`databricks … -p dev`) |
| User | `sumesh.kashyap90@gmail.com` |
| **Catalog** | **`my_databricks_workspace`**: use it for everything; never `main` |
| Project schema | `my_databricks_workspace.hsl_live_transit`, created by the bundle (`resources/schema.yml`) |
| Project pipeline | `hsl_live_transit`, id `55ff44d7-ba9f-4fde-998b-3f284abf99d0`, serverless, continuous: **stop it after every test** |
| Daily job | `hsl_daily_hsl_live_transit` (GTFS load + retention), 09:00 Helsinki, **unpaused**. Raw GTFS in volume `hsl_live_transit.raw/gtfs/` |
| Lakebase | project `hsl-live-transit` (PG 17, 0.5–2 CU, suspends after 5 min idle), branch `production`, endpoint `primary`, database `databricks_postgres`. Synced table `my_databricks_workspace.hsl_live_transit.gold_vehicle_current_online` (Postgres `hsl_live_transit.gold_vehicle_current_online`), continuous; its pipeline `25caa1d7-d217-45d6-ac50-0494a283f44e` is **stopped** |
| App | `hsl-live-transit`, https://hsl-live-transit-7405611495879743.3.azure.databricksapps.com, Streamlit, **stopped**. After a new Lakebase schema or a recreated UC table, rerun `scripts/grant_app_access.py` (Lakebase + UC grants; `--skip-lakebase` for UC only) |
| SQL Warehouse | `hsl-live-transit-analytics`, id `101db38ca6419b45`, serverless 2X-Small, auto-stop 5 min (bundle `resources/warehouse.yml`). Analytics views, health strip and Genie |
| Genie space | `HSL Live Transit`, id `01f1bf6086e110a18fb46a002c7a8b9f` (bundle `resources/genie.yml`). Instructions and example SQL live in the YAML; redeploy to change them |
| Demo jobs | `hsl_demo_session_hsl_live_transit` (`databricks bundle run demo_session -t dev [--params max_minutes=N]`, default 20 min) and `hsl_demo_stop_hsl_live_transit` (`bundle run demo_stop`). Script `src/jobs/demo.py` |
| AI/BI dashboard | `HSL tram Punctuality`, id `01f1bf6a84991dccb4874784e4a8859c`, published (bundle `resources/dashboard.yml`, JSON `src/dashboards/departures.lvdash.json`) |
| App-owned Lakebase | Schemas `hsl_reports` (`rider_reports`, REPLICA IDENTITY FULL) and `hsl_users` (`watched_routes`), owned by the app SP `3870ef78-3ec3-4f2d-b252-73d7f7706b5a`, created by `src/app/init_db.py` at every app start. **Never create or change them as yourself** (gotcha 21) |
| Replay target | `databricks bundle deploy -t replay`: schema `hsl_live_transit_replay`, pipeline `hsl_live_transit_replay` id `86e79dfc-ab0d-4bb2-acb3-3035a6d04aa7` (fixtures fully replayed; stopped). Its daily job is paused. Uses the live `silver_stops`/`silver_routes`. |
| Bundle | `databricks bundle deploy -t dev` (target `dev` uses profile `dev`); files under `/Workspace/Users/sumesh.kashyap90@gmail.com/.bundle/hsl_live_transit/dev/` |
| Spike schema | `my_databricks_workspace.hsl_spike`: volume `raw`, table `spike_bronze_notebook` (`spike_bronze_pipeline` and its pipeline `hsl_phase0_spike_c2` deleted 2026-10-03) |
| Test fixtures | `/Volumes/my_databricks_workspace/hsl_spike/raw/hfp_samples/20260926T182830Z/` (212,662 lines on disk; PHASE0_RESULTS' 212,892 counts 230 messages that never reached a file. 10 min, JSONL) |
| Workspace folder | `/Workspace/Users/sumesh.kashyap90@gmail.com/hsl_live_transit/scratchpad/` (spike notebook, module, pipeline file and analysis notebook; synced from the local `scratchpad/`) |
| Cluster | `Sumesh Kashyap's Cluster`, id `0926-172026-9fhkcdfl`, DBR 17.3, **Standard** access mode |

---

## Decisions so far (one line each)

- Paid workspace; project is blog/community content; trams + metro only.
- Ingestion: custom PySpark Python Data Source subscribing to MQTT (ADR-0001). Gaps accepted and shown.
- Events: `vp` + `arr`/`pde`/`dep`. Lateness = −`dl`, positive = late (ADR-0002).
- Metro on the map in neutral grey, with no lateness, and excluded from punctuality and the heatmap (ADR-0004).
- Pipeline: Lakeflow Declarative Pipelines. Packaging: Databricks Asset Bundle.
- Serving: Lakebase synced table for the live map, SQL Warehouse for analytics (ADR-0003).
- App: Streamlit + pydeck; live map, route punctuality board, stop heatmap, slim health strip.
- Freshness: fresh ≤ 30 s, fading to 5 min, hidden after. On-time window configurable (default −60 s…+180 s), judged on departures.
- Retention: bronze 5 days, silver/gold 30 days. Times in Europe/Helsinki; "today" = Operating Day.
- Catalog `my_databricks_workspace`.

## Gotchas already paid for (don't rediscover them)

1. **paho hangs in the sandbox (R7).** Standard clusters and serverless block paho's loopback wake-up socket. Set `paho.mqtt.client._socketpair_compat` to an AF_UNIX `socket.socketpair()` **inside** the connect function. See `src/hsl_mqtt_source/client.py`.
2. **Python Data Source can't import workspace modules (R8).** Call `pyspark.cloudpickle.register_pickle_by_value(module)` before `spark.dataSource.register(...)`, or install the source as a wheel (the Phase 1 plan). Module-level code doesn't run in the worker.
3. **A stale notebook session** keeps an old data source registration. After a failed stream, *detach and re-attach*; *Run all* is not enough.
4. **Serverless notebooks** only allow `availableNow` triggers; test the endless MQTT stream on the cluster or in a continuous pipeline.
5. **Pipeline progress metrics lag.** Count rows in the table instead of trusting `flow_progress` in the first minutes.
6. **Each tram `vp` arrives 4 times, byte-identical.** About 4 msg/s per tram in bronze, but only about 1 Hz distinct (fixtures: 196,392 msgs, 49,100 distinct). Silver dedup removes them; size bronze and buffers for the 4×.
7. **`mode: development` renames UC schemas** to `dev_<user>_<name>`. The `dev` target doesn't use it; it sets `presets.pipelines_development: true` instead so the schema stays `hsl_live_transit`.
8. **Deploying a continuous pipeline starts it.** Every `bundle deploy` that creates or changes the pipeline may start an update: run `databricks pipelines stop 55ff44d7-ba9f-4fde-998b-3f284abf99d0 -p dev` afterwards.
9. **Pipeline wheel path:** `environment.dependencies: ../dist/*.whl` resolves to the *unpatched* wheel, not the `dynamic_version` one. **Bump `version` in `pyproject.toml` on every source change** (now 0.2.0), and clear `dist/`.
10. **Changing `silver_stops`/`silver_routes` columns kills `gold_departures`** (`DELTA_SCHEMA_CHANGE_SINCE_ANALYSIS` in the stream-static join). Restart the pipeline after any schema change to the GTFS tables. The daily overwrite with the same schema is fine.
11. **`.cache()` / `persist` isn't supported on serverless jobs** (`NOT_SUPPORTED_WITH_SERVERLESS`).
12. **Shared stops:** GTFS `vehicle_type` marks a stop by its main mode, so tram stops shared with buses (e.g. Jokeri at Vermo) are tagged bus. `silver_stops` keeps all stops.
13. **Replay resumes from its offset** (`replay_pos`). To replay again from scratch, `bundle destroy -t replay` and redeploy. A full refresh is blocked on bronze.
14. **`bundle run` of the app can't resolve `${resources.postgres_projects.*.name}`** (deploy can). `resources/app.yml` builds paths from `${var.lakebase_project}` instead.
15. **App logs need OAuth:** `databricks apps logs` fails with the PAT profile (`bad handshake`). Use the app's Logs tab in the UI.
16. **Synced tables work in a normal UC catalog:** no separate Lakebase catalog is needed; the UC schema name becomes the Postgres schema.
17. **The Apps runtime runs Streamlit 1.38**, not the latest: `st.pydeck_chart` has no `height=` there (the app crashed with `unexpected keyword argument 'height'`). Before deploying, run `tests/test_app_smoke.py` with `--with streamlit==1.38.0` (command in the file).
18. **App env from a resource is `value_from`, not `valueFrom`** in the bundle: the camelCase key is silently ignored (validate only warns).
19. **Genie's "right now" needs the feed state spelled out.** With the pipeline stopped, Genie answered "No" to "is tram 4 on time?" until the instructions said: no fresh rows → say Lateness is unknown and give last_seen. Coverage is only mentioned when the instructions require it on every windowed answer.
20. **Altair 5 in the Apps runtime:** reuse an `alt.Scale` object between layers; `x.scale` from an `alt.X` is a property setter and fails validation.
21. **App-owned Postgres schemas must be created by the app SP.** If you create `hsl_reports`/`hsl_users` yourself (running app code locally against production), you own them and the SP gets `permission denied`. Test SQL on a throwaway branch instead (`databricks postgres create-branch … --json '{"spec": {"source_branch": …, "ttl": "7200s"}}'` gives its own endpoint), then delete it.
22. **Lakebase Change Data Feed (CDF) is a workspace preview** named exactly that (workspace user menu → Previews; not in the account console). After switching it on, the API kept answering `Lakebase CDF APIs are not enabled` for a few minutes. The config id must match `[a-z][a-z0-9_]{0,62}` (no hyphens), and empty tables are skipped: `lb_<table>_history` only appears after the first row.
23. **App command with a pre-step:** `sh -c "python init_db.py; exec streamlit run app.py --server.port $DATABRICKS_APP_PORT --server.address 0.0.0.0"`. Use `$VAR`, not `${VAR:-x}`: the bundle treats `${…}` as its own interpolation.
24. **Lakebase `default_endpoint_settings` don't apply to the existing `primary` endpoint.** The bundle's `suspend_timeout_duration: 300s` left `primary` at 24 h, so it billed ~0.107 DBU/h (~$1.60/day) around the clock. Fixed 2026-10-03 with `databricks postgres update-endpoint … spec.suspend_timeout_duration` (command in `resources/lakebase.yml`); a redeploy keeps it. Check with `databricks postgres get-endpoint … | status.suspend_timeout_duration`.

---

## Next step

All of Phases 0–4 and the Phase 5 build are done: demo jobs, AI/BI dashboard, README, Rider Reports + My Routes, Lakebase CDF and the blog draft. **Everything is stopped** (pipelines IDLE, app STOPPED; the warehouse and Lakebase compute suspend themselves; Lakebase confirmed IDLE ~6 min after a demo, with CDF on). Resume with item 1.

- [ ] 1. **Measure the real cost of a demo run.** The blog's ingestion figure (~$8.50/h) is too high: it came from eight short runs where startup dominated. Provisional steady state ≈ 12 DBU/h ≈ **$6/h** (from the 15:50–16:00 UTC record: 1.83 DBU in 10 min), running in `PERFORMANCE_OPTIMIZED` mode. Billing lagged at 18:40 UTC on 2026-10-03, so the three longer runs weren't in it yet: your check (20:17 UTC), the 6-min demo (20:36) and your 20-min demo (21:08). Query them per resource (`system.billing.usage`, `usage_metadata.dlt_pipeline_id`/`app_name`/`warehouse_id`, `product_features.performance_target`) and work out the cost of one 20-min demo and of a steady hour. The blog no longer has a cost section (removed 2026-10-04), so this is for the README / your own reference only.
- [ ] 2. **Optional cost experiment (decide first):** run the ingestion pipeline in serverless *standard* performance mode. For continuous pipelines that needs a continuous job with "Performance optimized" off ([docs](https://learn.microsoft.com/en-us/azure/databricks/ldp/serverless)); the trade-offs are a 4–6 min start and probably worse freshness. Alternative: a small fixed classic cluster. Compare with item 1; it may become a blog section.
- [ ] 3. **Your check of My Routes** (FR-14): pick routes, restart the app (`databricks apps stop/start hsl-live-transit -p dev`) and check they come back. Also try a second Rider Report on the same vehicle within 2 min (should be refused). Your first report already worked end to end.
- [ ] 4. **Your look at the AI/BI dashboard** (unverified: the On-time text-entry filters and the bar sort): `https://adb-7405611495879743.3.azuredatabricks.net/dashboardsv3/01f1bf6a84991dccb4874784e4a8859c/published`.
- [ ] 5. **Your review of the blog draft** in the review doc https://claude.ai/code/artifact/d8d30498-4bed-441a-b3bb-0556e4bb661b. Comment there; the repo copy `blog/hsl-live-transit.md` must stay in sync with it. **Out of sync since 2026-10-04:** the repo copy dropped the cost section and gained per-component "in brief" explanations with official doc links; push it to the review doc (or re-publish) before reviewing. Then add the four `[SCREENSHOT: …]` images (repo link filled in 2026-10-04).
- [ ] 6. Before publishing: the Lateness outliers (route 5T averaging about +10 min, route 2 about −18 min) and the Jokeri (route 15) departures without stop names (e.g. stop 1462402 has no `silver_stops` match).

**Open questions:** none in REQUIREMENTS.md. Unverified by choice: the README's fresh-workspace caveat (synced table and Genie space need gold tables at deploy).

---

## Log

### 2026-09-26 — Planning + Phase 0
- Grilling session: goals, scope, glossary (CONTEXT.md), ADR-0001…0003, REQUIREMENTS.md.
- Phase 0 spike built, uploaded to the workspace, run as a job and as a serverless pipeline. All platform checks pass; metro has no lateness or stop events. Details: docs/findings/PHASE0_RESULTS.md.
- Fixed two sandbox issues along the way (R7, R8).
- Decided: metro on the map in neutral colour (ADR-0004); catalog `my_databricks_workspace`.

### 2026-10-03 — Phase 1 bundle + thin silver/gold
- Decided with the user: tables in `hsl_live_transit` (not `hsl_spike`); a minimal medallion = bronze plus a thin silver and gold; source = HFP MQTT; ship the source as a wheel built by the bundle.
- Bundle: `databricks.yml`, `resources/schema.yml`, `resources/pipeline.yml`; `pyproject.toml` builds `src/hsl_mqtt_source` (core.py is pure and tested, client.py has the R7 fix, source.py is the `hsl_mqtt` DataSource with heartbeats, drop counting and replay mode).
- Pipeline `src/pipeline/{bronze,silver,gold}.py`. Bronze has `pipelines.reset.allowed=false` because MQTT can't replay.
- 8 unit tests pass (`uv run --with pytest --no-project python -m pytest`).
- Live run: about 24k events in the first 50 s; heartbeats every 10 s; silver: 107 trams (1.6 % lateness null), 35 metro (100 % null, ADR-0004), 0 duplicates, 0 expectation drops; gold_vehicle_current and gold_departures filled.
- Stop/restart exit check passed: a second `session_id`; gap 13:36:32 → 13:37:06 UTC visible between the heartbeats; 0 dropped messages. **Pipeline stopped (IDLE).**

### 2026-10-03 (later) — Phase 2: retention, sessions, coverage, GTFS, replay, Q2
- Daily job `hsl_daily` (`resources/jobs.yml`): `gtfs_load` (`src/jobs/gtfs_loader.py` → `silver_routes`: 34 tram/metro routes, with route_type 900 counted as tram; `silver_stops`: 8,267 stops, all modes) and `retention` (`src/jobs/retention.py`: DELETE works on streaming tables from serverless). The pipeline's streaming reads use `skipChangeCommits`.
- Pipeline: `silver_heartbeats`, `silver_ingestion_sessions` (MV), `gold_coverage_minutely` (MV: covered seconds per minute; zero minutes = Data Gaps). `gold_departures` is joined to the GTFS tables. Table names `dim_stops`/`dim_routes` are now `silver_stops`/`silver_routes` (medallion prefixes; REQUIREMENTS.md updated).
- `replay` target (schema `hsl_live_transit_replay`): all 212,662 fixture lines → 59,971 positions, 1,306 Stop Events, 443 departures. **Stop join 99.3 % on replay, 98.6 % live: Phase 2 exit criterion met.** 0 dropped, 0 expectation drops.
- Live: 4 sessions; coverage shows the 13 gap minutes between them.
- Q2 measured: for departures, −`dl` ≈ `tst − ttdep` − 22 s (r = 0.99). Write-up and options in docs/findings/Q2_STOP_EVENT_LATENESS.md. **Decision pending.**
- Wheel 0.2.0 (replay resumes from its offset). **Both pipelines stopped (IDLE).**

### 2026-10-03 (evening) — Q2 decided, cleanup, 5-min run, Phase 3
- **Q2: option 3** (ADR-0005). `timetable_lateness_s` is on `silver_stop_events` and `gold_departures`; `lateness_s` is still −`dl`. "Timetable Lateness" added to CONTEXT.md. On the new run the median of `lateness_s − timetable_lateness_s` for departures was −21 s, matching Q2.
- `gold_departures` falls back to the route short name when GTFS lacks the HFP route id. 5-min run: 99.6 % of departures matched a stop and 99.6 % a route.
- Cleanup: spike notebooks and code moved to `scratchpad/` (also in the workspace; the old `spikes/` folder there is deleted after a local backup), plus the analysis notebook `analysis_phase2.sql`. Findings moved to `docs/findings/`. Removed `pyproject.toml.bak`. Ruff knows `spark`/`dbutils`.
- 5-min live run: 384k bronze events, 115,759 positions from 147 vehicles, 0 dropped.
- Replay expectations: every expectation passed on all rows (59,971 positions, 1,306 Stop Events, 0 failed). **Phase 2 exit criteria met.**
- Phase 3 (all in the bundle, dev target): Lakebase project `hsl-live-transit`, continuous synced table `gold_vehicle_current_online`, and the Streamlit + pydeck app `hsl-live-transit` (`src/app/`). The app has Freshness fading, lateness bands, a grey "lateness not published" band for metro, a tooltip, mode/route filters and 4 s refresh. Postgres GRANTs are in `scripts/grant_app_access.py`. 12 unit tests pass.
- Measured: **NFR-2 met** (map query 20 ms median, 24 ms p95, from outside Azure). **NFR-1 not met:** p95 age in Lakebase about 26 s vs 15 s (stage lag: bronze 4.5 s, silver 9–12 s, gold 25 s, Lakebase ≈ gold).
- App deployed and RUNNING; not checked visually (needs your login). **Everything stopped:** ingestion pipeline, replay pipeline, sync pipeline and app compute. Lakebase compute suspends after 5 min idle.
- Deleted the Phase 0 pipeline `hsl_phase0_spike_c2` together with its table `hsl_spike.spike_bronze_pipeline` (asked for by the user).
- App crashed on `st.pydeck_chart(height=…)` (Streamlit 1.38 in the Apps runtime). Fixed, redeployed, and added the headless smoke test `tests/test_app_smoke.py` (13 tests pass with Streamlit 1.38).

### 2026-10-03 (night) — NFR-1 decided
- User chose option (c): NFR-1 relaxed from p95 ≤ 15 s to ≤ 30 s in REQUIREMENTS.md. The measured ~26 s passes. No code change; the pipeline topology stays as it is.
- Confirmed Phase 4 builds native Streamlit views on the SQL warehouse, not an embedded AI/BI dashboard (FR-8.2 slider, FR-8.3 coverage warnings, FR-10.2 gap shading).
- Added FR-11 (requested by the user): a separate AI/BI dashboard on `gold_departures`, in the bundle, delivered in Phase 5. Requirement only, nothing built.
- Added FR-12 (requested by the user): a Genie-backed chat tab in the app, built in Phase 4 after FR-8–10. Non-goals, environment table, NFR-5, NFR-6 and the Phase 4 exit criteria were updated. No ADR, because it is easy to remove and doesn't change ADR-0003. Requirement only, nothing built.

### 2026-10-03 (night, later) — Phase 3 closed, Phase 4 built
- You checked the live map visually: **Phase 3 done.**
- App (`src/app/`) now has a health strip (FR-10.1) and four tabs: Live map (unchanged, Lakebase), Punctuality (FR-8: Last hour / Today, On-time slider, per Route × direction Punctuality/avg/p90, Coverage warning below 90 %, departures chart with grey Data Gap bands for FR-10.2), Stop Lateness (FR-9: stops coloured by average Lateness, sized by departures, route filter, Coverage) and Ask (FR-12: Genie chat, shows the SQL and table, stale-data warning when no session is live, errors don't break the app).
- New modules: `analytics.py` (pure SQL and window helpers), `warehouse.py` (SQL connector, one connection per query), `genie.py` (Conversation API). "Today" = Operating Day, which starts at 04:30 Helsinki.
- Bundle: `resources/warehouse.yml` (2X-Small serverless, NFR-3) and `resources/genie.yml` (Genie space: 5 tables, CONTEXT.md vocabulary, 4 example SQLs). `app.yml` gained `sql_warehouse` and `genie_space` resources. `scripts/grant_app_access.py` now also grants UC SELECT on the six tables the app reads; NFR-6 now lists `silver_heartbeats` (health strip).
- Measured on real data: every analytics query took 0.5–2.6 s on the warehouse (**NFR-3 met**). Genie answered tram 4 (feed stopped → "unknown", last seen given), worst routes today (with Coverage 2.7 %) and metro M1 ("Lateness is not published") correctly after two instruction fixes.
- Tests: 27 pass (`uv run --no-project --with pytest --with streamlit==1.38.0 --with pydeck --with pandas --with databricks-sdk python -m pytest tests/`). The smoke test now uses fake warehouse and Genie backends, and covers the slider re-query, the coverage warning, the stale-pipeline warning and Genie errors.
- `pyproject.toml`: ruff `line-length = 110` to match the existing code.
- Deployed; app started successfully, then **stopped**. Pipelines IDLE. Warehouse auto-stops.

### 2026-10-03 (late) — Phase 4 signed off, Phase 5
- You checked Phase 4 visually: **Phase 4 done.**
- Decided with you: `demo_session` starts the ingestion pipeline, the Lakebase sync and the app, default 20 min (**Q1 closed**, NFR-4 updated). **No `prod` target**: readers edit host, catalog and schema themselves (NFR-5 updated).
- `resources/demo.yml` + `src/jobs/demo.py`: `demo_session` (start → hold → stop, stop runs `ALL_DONE`; start loads GTFS first if `silver_stops` is missing; hold ends early if the ingestion pipeline stops) and `demo_stop`. Tested: `demo_stop` stopped what was left running from your check; a 6-minute `demo_session` started everything from cold and stopped it all on time.
- FR-11 AI/BI dashboard `HSL tram Punctuality` (`resources/dashboard.yml`, `src/dashboards/departures.lvdash.json`): Operating Day, Route and On-time parameter filters; Punctuality, departures, average Lateness and Coverage KPIs; Punctuality by Route; departures per hour (on-time vs not); stop Lateness map. Dataset SQL tested on the warehouse; the parameters change the results. Deployed and published, not yet viewed.
- `README.md` for readers: architecture, setup steps for another workspace, demo commands, tests, gotchas, HSL CC BY 4.0 attribution (R6).
- **Everything stopped:** both pipelines IDLE, app STOPPED.

### 2026-10-03 (very late) — FR-13 Rider Reports, FR-14 My Routes
- Decided with you: Lakebase also as the app's write store, with Rider Reports and My Routes. Added FR-13, FR-14, goal 6, a non-goal update, NFR-6 note, ADR-0006 and the glossary terms Rider Report and My Routes. CLAUDE.md: next ADR is 0007.
- `src/app/store.py`: schemas `hsl_reports.rider_reports` (insert-only, reporter = SHA-256 of the email, snapshot of measured Lateness, position and last seen taken from the synced table inside the INSERT, category CHECK, 280-char note) and `hsl_users.watched_routes`. Rate limits (1 per vehicle per 2 min, 20 per hour) are checked in the same transaction under an advisory lock. `db.py` gained a lock and `transaction()`, because the connection is shared across sessions.
- App: purple report rings on the map (tooltip HTML-escaped; only position and tooltip go to the browser), report form + latest-reports panel in its own fragment, My Routes picker in the sidebar (saved on change), "Only my Routes" toggle, My Routes strip above the map. Anonymous viewers can't write. Viewer = `X-Forwarded-Email` (`HSL_DEV_VIEWER_EMAIL` for local runs).
- `src/app/init_db.py` runs before Streamlit (app command `sh -c …`), so the SP creates and owns the schemas at deploy time. Verified on production: both schemas owned by the SP, `rider_reports` REPLICA IDENTITY FULL.
- Store SQL tested for real on a throwaway Lakebase branch (`store-test`, deleted): idempotent schema, insert with snapshot, rate limit, unknown vehicle, category check, watchlist replace.
- `scripts/setup_lakebase_cdf.py` written; **blocked**: `Lakebase CDF APIs are not enabled`. `grant_app_access.py` also grants `lb_rider_reports_history` once it exists.
- Tests: 37 pass (new `tests/test_store.py`; smoke tests for reporting, rate limit, escaping, My Routes saving, anonymous viewer).
- App deployed and started successfully, then **stopped**. Pipelines IDLE.
- Later the same night: you enabled the **Lakebase Change Data Feed** preview (my earlier name "Lakehouse Sync" was outdated; renamed everywhere, script now `scripts/setup_lakebase_cdf.py`). The API needed a few minutes after enabling, and the config id had to be `hsl_reports` (no hyphens). CDF config created; the history table waits for the first report.
- Cost check from `system.billing.usage` (list prices): one demo hour ≈ $14–15 (ingestion pipeline ~16–18 DBU/h × $0.50 ≈ $8.5, warehouse 2X-Small ≈ $3.6 while the app is open, sync ≈ $1.35, app ≈ $0.50), plus ≈ $1.5/day fixed (predictive optimization, daily job). Found the Lakebase `primary` endpoint at a 24 h suspend timeout (gotcha 24) and set it to 300 s.
- First Rider Report verified end to end through Lakebase CDF; Genie space extended with `lb_rider_reports_history` (reporter_id excluded). Blog draft written: `blog/hsl-live-transit.md`.
- Verified: with Lakebase CDF on, the `primary` endpoint went IDLE at 21:34, ~6 min after the demo stopped at 21:28 (the 5-min suspend works; CDF doesn't keep it awake). Blog draft published as a review doc (Claude Docs).
- End of day: you questioned the blog's ingestion cost. Re-check from billing: ~$8.50/h was inflated by startup in short runs; provisional steady state ≈ $6/h in `PERFORMANCE_OPTIMIZED` mode. Blog unchanged on purpose until the evening runs appear in billing (Next step 1).

### 2026-10-04 — Blog restructure, planning files moved

- Blog: removed the cost section, the cost table and the Lakebase suspend cost lesson. Added short "in brief" explanations with official doc links (docs.databricks.com, AWS paths with a cloud selector) for bundles, Python Data Sources, Lakeflow pipelines (streaming tables, MVs, expectations, AUTO CDC), Lakebase (synced tables, branches, scale to zero), SQL warehouses, AI/BI dashboards, Genie + Conversation API, Databricks Apps (service principal, resources, auth), Lakebase CDF, Real-Time Mode and Lakeflow Jobs. All links checked to return 200.
- Moved PROGRESS.md, REQUIREMENTS.md and CONTEXT.md into `claude_notes/`. Updated links in CLAUDE.md, README.md and the moved files (repo layout in REQUIREMENTS §6 too). Code comments that say "CONTEXT.md" were left as they are.
