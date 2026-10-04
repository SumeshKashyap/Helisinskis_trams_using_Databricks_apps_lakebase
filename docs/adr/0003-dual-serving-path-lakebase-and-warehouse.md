# Live map reads Lakebase; analytics read the SQL Warehouse

Current vehicle positions are synced from a gold Delta table to a Lakebase (Postgres) synced table, and the live map reads only from there; punctuality and heatmap views query Delta through a SQL Warehouse. The map polls every few seconds with single-row-per-vehicle lookups, which a Postgres index serves in milliseconds, while a warehouse would add latency and would have to stay running just for polling.

## Considered Options

- **SQL Warehouse only** — one path and simpler, but slower refreshes and a warehouse that stays running.
- **App subscribes to MQTT directly** — lowest latency, but the map would bypass the lakehouse the project exists to demonstrate.
