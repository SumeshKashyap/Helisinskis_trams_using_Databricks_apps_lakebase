# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 0 spike — HSL Live Transit
# MAGIC
# MAGIC Answers the three questions that could change the design (REQUIREMENTS.md §7, §8), plus one bonus:
# MAGIC
# MAGIC | Check | Question | If it fails |
# MAGIC |---|---|---|
# MAGIC | **(a)** | Can this workspace reach `mqtt.hsl.fi` on 8883 (TLS) or 443 (WebSockets)? | R1: no feed at all → need an external bridge (supersedes ADR-0001) |
# MAGIC | **(b)** | Does HSL publish metro `vp` and `dep` events? | R2: metro shown on map only / dropped from punctuality |
# MAGIC | **(c1)** | Does a streaming Python Data Source work with Structured Streaming here? | ADR-0001 in question |
# MAGIC | **(c2)** | Does it work inside a Lakeflow Declarative Pipeline (serverless)? | R3: use classic pipeline compute |
# MAGIC | bonus | Is the HSL GTFS URL reachable? | R6: find the current URL |
# MAGIC
# MAGIC Check (b) also **records ~10 minutes of raw payloads** to a UC Volume as test fixtures.
# MAGIC
# MAGIC **Run on:** a classic all-purpose cluster, **Dedicated** access mode, **DBR 16.4 LTS** (or newer), single node is enough.
# MAGIC Serverless notebooks only allow `availableNow` triggers, which can't exercise a never-ending MQTT stream — serverless is tested separately in (c2).
# MAGIC
# MAGIC **Run during HSL service hours** (roughly 05:30–01:00 Helsinki time), otherwise (b) sees no traffic.

# COMMAND ----------

# MAGIC %pip install paho-mqtt==2.1.0

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "my_databricks_workspace")
dbutils.widgets.text("schema", "hsl_spike")
dbutils.widgets.text("volume", "raw")
dbutils.widgets.text("record_minutes", "10")
dbutils.widgets.text("stream_seconds", "120")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
VOLUME = dbutils.widgets.get("volume")
RECORD_MINUTES = float(dbutils.widgets.get("record_minutes"))
STREAM_SECONDS = int(dbutils.widgets.get("stream_seconds"))

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME}")
VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"

results = {}  # check name -> (passed: bool, detail: str)

# COMMAND ----------

import os, sys, socket, json, time, threading
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.getcwd())  # the spike module sits next to this notebook
import hsl_mqtt_spike_source as spike

now_hel = datetime.now(ZoneInfo("Europe/Helsinki"))
print(f"Helsinki time: {now_hel:%Y-%m-%d %H:%M}")
if 1 <= now_hel.hour < 5:
    print("WARNING: outside HSL service hours - check (b) will likely see little or no traffic.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## (a) Network egress to the HSL broker

# COMMAND ----------

import ssl

def tcp_open(host, port, timeout=10):
    """Probe DNS -> TCP -> TLS handshake separately, so a failure says *where* egress is blocked."""
    t0 = time.time()
    try:
        ip = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)[0][4][0]
    except OSError as e:
        return False, f"DNS failed: {e}"
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
    except OSError as e:
        return False, f"DNS {ip} OK; TCP failed after {time.time()-t0:.1f}s: {e}"
    try:
        with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as tls:
            return True, f"DNS {ip} OK; TCP OK; TLS OK ({tls.version()}) in {time.time()-t0:.1f}s"
    except (OSError, ssl.SSLError) as e:
        sock.close()
        return False, (f"DNS {ip} OK; TCP OK; TLS handshake failed after {time.time()-t0:.1f}s: "
                       f"{type(e).__name__}: {e} (firewall / TLS inspection?)")

working_transports = []
for name, (port, _) in spike.TRANSPORTS.items():
    ok, detail = tcp_open(spike.HSL_HOST, port)
    if ok:
        try:
            client = spike.connect(name)
            client.loop_stop(); client.disconnect()
            detail += "; MQTT CONNACK OK"
            working_transports.append(name)
        except Exception as e:
            ok, detail = False, detail + f"; MQTT failed: {e}"
    results[f"(a) egress {name}:{port}"] = (ok, detail)
    print(name, port, ok, detail)

TRANSPORT = working_transports[0] if working_transports else None
print("Using transport:", TRANSPORT)
assert TRANSPORT, "No route to mqtt.hsl.fi - stop here; see R1 in REQUIREMENTS.md"

# COMMAND ----------

# MAGIC %md
# MAGIC ## (b) Record the feed and check metro coverage
# MAGIC Writes one JSONL file per minute to the Volume: `{"topic", "payload", "received_at"}` per line.

# COMMAND ----------

counts = Counter()          # (event_type, mode) -> messages
vehicles = {}               # (event_type, mode) -> set of vehicle ids
dl_null = Counter()         # (event_type, mode) -> messages with dl == null (no Lateness possible)
lock = threading.Lock()
buffer = []

def on_message(c, userdata, msg):
    event_type, mode = spike.parse_topic(msg.topic)
    payload = msg.payload.decode("utf-8", "replace")
    line = json.dumps({"topic": msg.topic, "payload": payload,
                       "received_at": datetime.now(timezone.utc).isoformat()})
    with lock:
        counts[(event_type, mode)] += 1
        buffer.append(line)
        try:
            body = next(iter(json.loads(payload).values()))
            vehicles.setdefault((event_type, mode), set()).add(f"{body.get('oper')}/{body.get('veh')}")
            if body.get("dl") is None:
                dl_null[(event_type, mode)] += 1
        except Exception:
            pass

rec_dir = f"{VOLUME_PATH}/hfp_samples/{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
os.makedirs(rec_dir, exist_ok=True)

client = spike.connect(TRANSPORT, on_message=on_message)
start = time.time()
minute = 0
try:
    while time.time() - start < RECORD_MINUTES * 60:
        time.sleep(60)
        with lock:
            lines, buffer[:] = list(buffer), []
        with open(f"{rec_dir}/part-{minute:03d}.jsonl", "w") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        minute += 1
        print(f"minute {minute}: {len(lines)} msgs, totals so far {sum(counts.values())}")
finally:
    client.loop_stop(); client.disconnect()

elapsed = time.time() - start
print(f"Recorded {sum(counts.values())} messages in {elapsed:.0f}s -> {rec_dir}")

# COMMAND ----------

rows = [(e, m, counts[(e, m)], round(counts[(e, m)] / elapsed, 2), len(vehicles.get((e, m), ())),
         round(100 * dl_null[(e, m)] / counts[(e, m)], 1) if counts[(e, m)] else None)
        for e in spike.EVENT_TYPES for m in spike.MODES]
display(spark.createDataFrame(rows, "event_type string, mode string, messages long, per_sec double, "
                                    "distinct_vehicles long, pct_dl_null double"))

for mode in spike.MODES:
    for event in ["vp", "dep"]:
        n = counts[(event, mode)]
        results[f"(b) {mode} {event} events"] = (n > 0, f"{n} msgs, {len(vehicles.get((event, mode), ()))} vehicles")
    n_vp = counts[("vp", mode)]
    pct = 100 * dl_null[("vp", mode)] / n_vp if n_vp else 100.0
    results[f"(b) {mode} vp has lateness (dl)"] = (pct < 5, f"{pct:.1f}% of vp messages have dl = null")

# COMMAND ----------

# MAGIC %md
# MAGIC Quick look at one payload per event type (sanity-check field names, and the sign of `dl`: negative = behind schedule → our Lateness = −dl, ADR-0002).

# COMMAND ----------

samples = (spark.read.json(rec_dir)
           .selectExpr("split(topic, '/')[5] as event_type", "split(topic, '/')[6] as mode", "payload")
           .dropDuplicates(["event_type", "mode"]))
display(samples)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Bonus: GTFS static URL

# COMMAND ----------

import urllib.request
GTFS_URL = "https://infopalvelut.storage.hsldev.com/gtfs/hsl.zip"
try:
    req = urllib.request.Request(GTFS_URL, method="HEAD")
    with urllib.request.urlopen(req, timeout=15) as r:
        size_mb = int(r.headers.get("Content-Length", 0)) / 1e6
        results["(bonus) GTFS URL"] = (r.status == 200, f"HTTP {r.status}, {size_mb:.0f} MB, Last-Modified {r.headers.get('Last-Modified')}")
except Exception as e:
    results["(bonus) GTFS URL"] = (False, str(e))
print(results["(bonus) GTFS URL"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## (c1) Streaming Python Data Source in Structured Streaming
# MAGIC Runs the custom source for `stream_seconds`, writing to a Delta table, then stops it.

# COMMAND ----------

spike.register(spark)  # ships the module by value to Spark's Python worker

table = f"{CATALOG}.{SCHEMA}.spike_bronze_notebook"
checkpoint = f"{VOLUME_PATH}/_checkpoints/spike_bronze_notebook"
spark.sql(f"DROP TABLE IF EXISTS {table}")
dbutils.fs.rm(checkpoint, True)

try:
    query = (spark.readStream.format("hsl_mqtt_spike").option("transport", TRANSPORT).load()
             .writeStream.option("checkpointLocation", checkpoint)
             .trigger(processingTime="5 seconds")
             .toTable(table))
    deadline = time.time() + STREAM_SECONDS
    while query.isActive and time.time() < deadline:
        time.sleep(5)
    progress = query.recentProgress
    if not query.isActive:  # the stream died: surface why instead of reporting zero counts
        raise RuntimeError(f"stream terminated early: {str(query.exception())[:2000]}")
    query.stop()

    stats = spark.sql(f"""
        SELECT count_if(kind = 'event') AS events,
               count_if(kind = 'heartbeat') AS heartbeats,
               count(DISTINCT session_id) AS sessions
        FROM {table}""").first()
    batches = len(progress)
    ok = stats.events > 0 and stats.heartbeats > 0
    results["(c1) Python Data Source stream (classic)"] = (
        ok, f"{stats.events} events, {stats.heartbeats} heartbeats, {batches} micro-batches in {STREAM_SECONDS}s")
except Exception as e:
    results["(c1) Python Data Source stream (classic)"] = (False, f"{type(e).__name__}: {e}")
print(results["(c1) Python Data Source stream (classic)"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## (c2) Inside a Lakeflow Declarative Pipeline (serverless)
# MAGIC
# MAGIC Manual step — create a pipeline once:
# MAGIC 1. **Jobs & Pipelines → Create → ETL pipeline**, source code: `scratchpad/phase0_pipeline.py` (this folder).
# MAGIC 2. Default catalog / schema: the same as this notebook's widgets.
# MAGIC 3. **Serverless** on; pipeline mode **Continuous**.
# MAGIC 4. Settings → **Environment** → dependencies: `paho-mqtt==2.1.0`.
# MAGIC 5. Configuration: `spike.transport` = the transport chosen in (a) (`tls` or `wss`). If the pipeline fails with `ModuleNotFoundError: hsl_mqtt_spike_source`, also set `spike.module_dir` to this folder's workspace path.
# MAGIC 6. Start it, wait ~3 minutes, stop it, then run the next cell.
# MAGIC
# MAGIC If serverless fails, switch the pipeline to classic compute and retry — that answers R3 either way.

# COMMAND ----------

pipeline_table = f"{CATALOG}.{SCHEMA}.spike_bronze_pipeline"
try:
    s = spark.sql(f"""SELECT count_if(kind='event') AS events, count_if(kind='heartbeat') AS heartbeats
                      FROM {pipeline_table}""").first()
    results["(c2) Python Data Source in pipeline"] = (s.events > 0, f"{s.events} events, {s.heartbeats} heartbeats")
except Exception as e:
    results["(c2) Python Data Source in pipeline"] = (False, f"not run yet or failed: {type(e).__name__}")
print(results["(c2) Python Data Source in pipeline"])

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary
# MAGIC Paste this table into the Phase 0 notes / REQUIREMENTS.md risks section.

# COMMAND ----------

summary = [(name, "PASS" if ok else "FAIL", detail) for name, (ok, detail) in results.items()]
display(spark.createDataFrame(summary, "check string, result string, detail string"))
print(f"Fixtures: {rec_dir}")
