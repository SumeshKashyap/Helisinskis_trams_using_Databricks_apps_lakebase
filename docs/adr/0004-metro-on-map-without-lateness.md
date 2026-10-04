# Metro is shown on the live map without Lateness, and excluded from Punctuality

HSL's HFP feed publishes metro Position Events only: `dl` is always null (positions are manual, `loc = MAN`) and there are no `arr`/`pde`/`dep` Stop Events (Phase 0: 14,993 metro `vp` messages in 10 min, 100 % `dl` null, 0 stop events). We keep metro on the live map, drawn in a neutral colour, and leave it out of Lateness colouring, the Punctuality board and the stop heatmap, rather than dropping metro from the app or inferring its lateness ourselves. Metro stays in scope because seeing it move is still useful and the gap is itself a finding worth explaining in the blog.

## Consequences

- Lateness is nullable end to end; "unknown" is never shown as 0 or as on-time.
- If HSL starts publishing metro `dl` or stop events, metro joins the analytics with no redesign; revisit this ADR then.
