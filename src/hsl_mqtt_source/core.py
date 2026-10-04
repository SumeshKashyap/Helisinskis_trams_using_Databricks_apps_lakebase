"""Pure parts of the hsl_mqtt source: no Spark, no network. Unit-tested in tests/."""

import glob
import json
import os
from collections import deque

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
HEARTBEAT_EVERY_S = 10  # FR-1.4


def parse_topic(topic):
    """Return (event_type, mode) from an HFP v2 topic.

    /hfp/v2/journey/ongoing/<event_type>/<mode>/...  -> parts[5], parts[6]
    """
    parts = topic.split("/")
    if len(parts) < 7 or parts[1:3] != ["hfp", "v2"]:
        raise ValueError(f"Not an HFP v2 topic: {topic!r}")
    return parts[5], parts[6]


def parse_topics_option(value):
    """Comma-separated topics option -> list; empty means the default 8 topics (FR-1.5)."""
    topics = [t.strip() for t in (value or "").split(",") if t.strip()]
    return topics or list(DEFAULT_TOPICS)


class EventBuffer:
    """Bounded FIFO between MQTT callbacks and micro-batches.

    When full, the oldest message is dropped and counted, never silently (FR-1.6).
    """

    def __init__(self, max_size):
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self.max_size = max_size
        self._items = deque()
        self.dropped_total = 0
        self.received_total = 0

    def append(self, item):
        self.received_total += 1
        if len(self._items) >= self.max_size:
            self._items.popleft()
            self.dropped_total += 1
        self._items.append(item)

    def drain(self, limit=None):
        out = []
        while self._items and (limit is None or len(out) < limit):
            out.append(self._items.popleft())
        return out

    def __len__(self):
        return len(self._items)


class HeartbeatClock:
    """Says when the next heartbeat row is due (FR-1.4). The first call is always due."""

    def __init__(self, every_s=HEARTBEAT_EVERY_S):
        self.every_s = every_s
        self._last = None

    def due(self, now):
        if self._last is None or now - self._last >= self.every_s:
            self._last = now
            return True
        return False


def heartbeat_payload(buffer):
    return json.dumps(
        {"received_total": buffer.received_total, "dropped_total": buffer.dropped_total}
    )


def replay_files(path):
    """A JSONL file, or a directory of *.jsonl files in name order (Phase 0 fixture layout)."""
    if os.path.isdir(path):
        return sorted(glob.glob(os.path.join(path, "*.jsonl")))
    return [path]


def iter_replay(path):
    """Yield (topic, payload, received_at_iso) from recorded fixtures (NFR-7)."""
    for file in replay_files(path):
        with open(file) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    yield r["topic"], r["payload"], r["received_at"]
