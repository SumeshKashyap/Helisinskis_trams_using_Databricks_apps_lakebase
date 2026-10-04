# Helsinki's trams, live: a streaming lakehouse, Lakebase and a Databricks App, end to end

*DRAFT for review. Screenshots are marked `[SCREENSHOT: …]`.*

Every tram in Helsinki tells the world where it is, about four times a second. Helsinki Region Transport (HSL) publishes those positions on an open MQTT feed, together with how far each tram is from its timetable. I wanted to see how much of a real, near-real-time app I could build on that feed using only Databricks: no Kafka, no extra servers, nothing a reader couldn't deploy from one repository.

The result is a Databricks App with a live map of every tram and metro, a punctuality board, a stop-level lateness map, a chat box that answers "Is tram 4 on time right now?", and a way for riders to report problems that flows back into the lakehouse. This post walks through how it fits together, what each Databricks component does, and what surprised me along the way. Every component section ends with links to the official documentation, so you can dig deeper on your own.

[SCREENSHOT: the app's live map with trams coloured by lateness, the health strip and the My Routes strip]

The code is on GitHub: [SumeshKashyap/Helisinskis_trams_using_Databricks_apps_lakebase](https://github.com/SumeshKashyap/Helisinskis_trams_using_Databricks_apps_lakebase).

## The architecture in one picture

```
HSL HFP (MQTT) ──► custom PySpark streaming source ──► Lakeflow pipeline: bronze → silver → gold
                                                                   │
                         ┌─────────────────────────────────────────┼──────────────────────────┐
                         ▼                                         ▼                          ▼
            Lakebase synced table                     Serverless SQL Warehouse       AI/BI dashboard
                         │                                         │
                         ▼                                         ▼
                  Live map (4 s refresh)          Punctuality · Stop lateness · Health · Ask (Genie)
                         └──────────────── Streamlit Databricks App ────────────────┘
                                  │ Rider Reports, My Routes (writes)
                                  ▼
                       Lakebase Postgres tables ──► Lakebase Change Data Feed ──► Unity Catalog
```

Three serving paths, each chosen for its access pattern:

- **Lakebase** for the live map, because it polls every few seconds and needs millisecond lookups.
- **A serverless SQL warehouse** for analytics, because those are aggregations over days of data.
- **Lakebase again, as a real Postgres database**, for what viewers write.

Everything is declared in one Databricks Asset Bundle: pipeline, jobs, Lakebase project and synced table, warehouse, Genie space, dashboard and app.

### Declarative Automation Bundles in brief

A bundle (Databricks Asset Bundles, now called Declarative Automation Bundles) is a `databricks.yml` file plus YAML files under `resources/` that describe workspace resources as code. `databricks bundle deploy -t dev` creates or updates all of them, `bundle run` starts a job, pipeline or app, and targets (`dev`, `prod`) switch catalogs, schemas and permissions without copying files. Python code is built into a wheel during deploy and attached to the pipeline, which turns out to matter for the custom data source below.

📖 Docs: [What are bundles?](https://docs.databricks.com/aws/en/dev-tools/bundles/) · [Bundle resources](https://docs.databricks.com/aws/en/dev-tools/bundles/resources)

## Getting MQTT into Spark without a message bus

HSL's High-Frequency Positioning (HFP) feed is MQTT, not Kafka. The usual answer is a small bridge process that forwards messages into Kafka or Event Hubs. I didn't want readers to run and pay for an extra service, so I wrote a **custom PySpark Python Data Source**: a streaming reader that subscribes to MQTT itself.

```python
class HslMqttDataSource(DataSource):
    @classmethod
    def name(cls):
        return "hsl_mqtt"

    def simpleStreamReader(self, schema):
        return _HslMqttReader(self.options)
```

The reader keeps one MQTT client on the driver, buffers messages between micro-batches, and returns them as rows. Bronze is then a plain streaming table:

```python
spark.readStream.format("hsl_mqtt").option("topics", topics).load()
```

**What a Python Data Source is.** Since Spark 4.0 (and on current Databricks runtimes and serverless), you can write a data source in pure Python by subclassing `DataSource` and returning a reader. A batch reader yields partitions of rows; a streaming reader also reports offsets, so Structured Streaming knows what it has already read. `simpleStreamReader` is the simplest variant: it runs on the driver and returns each micro-batch directly, which suits a low-volume push feed like MQTT. Once registered with `spark.dataSource.register(...)`, it works with `spark.read` and `spark.readStream`, including inside a Lakeflow pipeline.

📖 Docs: [PySpark custom data sources](https://docs.databricks.com/aws/en/pyspark/datasources)

The trade-off is explicit: MQTT has no replay. If the stream is down, those messages are gone. So instead of pretending there are no gaps, the reader emits a **heartbeat** row every 10 seconds, even when no trams are moving, and every start gets a new session id. Downstream, a gap in heartbeats is a **Data Gap**: missing data, never "no traffic".

Two platform surprises cost me most of the first day. Both are worth knowing if you write your own data source:

1. **paho-mqtt hangs inside the Databricks sandbox.** paho builds an internal wake-up pipe with a loopback TCP connection to 127.0.0.1. On Standard access mode clusters and serverless, that `accept()` never returns, so `loop_start()` hangs forever, with no error. The fix is to hand paho a Unix socket pair instead:

    ```python
    def _unix_socketpair():
        a, b = socket.socketpair()
        a.setblocking(False)
        b.setblocking(False)
        return a, b

    mqtt._socketpair_compat = _unix_socketpair  # inside the connect function, not at import
    ```

2. **The Python Data Source runs in a worker process that can't import your workspace files** (`ModuleNotFoundError`). Ship the module as a wheel, which the bundle builds and attaches to the pipeline, or register it by value with `cloudpickle`. Module-level code, like the patch above, doesn't run in the worker, so do the patching inside functions.

## What the feed actually looks like

A ten-minute recording on a Saturday evening showed 326 tram position messages per second from 81 trams. That's about four per tram per second, not the one per second the docs suggest. On inspection, **each tram position arrives four times, byte-identical**. Silver deduplicates on vehicle, event type and timestamp inside a two-minute watermark.

Two more findings shaped the design:

- **Metro publishes positions, but no lateness and no stop events.** Every metro `dl` is null. Metro stays on the map in a neutral grey, labelled "lateness not published", and is left out of punctuality rather than guessed.
- **HSL's `dl` is negative when a tram is late.** That's the opposite of most people's intuition. The pipeline converts it once, in silver, to `lateness_s = -dl`: positive means late. Nothing past bronze ever sees `dl`.

I also compared HSL's own lateness on stop events with the public timetable. They track each other closely (r = 0.99), but HSL's figure is about 22 seconds less late than "departure time minus timetable minute", probably because HSL uses a second-precision internal schedule. Twenty-two seconds is enough to move a departure across the edge of an on-time window, so both values are kept side by side.

## The medallion layers

The pipeline is a Lakeflow Declarative Pipeline in continuous mode.

**Lakeflow pipelines in brief.** Formerly Delta Live Tables. You declare *what* each table is (a Python function or a SQL query), and the pipeline works out the dependency graph, creates the tables, manages checkpoints and retries, and runs on serverless compute. Four building blocks cover this project:

- **Streaming tables** process each input row once, incrementally. Bronze and silver are streaming tables.
- **Materialized views** are recomputed (incrementally where possible) from their sources. Coverage and Ingestion Sessions are materialized views.
- **Expectations** are data-quality rules on a table: log the violation, drop the row, or fail the update. Results show up in the pipeline UI and event log.
- **AUTO CDC** (formerly `APPLY CHANGES`) turns a stream of changes into a table that keeps one current row per key (SCD type 1) or full history (SCD type 2), handling out-of-order events.

*Continuous* mode keeps the pipeline running and processes micro-batches as data arrives; *triggered* mode processes what's there and stops.

📖 Docs: [Lakeflow pipelines](https://docs.databricks.com/aws/en/ldp/) · [Concepts](https://docs.databricks.com/aws/en/ldp/concepts) · [Streaming tables](https://docs.databricks.com/aws/en/ldp/concepts/streaming-tables) · [Materialized views](https://docs.databricks.com/aws/en/ldp/concepts/materialized-views) · [Expectations](https://docs.databricks.com/aws/en/ldp/expectations) · [AUTO CDC](https://docs.databricks.com/aws/en/ldp/cdc) · [Delta change data feed](https://docs.databricks.com/aws/en/tables/features/change-data-feed)

The layers:

- **Bronze:** the raw payload, plus received time and session id. Append-only, kept for 5 days.
- **Silver:** typed Position Events and Stop Events, deduplicated, with data-quality expectations: positions outside the Helsinki region are dropped, and lateness beyond ±1 hour is flagged but kept. Also heartbeats and Ingestion Sessions.
- **Gold:**
  - `gold_vehicle_current`: the latest position per vehicle, maintained with AUTO CDC (SCD type 1) and Change Data Feed enabled for Lakebase;
  - `gold_departures`: one row per tram departure, joined to stop and route names from HSL's daily GTFS timetable (99 % of departures match a stop);
  - `gold_coverage_minutely`: for every minute, how many seconds we were actually recording.

That last table is what makes the app honest. Every number in it comes with its **Coverage**: the share of the window we were actually listening. If you start the pipeline at 14:00 and ask for "today", you'll see punctuality *and* a warning that it covers only part of the day.

## Serving the live map from Lakebase

The live map refreshes every 4 seconds for every open browser. Running that against a SQL warehouse would mean a warehouse query per viewer every few seconds, and a warehouse that never stops. Instead, `gold_vehicle_current` is synced into **Lakebase**, Databricks' serverless Postgres, as a continuous synced table.

**Lakebase in brief.** Lakebase is managed PostgreSQL inside Databricks, built for OLTP: point lookups, small transactions, many concurrent connections. A few properties matter here:

- **Separate storage and compute.** Compute autoscales and can *scale to zero* after an idle timeout, then wakes on the next connection.
- **Branches.** Like git branches for a database: a copy-on-write clone you can create in seconds, give a TTL and throw away. Handy for testing schema changes.
- **Synced tables.** A managed pipeline that copies a Unity Catalog Delta table into a read-only Postgres table, in *snapshot*, *triggered* or *continuous* mode. Continuous mode reads the source table's change data feed, which is why `gold_vehicle_current` has CDF enabled.
- **Unity Catalog integration.** A Lakebase database can be registered in Unity Catalog, and authentication uses Databricks identities with short-lived OAuth tokens instead of passwords.

📖 Docs: [Lakebase Postgres](https://docs.databricks.com/aws/en/oltp/projects) · [Synced tables](https://docs.databricks.com/aws/en/oltp/projects/sync-tables) · [Autoscaling](https://docs.databricks.com/aws/en/oltp/projects/autoscaling) · [Scale to zero](https://docs.databricks.com/aws/en/oltp/projects/scale-to-zero) · [Branches](https://docs.databricks.com/aws/en/oltp/projects/branches)

The synced table is declared in the bundle:

```yaml
postgres_synced_tables:
  gold_vehicle_current_online:
    source_table_full_name: ${var.catalog}.${var.schema}.gold_vehicle_current
    primary_key_columns: [vehicle_id]
    scheduling_policy: CONTINUOUS
```

The map query against Lakebase takes **about 20 ms** (p95 24 ms). The app (more on Databricks Apps below) is Streamlit with pydeck: trams are coloured by lateness band and fade out as their last position gets older (fully opaque up to 30 s, gone after 5 minutes). When you stop the pipeline you can watch the city slowly go transparent.

**End-to-end freshness**, from HSL's timestamp to the dot on the map, is about **26 seconds at p95**. Almost all of it is the four streaming hops at a micro-batch cadence of a few seconds each: bronze about 4.5 s, silver 9–12 s, gold about 25 s. The Lakebase sync and the map query add almost nothing. For a demo that's fine. Getting closer to 15 s would mean skipping the silver hop for the current-position table, or [Real-Time Mode](https://docs.databricks.com/aws/en/structured-streaming/real-time/), a Structured Streaming trigger built for sub-second latency.

## Analytics through a serverless SQL warehouse

The other tabs read the gold Delta tables through a 2X-Small serverless SQL warehouse.

**SQL warehouses in brief.** A SQL warehouse is compute dedicated to SQL, running the Photon engine. The *serverless* type starts in seconds, scales clusters with concurrency, and stops itself after an idle timeout, which suits an app that's only open during demos. The app talks to it with the `databricks-sql-connector`, authenticated as the app's service principal.

📖 Docs: [SQL warehouses](https://docs.databricks.com/aws/en/compute/sql-warehouse/) · [Serverless SQL warehouses](https://docs.databricks.com/aws/en/admin/sql/serverless)

The tabs:

- **Punctuality:** per route and direction, for the last hour or the current operating day, with a slider for the on-time window (default 60 s early to 3 min late). Punctuality is computed at query time from `gold_departures`, because the window belongs to the viewer.
- **Stop lateness:** a map of tram stops coloured by average departure lateness.
- **Health strip:** live or stopped, events per second, time since the last event, and the last Data Gap.

Every query runs in 0.5–2.6 s. The departures chart shades Data Gaps grey, so a flat line during an outage reads as "we weren't listening", not "no trams ran".

[SCREENSHOT: the Punctuality tab with the coverage warning and a shaded Data Gap]

The same data also backs an **AI/BI dashboard** (formerly Lakeview), built with no app code at all: datasets are SQL queries, widgets are drag-and-drop, and the on-time window bounds are dashboard parameters. The dashboard is a `.lvdash.json` file deployed by the bundle.

📖 Docs: [AI/BI dashboards](https://docs.databricks.com/aws/en/dashboards) · [AI/BI overview](https://docs.databricks.com/aws/en/ai-bi/)

## "Is tram 4 on time right now?"

The Ask tab is a **Genie** space over the gold tables, called from the app through the Genie Conversation API.

**Genie in brief.** A Genie space (now called a Genie Agent) is a natural-language interface over a fixed set of Unity Catalog tables. An author adds the tables, plain-text instructions, example SQL queries and trusted functions; Genie turns questions into SQL, runs it on a SQL warehouse as the asking user, and returns the result with the query. The Conversation API exposes the same thing to code: start a conversation, post a message, poll until the answer and its SQL are ready.

📖 Docs: [Genie Agents](https://docs.databricks.com/aws/en/genie-agents/) · [Conversation API](https://docs.databricks.com/aws/en/genie-agents/conversation-api) · [Best practices](https://docs.databricks.com/aws/en/genie-agents/best-practices)

 The interesting part wasn't wiring it up; it was the instructions. Out of the box, Genie had no way to know that positive lateness means late, that metro lateness is unknown rather than zero, or what "today" means for a service that runs past midnight. Two failures taught me the most:

- With the pipeline stopped, Genie answered "**No**" to "Is tram 4 on time?". The honest answer is "unknown". The fix was an instruction: if nothing was seen in the last 5 minutes, say the feed isn't running and give the last time the route was seen.
- It never mentioned coverage until an instruction required it on every answer about a time window.

The app shows the SQL Genie ran under every answer. That's the single best feature for trusting it.

[SCREENSHOT: Ask tab answering "Is tram 4 on time right now?" with the SQL expanded]

## Writing back: Rider Reports and My Routes

First, a word on the app itself.

**Databricks Apps in brief.** Databricks Apps host Python (Streamlit, Dash, Gradio, Flask, FastAPI) or Node.js web apps on serverless compute inside the workspace, behind workspace single sign-on. Each app gets its own **service principal**, and you attach **resources** to it (a SQL warehouse, a Genie space, a Lakebase database, secrets…) in `app.yml` or the bundle. The platform grants the service principal access and injects connection details as environment variables, so the code holds no credentials. With *user authorization* enabled, the app can also act on behalf of the signed-in viewer. The runtime pins specific library versions, listed in the system environment page.

📖 Docs: [Databricks Apps](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/) · [Resources](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/resources) · [Authorization](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth) · [Lakebase in apps](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/lakebase) · [System environment](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/system-env)

Lakebase isn't only a read cache. Riders can **report a problem** with a tram on the map (late, crowded, skipped a stop, …) and save the **routes they watch**. Both are written to Postgres tables the app owns.

Two details I like in the Rider Reports design:

**The report saves what we measured at that moment, in the same statement.** The insert reads the vehicle's current lateness and position from the synced table, so "what the rider said" and "what we measured" can be compared later without any time-travel join:

```sql
INSERT INTO hsl_reports.rider_reports
    (reporter_id, vehicle_id, mode, route, direction, category, note,
     measured_lateness_s, measured_last_seen_at, lat, long)
SELECT %s, v.vehicle_id, v.mode, v.route, v.direction, %s, %s,
       v.lateness_s, v.last_seen_at, v.lat, v.long
FROM hsl_live_transit.gold_vehicle_current_online v
WHERE v.vehicle_id = %s
RETURNING report_id
```

**Rate limits are real transactions.** At most one report per vehicle every two minutes, and 20 per hour. They're checked in the same transaction as the insert, under an advisory lock per reporter. Reporters are stored as a hash of their email, never the email. Watched routes do hold emails, so they live in a separate schema that never leaves Lakebase.

Then the reports go back to the lakehouse with **Lakebase Change Data Feed** (Public Preview), the reverse direction of a synced table. It captures every change to the `hsl_reports` schema from the Postgres write-ahead log and lands it in Unity Catalog as a Delta table, `lb_rider_reports_history`. No pipeline or job of my own. My first report, about tram 1H, reached Delta about 0.3 seconds after the insert:

| category | measured lateness then | in the On-time window? |
|---|---|---|
| late | +2:18 | yes (up to +3:00) |

The rider felt it was late; by the definition we use, it was on time. That's exactly the kind of question you can now ask Genie: "Do Rider Reports match measured lateness?"

📖 Docs: [Lakebase Change Data Feed quickstart](https://docs.databricks.com/aws/en/oltp/projects/quickstart-lakebase-cdf)

Things that bit me here:

- **Let the app create its own schemas.** The app's service principal can create objects in Lakebase, but it can't use a schema someone else owns. If you run the app locally first, *you* own the schemas and the deployed app gets `permission denied`. I test SQL on a throwaway Lakebase branch with a 2-hour TTL instead. The app creates its schemas in a small `init_db.py` step before Streamlit starts.
- **Change Data Feed needs a workspace preview** called *Lakebase Change Data Feed*, on the workspace's Previews page (not the account console). It took a few minutes after enabling before the API accepted calls. Empty tables are skipped, so the history table only appears after the first row.

[SCREENSHOT: report form, purple report ring on the map, and the row in lb_rider_reports_history]

## Running it on demand

Nothing runs between demos. One Lakeflow job does it all:

```bash
databricks bundle run demo_session -t dev       # 20 minutes by default
databricks bundle run demo_stop -t dev          # end early
```

It starts the ingestion pipeline, the Lakebase sync and the app, waits, and stops all three. The stop task uses the job's `run_if: ALL_DONE` condition, so it runs even if something failed. The warehouse and Lakebase compute stop themselves when idle.

📖 Docs: [Lakeflow Jobs](https://docs.databricks.com/aws/en/jobs)

## What I'd tell you before you start

- **Design for gaps from day one.** Heartbeats, session ids and a coverage table took an afternoon. They made every later screen honest.
- **Pick the serving layer per access pattern, not per project.** Lakebase for the 4-second map, the warehouse for aggregates, and Postgres tables for writes all live happily in one app.
- **Spend your Genie time on instructions and example SQL**, not on wiring. And always show the SQL.
- **The Databricks Apps runtime pins Streamlit 1.38.** Run your smoke tests against that version; newer arguments crash the app.
- **Read the data before you model it.** Four copies of every message, metro without lateness, and a sign convention that runs backwards were all discovered in a ten-minute recording.

## Credits

Vehicle positions and timetables: [Helsinki Region Transport (HSL)](https://www.hsl.fi/en/hsl/open-data), licensed CC BY 4.0, via [Digitransit](https://digitransit.fi/en/developers/).

---

*Review notes (remove before publishing):*
- *Placeholders: four screenshots.*
- *Numbers are from 2026-10-03.*
- *Doc links point to the AWS docs; each page has a cloud selector for Azure and GCP.*
- *Still open before publishing: the lateness outliers (route 5T averaging about +10 min, route 2 about −18 min) and Jokeri (route 15) departures without stop names. Both show up in the dashboard and Genie answers, so worth checking first.*
