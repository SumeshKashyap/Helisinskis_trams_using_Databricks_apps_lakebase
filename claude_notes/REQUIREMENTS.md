# HSL Live Transit — Requirements & Plan

A Databricks App showing Helsinki trams and metro in near real time — a live map plus punctuality analytics — built end-to-end on Databricks and written up as blog / community content.

- Vocabulary: see [CONTEXT.md](./CONTEXT.md). Terms in **bold** below are defined there.
- Decisions: see [docs/adr/](../docs/adr/).
- Where we are and what's next: see [PROGRESS.md](./PROGRESS.md).

---

## 1. Goals and non-goals

### Goals
1. Demonstrate a complete streaming lakehouse: MQTT → bronze → silver → gold → Databricks App.
2. Show a live map of tram and metro **Vehicles** with **Freshness**-based fading. Trams are coloured by **Lateness**; metro is drawn in a neutral colour because HSL publishes no metro lateness (ADR-0004).
3. Show **Punctuality** per tram **Route** and a stop-level lateness heatmap (trams only), with a viewer-adjustable **On-time** window.
4. Be honest about missing data: every aggregate shows its **Coverage**; **Data Gaps** are visible.
5. Be reproducible by a blog reader with one `databricks bundle deploy`.
6. Show Lakebase as an app's transactional store too: viewers write Rider Reports and My Routes, and reports flow back into the lakehouse (ADR-0006).
7. Show agents that act on the live data: a tool-using agent that combines our Lateness with outside data (city bikes, weather) to advise a rider (FR-15), and one that finds **Bunching** in our own Stop Events (FR-16).

### Non-goals (v1)
- Buses, trains, ferries (the MQTT topic filter makes these a config change later, not a redesign).
- Always-on 24/7 operation — the pipeline runs on demand.
- Delay prediction / ML models.
- Metro lateness or punctuality: HSL publishes neither (ADR-0004).
- Computing position lateness ourselves from the timetable (we trust HSL's `dl`, see ADR-0002; stop-event lateness is open question Q2).
- Journey planning. Rider-facing features are limited to FR-13 (Rider Reports), FR-14 (My Routes), which demo Lakebase as a write store (ADR-0006), and the FR-15/FR-16 advice, which compares a few options for one trip rather than planning routes; the FR-12 chat tab is a "question the lakehouse" demo, not a rider service.
- Moderation workflows for Rider Reports beyond rate limits and length limits.

---

## 2. Environment and constraints

| Item | Decision |
|---|---|
| Workspace | Azure Databricks `https://adb-7405611495879743.3.azuredatabricks.net` (CLI profile `dev`), Unity Catalog enabled |
| **Catalog** | **`my_databricks_workspace`**: all schemas, tables, volumes and synced tables go here. Do not use `main`. |
| Schemas | `hsl_live_transit` for the project (created in Phase 1); `hsl_spike` holds Phase 0 artefacts only |
| Pipeline | Lakeflow Declarative Pipelines, continuous mode while a demo runs |
| Ingestion | Custom PySpark Python Data Source (streaming reader) subscribing to MQTT — ADR-0001 |
| Live serving | Lakebase synced table — ADR-0003 |
| App writes | Lakebase tables owned by the app (Rider Reports, My Routes); reports go back to UC via Lakebase Change Data Feed — ADR-0006 |
| Analytics serving | Serverless SQL Warehouse |
| Chat | Genie space over gold and silver tables (FR-12) |
| Agents | Tools as Unity Catalog functions (outside APIs through UC HTTP connections), ADR-0007; a tool-calling loop in the app on a Foundation Model API endpoint (`databricks-gpt-oss-120b`), ADR-0008 |
| App | Databricks App — Streamlit + pydeck |
| Packaging | Databricks Asset Bundle (`databricks.yml`) |
| Display timezone | Europe/Helsinki; "today" = current **Operating Day** |

---

## 3. Data sources

### 3.1 HSL High-Frequency Positioning (HFP) — real time
- Broker: `mqtt.hsl.fi`, port 8883 (MQTT over TLS, default); fallback `wss://mqtt.hsl.fi:443` (MQTT over WebSockets). Both verified reachable from the workspace in Phase 0.
- Topic pattern: `/hfp/v2/journey/ongoing/<event_type>/<mode>/#`
- Subscriptions (8 topics): event types `vp`, `arr`, `pde`, `dep` × modes `tram`, `metro`. The topic list is configuration.
- Observed volume (Phase 0, Saturday evening): tram `vp` about 326 msg/s from 81 vehicles (about 4 msg/s per vehicle); tram stop events about 0.7 msg/s each type; metro `vp` about 25 msg/s. Metro sends **no** `arr`/`pde`/`dep` and its `dl` is always null (ADR-0004).
- Payload fields used: `desi` (route), `dir`, `oper`, `veh`, `tst`, `tsi`, `lat`, `long`, `spd`, `hdg`, `dl`, `oday`, `start`, `route`, `stop`, `loc`, `drst`, `occu`.
- Docs: https://digitransit.fi/en/developers/apis/5-realtime-api/vehicle-positions/high-frequency-positioning/

### 3.2 HSL static GTFS — daily batch
- `hsl.zip` from HSL's GTFS publication (URL to confirm in Phase 0; https://infopalvelut.storage.hsldev.com/gtfs/hsl.zip at the time of writing).
- Used for: stop names and coordinates (`stops.txt`), route names and modes (`routes.txt`).

### 3.3 HSL Digitransit Routing API — on request (FR-15)
- GraphQL, `https://api.digitransit.fi/routing/v2/hsl/gtfs/v1`; needs a free subscription key (header `digitransit-subscription-key`).
- Used for (one `planConnection` request with three aliases): the earliest tram trip with HSL's real-time departure (Q3), walking, and city bike rental with the pick-up station's bikes and the drop-off station's free docks.
- City bikes run from April to October only (R10).
- Docs: https://digitransit.fi/en/developers/apis/1-routing-api/

### 3.4 Finnish Meteorological Institute (FMI) open data — on request (FR-15)
- WFS, `https://opendata.fmi.fi/wfs`, no key. Stored query `fmi::forecast::edited::weather::scandinavia::point::simple`, hourly, parameters `Precipitation1h`, `PoP` (chance of precipitation), `WindSpeedMS`, `HourlyMaximumGust`, `Temperature` (Q4). `WindGust` is always NaN in this forecast.
- Licence CC BY 4.0: attribute FMI in the app and blog.

---

## 4. Functional requirements

### FR-1 Ingestion (custom MQTT streaming source)
- FR-1.1 A Python Data Source named `hsl_mqtt` registers with Spark and exposes a streaming reader.
- FR-1.2 The reader holds one MQTT client on the driver, buffers messages between micro-batches, and returns them as rows: `topic`, `payload` (raw JSON string), `received_at`, `session_id`.
- FR-1.3 Each reader start generates a new `session_id` (one **Ingestion Session**).
- FR-1.4 The reader emits a heartbeat row every 10 s even when no messages arrive, so "subscribed but no traffic" (e.g. at night) is distinguishable from a **Data Gap**.
- FR-1.5 Topics, broker host/port/transport and buffer limits are passed as reader options.
- FR-1.6 If the buffer exceeds its limit, the oldest messages are dropped and the drop is counted and emitted, never silent.

### FR-2 Bronze
- FR-2.1 `bronze_hfp_events`: append-only, raw payload as received plus ingestion metadata. No parsing.
- FR-2.2 Retention: 5 days.

### FR-3 Silver
- FR-3.1 `silver_position_events` (from `vp`) and `silver_stop_events` (from `arr`, `pde`, `dep`).
- FR-3.2 Parse and type every field. Convert `tst` to timestamp. Derive `operating_day` from `oday`. Derive `vehicle_id = oper || '/' || veh` and a `journey_id` from (route, dir, oday, start).
- FR-3.3 `lateness_s = -dl` (ADR-0002). `dl` is not carried forward. `lateness_s` is nullable: always null for metro, occasionally null for trams (about 1 % of `vp`).
- FR-3.4 Deduplicate on (`vehicle_id`, `event_type`, `tst`) within a watermark.
- FR-3.5 Expectations (data quality checks): drop rows with null `tst`/`vehicle_id`; drop positions outside the Helsinki region bounding box; warn (keep) on lateness beyond ±1 h.
- FR-3.6 `silver_ingestion_sessions`: one row per session with start, last heartbeat, events received and drops. Built from `silver_heartbeats` (one row per heartbeat), which also feeds FR-4.3 and FR-10.
- FR-3.7 Retention: 30 days.

### FR-4 Gold
- FR-4.1 `gold_vehicle_current`: latest Position Event per vehicle (AUTO CDC / SCD type 1 keyed by `vehicle_id`), with route, mode, lat/long, heading, lateness and last-seen time. Change Data Feed enabled for Lakebase sync.
- FR-4.2 `gold_departures`: one row per departure Stop Event, joined to `silver_stops` and `silver_routes`, with `lateness_s`, `operating_day` and Helsinki local time. Punctuality is **computed at query time** from this table because the On-time window is chosen by the viewer.
- FR-4.3 `gold_coverage_minutely`: for each minute, whether it was covered by an ingestion session (from heartbeats). Coverage for any window is derived from this.
- FR-4.4 Retention: 30 days.

### FR-5 Reference data (GTFS job)
- FR-5.1 A daily Lakeflow Job downloads GTFS and overwrites `silver_stops` and `silver_routes` (routes: tram and metro only; stops: all, because HSL tags shared tram/bus stops as bus; GTFS route_type 900, the Jokeri light rail, counts as tram because HFP reports it as tram).
- FR-5.2 A failed download keeps the previous day's tables. It never truncates them.

### FR-6 Lakebase serving
- FR-6.1 A Lakebase database instance with a synced table from `gold_vehicle_current` (continuous sync mode).
- FR-6.2 The app reads the live map only from Lakebase, never from the warehouse.

### FR-7 App — Live map
- FR-7.1 pydeck map centred on Helsinki showing each tram and metro vehicle, refreshed every 3–5 s (`st.fragment(run_every=…)`).
- FR-7.2 Colour trams by Lateness band (early / on-time / late / very late). Vehicles with unknown Lateness (all metro, and trams whose last event had none) are drawn in a neutral grey; the legend says "lateness not published".
- FR-7.3 **Freshness**: fully opaque at ≤ 30 s since last Position Event, opacity fading linearly from 30 s to 5 min, hidden after 5 min. Computed in the app at render time.
- FR-7.4 Hover tooltip: route, direction, vehicle id, lateness (or "not published"), last seen (Helsinki time).
- FR-7.5 Filters: mode, route.

### FR-8 App — Route punctuality board
- FR-8.1 Table of tram Routes × direction: Punctuality %, average and p90 lateness, number of departures. Windows: last hour and current Operating Day.
- FR-8.2 On-time window slider, default −60 s … +180 s; changing it re-queries.
- FR-8.3 Every row or window shows its Coverage; shows a warning when coverage is below 90 %.

### FR-9 App — Stop-level lateness heatmap
- FR-9.1 Map of tram Stops (from `silver_stops`) weighted by average departure lateness over the selected window.
- FR-9.2 Filter by route; tooltip shows stop name, departures and average lateness.
- FR-9.3 Shows the window's Coverage.

### FR-10 App — Health strip
- FR-10.1 A slim status bar: current Ingestion Session state (live / stopped), events/sec over the last minute, time since the last event, and the most recent Data Gap (start, duration).
- FR-10.2 Event-time charts shade Data Gap periods.

### FR-11 AI/BI dashboard on departures (blog extra)
- FR-11.1 A separate AI/BI (Lakeview) dashboard, outside the app, built on `gold_departures` (with `gold_coverage_minutely` for Coverage). It shows the same data as FR-8 with no app code; it doesn't replace FR-8–FR-10.
- FR-11.2 Tram Routes only (ADR-0004). Punctuality per Route and direction, average lateness, and departures over time per Operating Day.
- FR-11.3 On-time window bounds are dashboard parameters (default −60 s … +180 s), so Punctuality is still computed at query time (FR-4.2).
- FR-11.4 Shows the selected window's Coverage.
- FR-11.5 Declared in the bundle and runs on the same serverless SQL Warehouse (NFR-3, NFR-5).

### FR-12 App — Ask (Genie chat tab)
- FR-12.1 A chat tab in the app (`st.chat_input`) that sends questions to a Genie space through the Genie Conversation API, using the app's service principal. Questions like "Is tram 4 on time right now?" or "Which tram routes were most late today?".
- FR-12.2 The Genie space covers `gold_vehicle_current` (Delta, not the Lakebase copy), `gold_departures`, `silver_routes`, `silver_stops` and `gold_coverage_minutely`, and runs on the analytics SQL Warehouse. FR-6.2 still holds: only the map reads Lakebase.
- FR-12.3 Genie instructions use the CONTEXT.md vocabulary and state: Lateness is −`dl` and positive means late (ADR-0002); "on time" uses the default On-time window (−60 s … +180 s) and the answer names the window used; metro lateness is "not published" (ADR-0004), never treated as on time; "tram 4" means the Route short name. Example SQL queries cover current status per Route, Punctuality per Route today, and the latest stops.
- FR-12.4 Every answer shows the result table and the generated SQL (expandable), plus the `last_seen` time for current-status answers so stale data is visible (Freshness).
- FR-12.5 When no Ingestion Session is live, the tab shows a "pipeline stopped, answers may be stale" notice (same source as FR-10.1).
- FR-12.6 Genie errors and timeouts show a readable message; the rest of the app keeps working.
- FR-12.7 The Genie space is declared in the bundle if the bundle supports it; otherwise a script in `scripts/` creates it from a config file kept in the repo (NFR-5).

### FR-13 App — Rider Reports (Lakebase write path)
- FR-13.1 A signed-in viewer reports a problem with a tram or metro Vehicle that is on the map: pick Route, then Vehicle, then a category (late, early, crowded, skipped stop, breakdown, other) and an optional note (≤ 280 characters).
- FR-13.2 Each report is one INSERT into Lakebase table `hsl_reports.rider_reports`, owned by the app's service principal. It stores a snapshot of what we measured at that moment: the Vehicle's Lateness (null for metro), position and last-seen time from `gold_vehicle_current_online`. Comparing reported with measured needs no join later.
- FR-13.3 The reporter is stored as a SHA-256 hash of the viewer's email (`reporter_id`), never the email itself.
- FR-13.4 Rate limits, checked in the same transaction as the INSERT: at most 1 report per viewer and Vehicle per 2 minutes, and 20 per viewer per hour.
- FR-13.5 The live map shows reports from the last 30 minutes as a separate layer at the reported position, with a tooltip (category, note, age, measured Lateness then). A "Rider Reports" panel lists the latest ones.
- FR-13.6 Lakebase Change Data Feed (Lakebase → Unity Catalog CDC) copies schema `hsl_reports` into `${catalog}.${schema}` as `lb_rider_reports_history`, so the warehouse, Genie and the AI/BI dashboard can join reports with gold tables. Set up once by `scripts/setup_lakebase_cdf.py` (the bundle can't declare it).
- FR-13.7 Without a signed-in viewer (no `X-Forwarded-Email` header), reporting is disabled with an explanation.

### FR-14 App — My Routes (per-viewer state in Lakebase)
- FR-14.1 A signed-in viewer picks the Routes they care about; the choice is saved in Lakebase table `hsl_users.watched_routes` (viewer email, route, added time) and comes back on their next visit, on any device.
- FR-14.2 A "My Routes" strip at the top of the Live map tab (refreshed with the map) shows, per watched Route: Vehicles on the map now, the worst current Lateness, and Rider Reports in the last 30 minutes. Metro shows "Lateness not published" (ADR-0004).
- FR-14.3 A live-map toggle "Only my Routes" filters the map to the watched Routes.
- FR-14.4 `hsl_users` is never synced to the lakehouse: it holds emails and has no analytical use.

### FR-15 Agent — Bike, walk or wait
- FR-15.1 A viewer gives a tram Stop they are at and a destination Stop. The answer is one of **wait**, **walk** or **bike**, the reason, and the numbers behind it. In the app's "Bike, walk or wait" tab: a form that calls `bike_walk_or_wait` directly, and an agent chat that uses the same tools (ADR-0008).
- FR-15.2 Tools (each one a separate, testable function the agent calls):
  - `find_stop`: Stops by name from `silver_stops`.
  - `trip_options`: arrival times for the earliest tram trip (with HSL's real-time departure), walking and city bike, from the Digitransit planner (§3.3, Q3).
  - `tram_lateness`: our measured Lateness per Vehicle on a Route, with Freshness.
  - `weather_outlook`: precipitation, chance of rain, wind, gusts and temperature for this and the next two hours (§3.4).
  - `bike_walk_or_wait`: runs all of the above for two Stops and applies `decide_trip`; it also matches the planner's tram to our journey (`journey_id`) to show our measured Lateness.
- FR-15.3 A deterministic rule function, not the language model, picks wait, walk or bike from the tool results. The model calls the tools and writes the answer. Thresholds (e.g. rain ≥ 0.5 mm/h or gusts ≥ 10 m/s within the ride time rule out the bike; walking wins when it arrives no later than the tram) are configuration and unit-tested (NFR-7).
- FR-15.4 The answer always shows the measured Lateness and its Freshness. With no live Ingestion Session, or for metro (ADR-0004), it says Lateness is unknown and bases advice on the timetable only. It never treats unknown as on time.
- FR-15.5 Outside the city bike season, or when no station within 400 m has a bike, the bike option is dropped with that reason.
- FR-15.6 If an outside API fails or times out (5 s per call), the agent answers with what it has and names the missing input. The rest of the app keeps working.
- FR-15.7 Advice is never stored with the viewer's position. Only counts by outcome (wait/walk/bike, reason) are logged for the blog.
- FR-15.8 No prediction: advice uses current Lateness only (non-goal "Delay prediction").

### FR-16 Agent — Bunching spotter
- FR-16.1 For each tram Route and direction, compute the **Headway** between consecutive Vehicles from departure Stop Events (`gold_departures`): at the most recent Stop both Vehicles departed, the time between their departures.
- FR-16.2 A Vehicle pair is **Bunching** when their Headway is below a configurable share of the scheduled headway (default 25 %, scheduled headway derived from the same Route's departures over the last hour, or Q3's source). The Vehicle behind is the one to recommend.
- FR-16.3 Live view: a "Bunching now" list (Route, direction, Stop, the two Vehicles, Headway, Lateness of each) and a map layer linking the pair. Data older than the Freshness *fading* band is not shown as live.
- FR-16.4 Rider advice: when a viewer's Route (My Routes or the FR-15 Stop) has Bunching, the agent says "the next tram has another one about N s behind it". It does not claim the second one is emptier unless HSL publishes occupancy for it (`occu`).
- FR-16.5 History: Bunching events per Route and Stop over a window, shown with Coverage, so the punctuality board can show where trams bunch. Trams only: metro has no Stop Events (ADR-0004).
- FR-16.6 The rules (Headway, Bunching threshold) are pure functions with unit tests on the recorded fixtures (NFR-7).

---

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | End-to-end freshness (HSL `tst` → visible on map): p95 ≤ 30 s while the pipeline is running. Relaxed from 15 s on 2026-10-03: the measured ~26 s comes from 4 streaming hops at a 4–7 s micro-batch cadence, and it still falls inside the Freshness "fully opaque" band (FR-7.3). |
| NFR-2 | Map refresh query against Lakebase ≤ 200 ms. |
| NFR-3 | Punctuality and heatmap queries ≤ 5 s on a serverless warehouse (size 2X-Small). |
| NFR-4 | Cost: nothing runs continuously by default. A `demo_session` job starts the ingestion pipeline, the Lakebase sync and the app, and stops all three after a configurable maximum (default 20 min); `demo_stop` ends a demo early. The warehouse and Lakebase compute stop themselves when idle. |
| NFR-5 | Reproducibility: all resources (pipeline, jobs, Lakebase instance and synced table, app, AI/BI dashboard, Genie space, schemas) are declared in the bundle. One `dev` target (plus `replay`); no `prod` target: readers adapt host, catalog, schema and names in the repo (decided 2026-10-03). |
| NFR-6 | Security: the app uses its service principal with least-privilege UC grants (SELECT on gold, silver_stops, silver_routes and silver_heartbeats only, the last for the FR-10.1 health strip; CAN RUN on the FR-12 Genie space; for FR-15: EXECUTE on the agent functions, USE CONNECTION on `hsl_fmi`/`hsl_digitransit`, READ on secret scope `hsl_live_transit`, CAN QUERY on the agent's model endpoint). In Lakebase it reads the synced schema and owns only the schemas it creates (`hsl_reports`, `hsl_users`, FR-13, FR-14). No secrets are needed for the HSL feed (public). The Digitransit key (FR-15) lives in secret scope `hsl_live_transit` and is read only inside the `trip_options` UC function (`secret()`, ADR-0007); it never reaches the app or the repo. FMI needs no key. |
| NFR-7 | Testability: parsing and lateness logic are pure functions with unit tests. The MQTT reader has a test mode that replays recorded payloads from a file. |

---

## 6. Proposed repository layout

```
hsl_live_transit/
├── README.md                   # setup and demo instructions for readers
├── CLAUDE.md                   # notes for Claude Code
├── claude_notes/
│   ├── CONTEXT.md              # glossary
│   ├── REQUIREMENTS.md         # this file
│   └── PROGRESS.md             # where we are, what's next
├── docs/adr/                   # decisions
├── docs/findings/              # measurements behind decisions (Phase 0, Q2)
├── databricks.yml              # bundle root (targets: dev, replay)
├── pyproject.toml              # builds the hsl_mqtt_source wheel
├── resources/
│   ├── schema.yml              # UC schema
│   ├── pipeline.yml            # Lakeflow Declarative Pipeline
│   ├── jobs.yml                # daily GTFS + retention job, raw volume
│   ├── demo.yml                # demo_session / demo_stop jobs (NFR-4, dev)
│   ├── lakebase.yml            # Lakebase project + synced table (dev)
│   ├── app.yml                 # Databricks App (dev)
│   ├── warehouse.yml           # 2X-Small serverless SQL Warehouse for analytics + Genie (dev)
│   ├── genie.yml               # Genie space for the Ask tab (FR-12, dev)
│   └── dashboard.yml           # AI/BI dashboard on gold_departures (FR-11, dev)
├── src/
│   ├── hsl_mqtt_source/        # Python Data Source (streaming reader), shipped as a wheel
│   ├── pipeline/               # bronze.py, silver.py, gold.py
│   ├── jobs/                   # gtfs_loader.py, retention.py, demo.py
│   ├── app/                    # app.py, db.py (Lakebase), store.py (app-owned Lakebase tables), warehouse.py, analytics.py, genie.py, map_style.py
│   └── dashboards/             # departures.lvdash.json (FR-11)
├── scripts/                    # one-off setup the bundle can't declare (Lakebase + UC GRANTs, Lakebase Change Data Feed)
├── scratchpad/                 # spike and analysis notebooks; not deployed
├── tests/                      # unit tests + recorded HFP sample payloads
└── blog/                       # draft article, screenshots
```

---

## 7. Delivery plan

| Phase | Outcome | Exit criteria |
|---|---|---|
| **0. Spike (de-risk)** ✅ done 2026-09-26 — [results](../docs/findings/PHASE0_RESULTS.md) | Prove the three riskiest assumptions | (a) a cluster in the workspace can reach `mqtt.hsl.fi` on 8883 or 443; (b) metro publishes `vp` and `dep` events on HFP; (c) a streaming Python Data Source runs inside a Declarative Pipeline on the chosen compute (serverless or classic). Record ~10 min of payloads for tests. |
| **1. Ingestion + bronze** | `hsl_mqtt` source, heartbeats, bronze table | Continuous pipeline lands events; stopping and restarting produces a new session_id and a visible gap. |
| **2. Silver + gold + GTFS** | Parsed events, lateness, departures, coverage, dims | Expectations pass on recorded data; unit tests green; `gold_departures` joins ≥ 95 % of stops to `silver_stops`. |
| **3. Lakebase + live map** | Synced table and Streamlit map | Map meets NFR-1 and NFR-2; fading visible after stopping the pipeline. |
| **4. Analytics views** | Punctuality board, heatmap, health strip, Genie chat tab (FR-12) | Slider changes results; coverage warnings appear after an induced gap; the chat answers "is tram N on time?" with the right lateness sign and says "not published" for metro. |
| **5. Package + publish** | Demo jobs, AI/BI dashboard (FR-11), Rider Reports and My Routes (FR-13, FR-14), README, blog draft | `databricks bundle run demo_session` starts a working app and stops everything after 20 min; a Rider Report written in the app shows on the map and appears in `lb_rider_reports_history`; My Routes survive an app restart; the README lists the steps for another workspace. |
| **6. Agents** | Bike, walk or wait (FR-15) first (your choice, 2026-10-08), then Bunching spotter (FR-16) | Bunching rules pass unit tests on the fixtures and a live demo lists at least one bunched pair with Headway; the FR-15 agent gives a sensible answer in a live demo for a late tram in dry and in rainy/windy weather (forced through config), drops the bike outside the season, and degrades readably when an outside API is down. Q3–Q5 closed. |

---

## 8. Risks and open questions

| # | Risk / question | Mitigation |
|---|---|---|
| R1 | Workspace egress may block MQTT ports | **Closed (Phase 0):** 8883 and 443 both open. Default `tls`, `wss` kept as fallback. |
| R2 | Metro HFP coverage (positions / stop events) may be partial | **Closed (Phase 0), decided in ADR-0004:** 14,993 metro `vp` in 10 min, 100 % `dl` null, 0 stop events. Metro stays on the map in a neutral colour; excluded from Punctuality and the heatmap. |
| R3 | Python Data Source streaming support on serverless pipelines | **Closed (Phase 0):** works on serverless pipelines and Standard clusters, given the R7 and R8 workarounds. |
| R4 | Single-driver MQTT client limits throughput | Acceptable for trams + metro. Adding buses later may need the Kafka path (would supersede ADR-0001). |
| R5 | Lakebase availability / region on the trial workspace | Fallback: map reads `gold_vehicle_current` via the warehouse (would supersede ADR-0003). |
| R6 | HSL GTFS URL or licence changes | URL is config. Data is CC BY 4.0: attribute HSL in app and blog. |
| R7 | **Found in Phase 0 (2026-09-26):** paho-mqtt's `loop_start()` builds its internal wake-up pipe via loopback TCP (`127.0.0.1` listen/accept). The Standard-access-mode sandbox blocks this, so it hangs forever in `accept()`; serverless likely has the same sandbox. Network egress to `mqtt.hsl.fi` on 8883 and 443 itself is fine. | Replace `paho.mqtt.client._socketpair_compat` with an AF_UNIX `socket.socketpair()` (done in the spike module; verified on a DBR 17.3 Standard cluster). Carry into `src/hsl_mqtt_source/` with a comment and pin the paho version. |
| R8 | **Found in Phase 0 (2026-09-26):** on Standard access mode (Spark Connect), the Python Data Source runs in a separate worker process that cannot import modules from workspace folders, so it fails with `PYTHON_DATA_SOURCE_ERROR … ModuleNotFoundError`. | Ship the module by value with `pyspark.cloudpickle.register_pickle_by_value(module)` before `spark.dataSource.register(...)`. Module-level side effects (e.g. the R7 patch) don't run in the worker, so they must happen inside functions. For production, package `src/hsl_mqtt_source/` as a wheel installed on the compute instead. Verified: ~1,750 rows per 5 s micro-batch on DBR 17.3 Standard. |
| R9 | **FR-15:** outside APIs (Digitransit, FMI) may be slow, rate-limited or change | Short timeouts and readable fallbacks (FR-15.6); endpoints and stored queries are config. |
| R10 | **FR-15:** city bikes run April–October only, so the bike option is unavailable for half the year | FR-15.5 drops the bike with a reason; the blog demo needs a run within the season (ends 2026-10-31). |
| Q1 | Should the demo-session job also start/stop the SQL warehouse and app compute? | **Closed (Phase 5):** it starts and stops the ingestion pipeline, the Lakebase sync and the app; the warehouse auto-stops (NFR-4). |
| Q2 | Stop Events carry `ttarr`/`ttdep`; in samples `dl` did not match `tst − ttdep`. Should Stop Event lateness be computed from `ttdep` instead of `-dl`? | **Closed (Phase 2):** both kept, ADR-0005. Lateness stays −`dl`; `timetable_lateness_s` is added next to it. See docs/findings/Q2_STOP_EVENT_LATENESS.md. |
| Q3 | **FR-15, FR-16:** when is the next tram due at a Stop? We don't load GTFS `stop_times` (~1 GB) and silver doesn't parse `next_stop` from the topic. | **Closed 2026-10-08:** the Digitransit planner gives the tram trip with HSL's real-time departure; our measured Lateness for the same journey is shown beside it. |
| Q4 | **FR-15:** which FMI forecast to use (stored query, model, resolution), or a different source such as Open-Meteo? | **Closed 2026-10-08:** FMI edited forecast, hourly (§3.4). Open-Meteo has 15-min steps but FMI is the local official source. |
| Q5 | **FR-15, FR-16:** where do the agents run? | **Closed 2026-10-08:** tools as UC functions (ADR-0007). The Supervisor Agent and Claude endpoints are gated on our account, so a tool-calling loop in the app runs them (ADR-0008). |
