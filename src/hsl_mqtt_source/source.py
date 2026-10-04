"""Streaming Python Data Source `hsl_mqtt` (FR-1).

    spark.readStream.format("hsl_mqtt")
        .option("transport", "tls")       # tls (8883, default) | wss (443)
        .option("topics", "...")          # comma-separated; default: vp/arr/pde/dep x tram/metro
        .option("max_buffer", "200000")   # oldest messages dropped and counted beyond this
        .option("replay_path", "/Volumes/...")  # optional: replay recorded JSONL instead of MQTT
        .load()

Rows: kind ('event' | 'heartbeat'), topic, payload, received_at, session_id.
A heartbeat's payload is JSON with running totals of messages received and dropped.
"""

import itertools
import time
import uuid
from datetime import datetime, timezone

from pyspark.sql.datasource import DataSource, SimpleDataSourceStreamReader

from .core import EventBuffer, HeartbeatClock, heartbeat_payload, iter_replay, parse_topics_option

SCHEMA = "kind string, topic string, payload string, received_at timestamp, session_id string"
REPLAY_BATCH_ROWS = 20000


class HslMqttDataSource(DataSource):
    @classmethod
    def name(cls):
        return "hsl_mqtt"

    def schema(self):
        return SCHEMA

    def simpleStreamReader(self, schema):
        return _HslMqttReader(self.options)


class _HslMqttReader(SimpleDataSourceStreamReader):
    """Runs on the driver; buffers MQTT messages between micro-batches.

    MQTT cannot replay, so readBetweenOffsets() returns nothing: messages lost to a restart
    are a Data Gap (ADR-0001), made visible by the heartbeats and the new session_id.
    """

    def __init__(self, options):
        self.transport = options.get("transport", "tls")
        self.topics = parse_topics_option(options.get("topics"))
        self.max_buffer = int(options.get("max_buffer", "200000"))
        self.replay_path = options.get("replay_path") or None
        self.session_id = uuid.uuid4().hex  # FR-1.3: one per reader start
        self.buffer = None
        self.client = None
        self.replay = None
        self.replay_pos = 0
        self.heartbeat = HeartbeatClock()

    def _start(self, start):
        if self.buffer is not None:
            return
        self.buffer = EventBuffer(self.max_buffer)
        if self.replay_path:
            # Resume after the last committed line, so a restart doesn't replay twice.
            self.replay_pos = start.get("replay_pos", 0)
            self.replay = itertools.islice(iter_replay(self.replay_path), self.replay_pos, None)
            return

        from .client import connect

        def on_message(c, userdata, msg):
            self.buffer.append(
                (msg.topic, msg.payload.decode("utf-8", "replace"), datetime.now(timezone.utc))
            )

        self.client = connect(self.transport, self.topics, on_message)

    def _fill_from_replay(self):
        for _ in range(REPLAY_BATCH_ROWS):
            item = next(self.replay, None)
            if item is None:
                return
            topic, payload, received_at = item
            self.replay_pos += 1
            self.buffer.append((topic, payload, datetime.fromisoformat(received_at)))

    def initialOffset(self):
        return {"seq": 0, "replay_pos": 0}

    def read(self, start):
        self._start(start)
        if self.replay is not None:
            self._fill_from_replay()
        rows = [
            ("event", topic, payload, received_at, self.session_id)
            for topic, payload, received_at in self.buffer.drain()
        ]
        if self.heartbeat.due(time.monotonic()):
            rows.append(
                ("heartbeat", None, heartbeat_payload(self.buffer),
                 datetime.now(timezone.utc), self.session_id)
            )
        return iter(rows), {"seq": start["seq"] + len(rows), "replay_pos": self.replay_pos}

    def readBetweenOffsets(self, start, end):
        return iter([])

    def commit(self, end):
        pass
