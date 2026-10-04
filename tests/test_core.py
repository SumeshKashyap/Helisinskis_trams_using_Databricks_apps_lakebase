import json
from pathlib import Path

import pytest

from hsl_mqtt_source.core import (
    DEFAULT_TOPICS,
    EventBuffer,
    HeartbeatClock,
    heartbeat_payload,
    iter_replay,
    parse_topic,
    parse_topics_option,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_topic():
    topic = "/hfp/v2/journey/ongoing/vp/tram/0040/00075/1003/2/Olympiaterm./21:14/1111432/4/60;24/19/85/22"
    assert parse_topic(topic) == ("vp", "tram")


def test_parse_topic_rejects_other_feeds():
    with pytest.raises(ValueError):
        parse_topic("/gtfsrt/vp/hsl")


def test_default_topics_are_eight():
    assert len(DEFAULT_TOPICS) == 8
    assert "/hfp/v2/journey/ongoing/dep/metro/#" in DEFAULT_TOPICS


def test_topics_option():
    assert parse_topics_option("") == DEFAULT_TOPICS
    assert parse_topics_option(None) == DEFAULT_TOPICS
    assert parse_topics_option(" a/# , b/# ") == ["a/#", "b/#"]


def test_buffer_drops_oldest_and_counts():
    buf = EventBuffer(max_size=3)
    for i in range(5):
        buf.append(i)
    assert buf.drain() == [2, 3, 4]
    assert buf.dropped_total == 2
    assert buf.received_total == 5
    assert json.loads(heartbeat_payload(buf)) == {"received_total": 5, "dropped_total": 2}


def test_buffer_drain_limit():
    buf = EventBuffer(max_size=10)
    for i in range(4):
        buf.append(i)
    assert buf.drain(limit=3) == [0, 1, 2]
    assert len(buf) == 1


def test_heartbeat_every_10_seconds():
    clock = HeartbeatClock(every_s=10)
    assert clock.due(100.0)
    assert not clock.due(105.0)
    assert not clock.due(109.9)
    assert clock.due(110.0)
    assert not clock.due(115.0)


def test_replay_file_and_directory_give_same_rows():
    from_file = list(iter_replay(str(FIXTURES / "hfp_sample.jsonl")))
    from_dir = list(iter_replay(str(FIXTURES)))
    assert from_file == from_dir
    assert len(from_file) == 15
    topic, payload, received_at = from_file[0]
    assert parse_topic(topic)[0] in {"vp", "arr", "pde", "dep"}
    assert json.loads(payload)
    assert received_at.endswith("+00:00")
