"""Delete rows past retention: bronze 5 days (FR-2.2), silver and gold 30 days (FR-3.7, FR-4.4).

Downstream streaming reads use skipChangeCommits, so these deletes don't break the pipeline.
Not covered here: gold_vehicle_current (one row per vehicle, an AUTO CDC target) and the
materialized views, which apply the 30-day window in their own queries.
"""

import argparse

from pyspark.sql import SparkSession

RETENTION = [
    # table, timestamp column, days
    ("bronze_hfp_events", "received_at", 5),
    ("silver_position_events", "tst", 30),
    ("silver_stop_events", "tst", 30),
    ("silver_heartbeats", "heartbeat_at", 30),
    ("gold_departures", "departed_at", 30),
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    args = p.parse_args()
    spark = SparkSession.builder.getOrCreate()

    for table, column, days in RETENTION:
        fq = f"{args.catalog}.{args.schema}.{table}"
        if not spark.catalog.tableExists(fq):
            print(f"{fq}: not created yet, skipped")
            continue
        result = spark.sql(
            f"DELETE FROM {fq} WHERE {column} < current_timestamp() - INTERVAL {days} DAYS"
        ).collect()
        print(f"{fq}: older than {days} days deleted {result}")


if __name__ == "__main__":
    main()
