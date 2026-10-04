"""Demo session control (NFR-4): start, hold and stop the ingestion pipeline, the Lakebase sync and the app.

One script, three modes, used by the `demo_session` job (start → hold → stop) and by `demo_stop`:
  start  loads GTFS first if silver_stops doesn't exist yet, then starts all three.
  hold   waits until --max-minutes have passed since the run started, or until the ingestion
         pipeline is no longer running (someone ran demo_stop, or it failed).
  stop   stops all three. Safe to run any time; the job runs it even when an earlier task fails.

The warehouse isn't touched: it stops itself after 5 idle minutes.
"""

import argparse
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.apps import ComputeState

POLL_S = 30
# A continuous pipeline takes a few minutes to reach RUNNING; don't treat that as stopped.
STARTUP_GRACE_S = 10 * 60
ACTIVE_PIPELINE_STATES = {"RUNNING", "STARTING", "RESETTING", "DEPLOYING"}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["start", "hold", "stop"])
    p.add_argument("--pipeline-id", required=True)
    p.add_argument("--synced-table", required=True, help="UC name of the Lakebase synced table.")
    p.add_argument("--app", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--daily-job-id", type=int, help="GTFS job, run first when silver_stops is missing.")
    p.add_argument("--max-minutes", type=float, default=20)
    p.add_argument("--run-start-ms", type=int, help="Job run start time ({{job.start_time.timestamp_ms}}).")
    return p.parse_args()


def sync_pipeline_id(w, synced_table):
    return w.postgres.get_synced_table(name=f"synced_tables/{synced_table}").status.pipeline_id


def pipeline_state(w, pipeline_id):
    return str(w.pipelines.get(pipeline_id).state.value)


def start_pipeline(w, pipeline_id):
    state = pipeline_state(w, pipeline_id)
    if state in ACTIVE_PIPELINE_STATES:
        print(f"pipeline {pipeline_id} already {state}")
        return
    w.pipelines.start_update(pipeline_id)
    print(f"pipeline {pipeline_id} starting")


def stop_pipeline(w, pipeline_id):
    state = pipeline_state(w, pipeline_id)
    if state not in ACTIVE_PIPELINE_STATES:
        print(f"pipeline {pipeline_id} already {state}")
        return
    w.pipelines.stop(pipeline_id)
    print(f"pipeline {pipeline_id} stopping")


def ensure_gtfs(w, args):
    """gold_departures joins silver_stops/silver_routes, so a fresh schema needs GTFS before the pipeline."""
    if w.tables.exists(f"{args.catalog}.{args.schema}.silver_stops").table_exists:
        return
    if not args.daily_job_id:
        raise SystemExit("silver_stops is missing and no --daily-job-id was given to load GTFS")
    print("silver_stops missing: running the daily GTFS job first")
    run = w.jobs.run_now_and_wait(args.daily_job_id)
    print(f"GTFS job finished: {run.state.result_state}")


def start(w, args):
    ensure_gtfs(w, args)
    start_pipeline(w, args.pipeline_id)
    start_pipeline(w, sync_pipeline_id(w, args.synced_table))
    app = w.apps.get(args.app)
    if app.compute_status.state in (ComputeState.ACTIVE, ComputeState.STARTING):
        print(f"app {args.app} already {app.compute_status.state.value}")
    else:
        w.apps.start(args.app)  # don't wait: the pipelines need minutes anyway
        print(f"app {args.app} starting: {app.url}")


def hold(w, args):
    started = args.run_start_ms / 1000 if args.run_start_ms else time.time()
    deadline = started + args.max_minutes * 60
    while time.time() < deadline:
        state = pipeline_state(w, args.pipeline_id)
        if state not in ACTIVE_PIPELINE_STATES and time.time() - started > STARTUP_GRACE_S:
            print(f"ingestion pipeline is {state}: ending the demo early")
            return
        time.sleep(min(POLL_S, max(0, deadline - time.time())))
    print(f"{args.max_minutes:g} minutes are up")


def stop(w, args):
    stop_pipeline(w, args.pipeline_id)
    stop_pipeline(w, sync_pipeline_id(w, args.synced_table))
    app = w.apps.get(args.app)
    if app.compute_status.state in (ComputeState.ACTIVE, ComputeState.STARTING):
        w.apps.stop(args.app)
        print(f"app {args.app} stopping")
    else:
        print(f"app {args.app} already {app.compute_status.state.value}")


def main():
    args = parse_args()
    w = WorkspaceClient()
    {"start": start, "hold": hold, "stop": stop}[args.mode](w, args)


if __name__ == "__main__":
    main()
