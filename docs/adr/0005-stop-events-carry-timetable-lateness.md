# Stop Events carry Timetable Lateness next to Lateness

Amends ADR-0002 for Stop Events; ADR-0002 still holds for everything else.

Q2 measured how HSL's `dl` compares with the public timetable on 1,306 recorded Stop Events (see [docs/findings/Q2_STOP_EVENT_LATENESS.md](../findings/Q2_STOP_EVENT_LATENESS.md)). The two track each other closely (r = 0.99 for departures), but for departures −`dl` is about 22 s less late than `tst − ttdep`, and for arrivals about 34 s more late than `tst − ttarr`. HSL's figure presumably uses its internal second-precision schedule. The public `ttarr`/`ttdep` are rounded to whole minutes. Neither is plainly wrong, and the 22 s shift moves departures across the On-time window's edges.

We keep `lateness_s = -dl` (ADR-0002) as **the** Lateness, used for Punctuality, the map and the heatmap. We also store **Timetable Lateness** as `timetable_lateness_s` on `silver_stop_events` and `gold_departures`: `tst − ttarr` for arrivals and `tst − ttdep` for pre-departures and departures, in whole seconds, positive = late. It is for comparison and the blog. It is never substituted for Lateness and never used to fill a null Lateness.

## Consequences

- Rows written before 2026-10-03 have a null `timetable_lateness_s`. The scheduled times are already in silver, so the value can be recomputed if needed.
- Switching Punctuality to Timetable Lateness later is a query change, not a pipeline change; it would supersede this ADR.
- Position Events get no Timetable Lateness: they carry no scheduled time.
