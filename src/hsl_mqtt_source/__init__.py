"""HSL HFP MQTT streaming source for Spark (FR-1). Installed as a wheel so Spark's Python
worker can import it (R8)."""


def register(spark):
    """Register the `hsl_mqtt` data source with this Spark session."""
    from .source import HslMqttDataSource

    spark.dataSource.register(HslMqttDataSource)
