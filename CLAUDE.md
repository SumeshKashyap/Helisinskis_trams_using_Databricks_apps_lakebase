# HSL Live Transit — notes for Claude

Near-real-time Databricks App for Helsinki trams and metro (HSL HFP MQTT feed → Lakeflow pipeline → Lakebase/warehouse → Streamlit). Built as blog/community content.

## Start of every session

Planning files live in [claude_notes/](./claude_notes/): PROGRESS.md, REQUIREMENTS.md and CONTEXT.md.

1. Read [claude_notes/PROGRESS.md](./claude_notes/PROGRESS.md) first. It says which phase we're in and which step comes next.
2. Continue from the first unchecked item under "Next step" unless the user asks for something else.
3. At the end of a session, update claude_notes/PROGRESS.md: tick items, add a log entry with the date, and rewrite "Next step" so the next session can start cold.

## Rules

- **Catalog is always `my_databricks_workspace`.** Never `main`. Project schema is `hsl_live_transit`; `hsl_spike` is Phase 0 only.
- Databricks CLI: always `-p dev` (host `https://adb-7405611495879743.3.azuredatabricks.net`).
- Use the vocabulary in [claude_notes/CONTEXT.md](./claude_notes/CONTEXT.md) for table, column and UI names (e.g. `lateness_s`, never `delay`). When a new domain term settles, add it there; it stays a glossary, never a spec.
- Decisions that are hard to reverse, surprising and a real trade-off get an ADR in `docs/adr/` (next number: 0007). Don't silently contradict an existing ADR; supersede it.
- Requirement IDs (FR-x, NFR-x, R-x, Q-x) in claude_notes/REQUIREMENTS.md are stable. Reference them in code comments and commits instead of restating them.
- Don't leave pipelines running: this is on-demand. Stop continuous pipelines after testing and say so.
- Known platform gotchas (paho sandbox hang, worker can't import workspace modules, stale sessions) are listed in claude_notes/PROGRESS.md. Read them before touching ingestion.
