"""Bronze: raw HFP messages and heartbeats from the hsl_mqtt source (FR-2.1). No parsing."""

from pyspark import pipelines as dp

import hsl_mqtt_source

hsl_mqtt_source.register(spark)


@dp.table(
    name="bronze_hfp_events",
    comment="Raw HSL HFP messages as received, plus a heartbeat row every 10 s per Ingestion Session.",
    table_properties={
        # MQTT can't replay: a full refresh would delete history that can never be re-read.
        "pipelines.reset.allowed": "false",
    },
)
def bronze_hfp_events():
    reader = (
        spark.readStream.format("hsl_mqtt")
        .option("transport", spark.conf.get("hsl.transport", "tls"))
        .option("topics", spark.conf.get("hsl.topics", ""))
        .option("max_buffer", spark.conf.get("hsl.max_buffer", "200000"))
    )
    replay_path = spark.conf.get("hsl.replay_path", "")
    if replay_path:
        reader = reader.option("replay_path", replay_path)
    return reader.load()
