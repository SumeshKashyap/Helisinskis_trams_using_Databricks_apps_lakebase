# Ingest HSL MQTT via a custom PySpark streaming data source

---
status: accepted
---

We subscribe to the HSL high-frequency positioning MQTT feed directly from Structured Streaming using a custom Python Data Source (streaming reader), instead of a bridge process writing to Kafka/Event Hubs or to a UC Volume for Auto Loader. The project is primarily blog/community content, and keeping the whole pipeline inside Databricks — with no extra service to run — makes it reproducible and showcases the Python Data Source API.

## Considered Options

- **Bridge → Kafka/Event Hubs → Structured Streaming** — production-grade replay and buffering, but adds a paid external service readers must provision.
- **Bridge → UC Volume files → Auto Loader** — simple and robust, but many small files and a second process to operate.

## Consequences

- MQTT has no replay: messages published while the stream is down are lost. Gaps are accepted and must be visible, not hidden.
- The MQTT client lives on the driver and buffers between micro-batches, so throughput is bounded by a single node — acceptable for the trams + metro subset.
