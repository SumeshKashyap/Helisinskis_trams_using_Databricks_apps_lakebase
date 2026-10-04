-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Phase 2 analysis (scratch)
-- MAGIC
-- MAGIC Ad-hoc checks behind the Phase 2 findings. Not part of the pipeline.
-- MAGIC - Q2: Lateness (−`dl`) vs Timetable Lateness → [docs/findings/Q2_STOP_EVENT_LATENESS.md](../docs/findings/Q2_STOP_EVENT_LATENESS.md), ADR-0005
-- MAGIC - Tram `vp` messages arrive 4× byte-identical
-- MAGIC - GTFS join rates for `gold_departures` (Phase 2 exit: ≥ 95 % of stops)
-- MAGIC
-- MAGIC Set `schema` to `hsl_live_transit` (live) or `hsl_live_transit_replay` (Phase 0 fixtures).

-- COMMAND ----------

-- MAGIC %python
-- MAGIC dbutils.widgets.text("schema", "hsl_live_transit_replay")

-- COMMAND ----------

USE CATALOG my_databricks_workspace;
USE SCHEMA IDENTIFIER(:schema);

-- COMMAND ----------

-- MAGIC %md ## Q2: Lateness vs Timetable Lateness per Stop Event type

-- COMMAND ----------

WITH s AS (
  SELECT event_type, lateness_s,
         unix_timestamp(tst) - unix_timestamp(CASE WHEN event_type = 'arr' THEN scheduled_arrival ELSE scheduled_departure END) AS from_schedule_s
  FROM silver_stop_events
  WHERE lateness_s IS NOT NULL
)
SELECT event_type,
       count(*) AS n,
       count_if(lateness_s = from_schedule_s) AS exact,
       count_if(abs(lateness_s - from_schedule_s) <= 5) AS within_5s,
       percentile(lateness_s - from_schedule_s, 0.5) AS median_diff,
       percentile(lateness_s - from_schedule_s, array(0.1, 0.9)) AS p10_p90,
       round(corr(lateness_s, from_schedule_s), 3) AS corr
FROM s
GROUP BY event_type

-- COMMAND ----------

-- MAGIC %md ## Duplicate Position Events in bronze

-- COMMAND ----------

SELECT split(topic, '/')[6] AS mode,
       count(*) AS msgs,
       count(DISTINCT payload) AS distinct_payloads,
       round(count(*) / count(DISTINCT payload), 2) AS copies_per_payload
FROM bronze_hfp_events
WHERE kind = 'event' AND split(topic, '/')[5] = 'vp'
GROUP BY 1

-- COMMAND ----------

-- MAGIC %md ## GTFS join rates in gold_departures

-- COMMAND ----------

SELECT count(*) AS departures,
       round(100 * count_if(stop_name IS NOT NULL) / count(*), 1) AS pct_stop_matched,
       round(100 * count_if(route_name IS NOT NULL) / count(*), 1) AS pct_route_matched,
       collect_set(CASE WHEN stop_name IS NULL THEN stop_id END) AS unmatched_stops,
       collect_set(CASE WHEN route_name IS NULL THEN route END) AS unmatched_routes
FROM gold_departures

-- COMMAND ----------

-- MAGIC %md ## Ingestion Sessions and Coverage

-- COMMAND ----------

SELECT * FROM silver_ingestion_sessions ORDER BY started_at

-- COMMAND ----------

SELECT minute_start, covered_s, coverage FROM gold_coverage_minutely ORDER BY minute_start
