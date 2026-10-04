# Lakebase also stores what viewers write; Rider Reports flow back to Unity Catalog with Lakebase Change Data Feed

Until now Lakebase only held a read copy of `gold_vehicle_current` (ADR-0003). Rider Reports (FR-13) and My Routes (FR-14) are written by viewers, so they need a transactional store with single-row inserts in milliseconds, uniqueness and rate-limit checks inside a transaction. We write them to Lakebase tables that the app's service principal creates and owns (`hsl_reports`, `hsl_users`), next to the synced table, and use Lakebase Change Data Feed (Lakebase → UC CDC, Public Preview) to land `hsl_reports` in Unity Catalog as `lb_rider_reports_history`. Delta stays the analytical home of everything; Postgres is the system of record only for what viewers write.

## Considered Options

- **Write reports to a Delta table through the SQL Warehouse** — one store, but each click waits on a warehouse (and keeps it running), with no row-level transactions for rate limits.
- **Lakebase plus a scheduled job copying reports to Delta** — works without a preview feature, but it's our own CDC code to maintain and run.

## Consequences

- Lakebase now holds data that exists nowhere else: deleting the Lakebase project loses reports and watchlists not yet synced (and watchlists are never synced, FR-14.4).
- The app's service principal owns two schemas in Lakebase; the project owner reads them only through grants or Lakebase Change Data Feed.
- Lakebase Change Data Feed is in Public Preview (a workspace admin enables it) and set up by a script, not the bundle. If it changes or disappears, a scheduled copy job is the fallback, and this ADR gets superseded.
- Reports are insert-only, so the CDC history table is the report table: no "latest state" deduplication needed.
