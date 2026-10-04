# Q2: Stop Event Lateness, −`dl` vs `tst − ttdep`

**Date:** 2026-10-03 · **Data:** Phase 0 fixtures replayed into `my_databricks_workspace.hsl_live_transit_replay.silver_stop_events` (1,306 Stop Events, 10 min, Saturday evening). A 3-minute live sample on 2026-10-03 gave the same picture.

`from_schedule_s` = `tst − ttdep` for `pde`/`dep` and `tst − ttarr` for `arr`. Difference = `lateness_s − from_schedule_s`.

| event_type | n | exact match | within ±5 s | median diff | p10 … p90 diff | correlation | avg −`dl` | avg `tst − tt*` |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| dep | 443 | 5 | 48 | −22 s | −35 … −5 s | 0.992 | 31.0 s | 51.6 s |
| pde | 404 | 5 | 39 | −23 s | −35 … −6 s | 0.992 | 30.8 s | 52.8 s |
| arr | 440 | 3 | 34 | +34 s | −3 … +74 s | 0.941 | 52.9 s | 17.2 s |

## Reading

- The two measures move together almost perfectly (r = 0.99 for departures), so `dl` isn't noise. It is offset: for departures, −`dl` is consistently about 22 s *less late* than `tst − ttdep`.
- `ttarr` / `ttdep` are always whole minutes (`HH:MM:00.000Z`). HSL's internal schedule most likely has seconds precision, and `dl` is measured against that. The rounding alone can't explain a mean of −22 s, though: it would spread from 0 to −59 s, centred near −30 s, and that is roughly what we see.
- For arrivals, the offset flips to about +34 s. `ttarr` was equal to `ttdep` in the samples, so the arrival is compared with the same minute as the departure.

## Impact

With the default On-time window (−60 s … +180 s), a 22 s shift moves departures across the edges: trams leaving 60–82 s early count as on-time under −`dl` but early under `tst − ttdep`.

## Options

**Decided 2026-10-03: option 3**, see [ADR-0005](../adr/0005-stop-events-carry-timetable-lateness.md). Column: `timetable_lateness_s`.

1. **Keep −`dl` for everything** (current). It is HSL's own figure and presumably uses second-precision schedules. It is consistent with Position Event Lateness.
2. **Use `tst − ttdep` for departures.** It's transparent and reproducible from the public timetable, but inherits minute rounding and differs from the figure HSL shows on its own displays.
3. Keep −`dl` as `lateness_s`, and also expose `tst − ttdep` as a second column for the blog's discussion.
