"""Phase 0 spike: minimal HSL HFP MQTT client + streaming Python Data Source.

Throwaway code for de-risking (see REQUIREMENTS.md §7, Phase 0). The production
source will live in src/hsl_mqtt_source/ and be written properly.
"""

import json
import socket
import ssl
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


def _unix_socketpair():
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    return a, b



HSL_HOST = "mqtt.hsl.fi"
TRANSPORTS = {
    # name: (port, paho transport)
    "tls": (8883, "tcp"),
    "wss": (443, "websockets"),
}
EVENT_TYPES = ["vp", "arr", "pde", "dep"]
MODES = ["tram", "metro"]
DEFAULT_TOPICS = [
    f"/hfp/v2/journey/ongoing/{event}/{mode}/#" for event in EVENT_TYPES for mode in MODES
]


def connect(transport="tls", topics=DEFAULT_TOPICS, on_message=None, timeout_s=15):
    """Connect to HSL, subscribe, and start the network loop in a background thread.

    Returns the connected client. Raises ConnectionError if not connected within timeout_s.
    """
    # paho's wake-up pipe is a loopback TCP listen/accept on 127.0.0.1. The sandbox used by
    # Standard-access-mode clusters (and serverless) blocks that, so loop_start() hangs forever
    # in accept(). An AF_UNIX socketpair gives paho the same pipe without loopback TCP.
    # Patched here rather than at import: this module is shipped to Spark's Python data source
    # worker by value (cloudpickle), and module-level statements don't run there.
    mqtt._socketpair_compat = _unix_socketpair
    port, paho_transport = TRANSPORTS[transport]
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"dbx-hsl-spike-{uuid.uuid4().hex[:8]}",
        transport=paho_transport,
    )
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    if paho_transport == "websockets":
        client.ws_set_options(path="/")

    connected = threading.Event()

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code == 0:
            c.subscribe([(t, 0) for t in topics])
            connected.set()

    client.on_connect = on_connect
    if on_message:
        client.on_message = on_message
    # connect_async + loop_start: the blocking TCP/TLS/WebSocket handshake runs on paho's
    # (daemon) network thread, so a middlebox that stalls the handshake can't hang the caller.
    # paho would otherwise wait up to `keepalive` seconds per handshake step.
    client.connect_timeout = timeout_s
    client.connect_async(HSL_HOST, port, keepalive=60)
    client.loop_start()
    if not connected.wait(timeout_s):
        # Don't loop_stop(): it joins the network thread, which may be stuck in the handshake.
        client._thread_terminate = True
        raise ConnectionError(f"No CONNACK from {HSL_HOST}:{port} ({transport}) in {timeout_s}s")
    return client


def parse_topic(topic):
    """Return (event_type, mode) from an HFP v2 topic.

    /hfp/v2/journey/ongoing/<event_type>/<mode>/...  -> parts[5], parts[6]
    """
    parts = topic.split("/")
    return parts[5], parts[6]


# ---------------------------------------------------------------------------
# Streaming Python Data Source (check c)
# ---------------------------------------------------------------------------

from pyspark.sql.datasource import DataSource, SimpleDataSourceStreamReader  # noqa: E402

SCHEMA = "kind string, topic string, payload string, received_at timestamp, session_id string"
HEARTBEAT_EVERY_S = 10


class HslMqttSpikeDataSource(DataSource):
    """spark.readStream.format("hsl_mqtt_spike").option("transport", "tls"|"wss").load()

    Register with register() below, not spark.dataSource.register() directly.
    """

    @classmethod
    def name(cls):
        return "hsl_mqtt_spike"

    def schema(self):
        return SCHEMA

    def simpleStreamReader(self, schema):
        return _SpikeReader(self.options)


class _SpikeReader(SimpleDataSourceStreamReader):
    """Runs on the driver; buffers MQTT messages between micro-batches.

    MQTT cannot replay, so readBetweenOffsets() returns nothing: after a failure
    the lost messages are simply a Data Gap (ADR-0001).
    """

    def __init__(self, options):
        self.transport = options.get("transport", "tls")
        self.max_buffer = int(options.get("max_buffer", "200000"))
        self.session_id = uuid.uuid4().hex
        self.client = None
        self.buffer = None
        self.dropped = 0
        self.last_heartbeat = 0.0

    def _ensure_connected(self):
        if self.client is not None:
            return
        self.buffer = deque()

        def on_message(c, userdata, msg):
            if len(self.buffer) >= self.max_buffer:
                self.buffer.popleft()
                self.dropped += 1
            self.buffer.append(
                (msg.topic, msg.payload.decode("utf-8", "replace"), datetime.now(timezone.utc))
            )

        self.client = connect(self.transport, on_message=on_message)

    def initialOffset(self):
        return {"seq": 0}

    def read(self, start):
        self._ensure_connected()
        rows = []
        while self.buffer:
            topic, payload, received_at = self.buffer.popleft()
            rows.append(("event", topic, payload, received_at, self.session_id))
        now = time.time()
        if now - self.last_heartbeat >= HEARTBEAT_EVERY_S:
            self.last_heartbeat = now
            info = json.dumps({"dropped": self.dropped})
            rows.append(("heartbeat", None, info, datetime.now(timezone.utc), self.session_id))
        return iter(rows), {"seq": start["seq"] + len(rows)}

    def readBetweenOffsets(self, start, end):
        return iter([])

    def commit(self, end):
        pass


def register(spark):
    """Register the data source so Spark's Python worker can load it.

    The worker process can't import this module from the workspace folder, so ship the
    module's code by value instead of by reference.
    """
    import sys
    from pyspark import cloudpickle

    cloudpickle.register_pickle_by_value(sys.modules[__name__])
    spark.dataSource.register(HslMqttSpikeDataSource)
