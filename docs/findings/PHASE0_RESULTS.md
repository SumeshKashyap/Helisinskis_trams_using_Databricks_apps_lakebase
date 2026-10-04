# Phase 0 spike — results

**Run:** 2026-09-26, 21:28 Helsinki time (Saturday evening), job run [598609704021697](https://adb-7405611495879743.3.azuredatabricks.net/?o=7405611495879743#job/291490888462770/run/598609704021697)
**Compute:** notebook on `Sumesh Kashyap's Cluster` (DBR 17.3, Standard access mode); pipeline `hsl_phase0_spike_c2` on serverless (continuous, stopped after ~4 min)
**Fixtures:** `/Volumes/my_databricks_workspace/hsl_spike/raw/hfp_samples/20260926T182830Z/` — 212,892 messages over 10 min (JSONL, one file per minute)

## Summary

| Check | Result | Detail |
|---|---|---|
| (a) egress tls:8883 | PASS | DNS, TCP and TLS 1.3 in 0.2 s; MQTT CONNACK OK |
| (a) egress wss:443 | PASS | DNS, TCP and TLS 1.3 in 0.1 s; MQTT CONNACK OK |
| (b) tram vp events | PASS | 196,593 msgs, 81 vehicles |
| (b) tram dep events | PASS | 443 msgs, 80 vehicles |
| (b) tram vp has lateness | PASS | 1.2 % of vp messages have `dl = null` |
| (b) metro vp events | PASS | 14,993 msgs, 26 vehicles |
| (b) metro dep events | **FAIL** | 0 msgs |
| (b) metro vp has lateness | **FAIL** | 100 % of vp messages have `dl = null` |
| (bonus) GTFS URL | PASS | HTTP 200, 81 MB, refreshed daily (Last-Modified 05:03 GMT) |
| (c1) Python Data Source stream (notebook, Standard access) | PASS | 39,206 events, 9 heartbeats, 24 micro-batches in 120 s |
| (c2) Python Data Source in Declarative Pipeline (serverless) | PASS | 78,800 events, 20 heartbeats |

## Feed profile (10 min)

| event_type | mode | messages | per sec | vehicles | % `dl` null |
|---|---|---:|---:|---:|---:|
| vp | tram | 196,593 | 326.2 | 81 | 1.2 |
| vp | metro | 14,993 | 24.9 | 26 | 100.0 |
| arr | tram | 459 | 0.76 | 79 | 4.1 |
| pde | tram | 404 | 0.67 | 80 | 0.0 |
| dep | tram | 443 | 0.74 | 80 | 0.0 |
| arr / pde / dep | metro | 0 | 0 | 0 | — |

## Findings

1. **Egress is open** (R1 closed). Both transports work; use `tls` (8883) by default, keep `wss` as a fallback option.
2. **Metro sends no Lateness and no Stop Events** (R2 confirmed). Metro `vp` has `dl`, `odo`, `drst`, `acc` all null and `loc = MAN`. Metro can appear on the live map only. **Needs a decision** before Phase 1.
3. **Python Data Source streaming works on Standard clusters and serverless pipelines** (R3 closed), but only with two workarounds:
   - R7: paho's loopback-TCP wake-up pipe hangs in the sandbox → replace with an AF_UNIX `socketpair()`.
   - R8: the Python worker can't import workspace modules → ship the module by value with `cloudpickle.register_pickle_by_value`, or install it as a wheel.
4. **An interactive notebook session can hold a stale data source registration.** After a failed run, *Run all* (which restarts Python) was not enough; detaching and re-attaching fixed it. Worth a line in the blog.
5. **Trams send about 4 `vp` messages per vehicle per second** (326 msg/s ÷ 81 vehicles), not the 1 Hz the docs suggest. Silver dedup (FR-3.4) and sizing should assume this rate.
6. **Stop events carry scheduled times** (`ttarr`, `ttdep`). In one sample `dep` with `tst` 18:28:32 and `ttdep` 18:28:00 had `dl = 0`, and an `arr` 32 s after `ttarr` had `dl = -46`. `dl` may not equal `tst − ttdep` for stop events. **Open question for Phase 2:** compute Lateness for Stop Events from `tst − ttdep` rather than `-dl`? This would amend ADR-0002.
7. The sample ran on a Saturday evening; weekday peak volume will be higher.

## Resources left in the workspace

- Schema `my_databricks_workspace.hsl_spike` with volume `raw` (fixtures + checkpoints), tables `spike_bronze_notebook`, `spike_bronze_pipeline`
- Pipeline `hsl_phase0_spike_c2` (id `90278211-4177-427f-901d-129ecc1ef645`) — **stopped (IDLE)**
