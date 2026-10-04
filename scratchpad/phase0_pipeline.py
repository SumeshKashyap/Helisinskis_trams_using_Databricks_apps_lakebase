"""Phase 0 spike, check (c2): the custom MQTT source inside a Lakeflow Declarative Pipeline.

Pipeline configuration:
  spike.transport   tls | wss   (whichever passed check (a))
  spike.module_dir  optional: workspace path of this scratchpad/ folder, e.g.
                    /Workspace/Users/<you>/hsl_live_transit/scratchpad
                    (only needed if the import below fails)
Environment dependency: paho-mqtt==2.1.0
"""

import sys

from pyspark import pipelines as dp

module_dir = spark.conf.get("spike.module_dir", "")
if module_dir and module_dir not in sys.path:
    sys.path.insert(0, module_dir)

import hsl_mqtt_spike_source as spike  # noqa: E402

spike.register(spark)


@dp.table(name="spike_bronze_pipeline", comment="Phase 0 spike: raw HFP messages + heartbeats")
def spike_bronze_pipeline():
    return (
        spark.readStream.format("hsl_mqtt_spike")
        .option("transport", spark.conf.get("spike.transport", "tls"))
        .load()
    )
