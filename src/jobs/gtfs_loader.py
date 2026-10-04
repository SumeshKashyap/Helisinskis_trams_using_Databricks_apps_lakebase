"""Daily GTFS load: silver_stops (all boarding stops) and silver_routes (tram and metro) (FR-5).

Stops are not filtered by mode: HSL tags shared stops by their main vehicle type, so tram
stops served mostly by buses (e.g. Jokeri at Vermo) would be lost.

Downloads hsl.zip into the raw volume, extracts stops.txt and routes.txt, and overwrites
the two tables. Any failure raises before the overwrite, so yesterday's tables stay (FR-5.2).
"""

import argparse
import os
import shutil
import urllib.request
import zipfile
from datetime import datetime, timezone

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

# GTFS route_type -> Mode. HSL tags Jokeri light rail (route 15) as 900, but HFP reports it
# as tram, so it counts as tram here too.
ROUTE_TYPE_TO_MODE = {"0": "tram", "900": "tram", "1": "metro"}
# Sanity floors: far below today's counts (34 routes, ~8,400 stops), far above a broken file.
MIN_ROUTES = 10
MIN_STOPS = 1000


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--volume", default="raw")
    p.add_argument("--url", required=True)
    return p.parse_args()


def download(url, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, "hsl.zip")
    with urllib.request.urlopen(url, timeout=300) as resp, open(zip_path, "wb") as out:
        shutil.copyfileobj(resp, out, length=8 * 1024 * 1024)
    with zipfile.ZipFile(zip_path) as z:
        # Only the two small files: stop_times.txt alone is ~1 GB unpacked.
        for name in ("stops.txt", "routes.txt"):
            z.extract(name, dest_dir)
    os.remove(zip_path)  # ~80 MB a day otherwise; the two extracted files are kept
    return dest_dir


def read_csv(spark, path):
    # All columns as strings; HSL pads empty fields with a single space.
    df = spark.read.option("header", True).option("encoding", "UTF-8").csv(path)
    return df.select([F.nullif(F.trim(F.col(c)), F.lit("")).alias(c) for c in df.columns])


def mode_from(column, mapping):
    m = F.create_map(*[F.lit(x) for kv in mapping.items() for x in kv])
    return m[F.col(column)]


def main():
    args = parse_args()
    spark = SparkSession.builder.getOrCreate()
    fq = f"{args.catalog}.{args.schema}"
    run_dir = (
        f"/Volumes/{args.catalog}/{args.schema}/{args.volume}/gtfs/"
        f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    )
    download(args.url, run_dir)
    loaded_at = F.current_timestamp()

    routes = (
        read_csv(spark, f"{run_dir}/routes.txt")
        .withColumn("mode", mode_from("route_type", ROUTE_TYPE_TO_MODE))
        .where("mode IS NOT NULL")
        .select(
            F.col("route_id").alias("hsl_route_id"),
            F.col("route_short_name").alias("route"),
            F.col("route_long_name").alias("route_name"),
            "mode",
            F.col("route_type").cast("int").alias("gtfs_route_type"),
            loaded_at.alias("loaded_at"),
        )
    )
    stops = (
        read_csv(spark, f"{run_dir}/stops.txt")
        .where("coalesce(location_type, '0') = '0'")
        .select(
            "stop_id",
            "stop_code",
            "stop_name",
            F.col("stop_lat").cast("double").alias("lat"),
            F.col("stop_lon").cast("double").alias("long"),
            # HSL's main vehicle type for the stop (0 tram, 1 metro, 3 bus…); informational only
            F.col("vehicle_type").cast("int").alias("hsl_vehicle_type"),
            "zone_id",
            "platform_code",
            "parent_station",
            loaded_at.alias("loaded_at"),
        )
    )

    # No .cache(): unsupported on serverless; re-reading two small CSVs is cheap.
    n_routes, n_stops = routes.count(), stops.count()
    print(f"GTFS {run_dir}: {n_routes} tram/metro routes, {n_stops} stops")
    if n_routes < MIN_ROUTES or n_stops < MIN_STOPS:
        raise ValueError(f"GTFS looks broken ({n_routes} routes, {n_stops} stops); keeping old tables")

    routes.write.mode("overwrite").option("overwriteSchema", True).saveAsTable(f"{fq}.silver_routes")
    stops.write.mode("overwrite").option("overwriteSchema", True).saveAsTable(f"{fq}.silver_stops")


if __name__ == "__main__":
    main()
