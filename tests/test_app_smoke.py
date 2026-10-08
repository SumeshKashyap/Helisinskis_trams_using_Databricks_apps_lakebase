"""Runs src/app/app.py headless against fake Lakebase, SQL Warehouse, Genie and agent-model backends.

Run with the Streamlit version of the Databricks Apps runtime (1.38), which is older than PyPI's:
    uv run --no-project --with pytest --with streamlit==1.38.0 --with pydeck --with pandas --with databricks-sdk python -m pytest tests/test_app_smoke.py
"""

import datetime as dt
import sys
import types
from pathlib import Path
from typing import ClassVar

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("pydeck")

import pandas as pd

APP_DIR = Path(__file__).parents[1] / "src" / "app"
sys.path.insert(0, str(APP_DIR))
COLUMNS = [
    "vehicle_id",
    "mode",
    "route",
    "direction",
    "lat",
    "long",
    "heading",
    "lateness_s",
    "last_seen_at",
    "age_s",
]
NOW = dt.datetime.now(dt.UTC)


REPORT_COLUMNS = [
    "report_id", "reported_at", "vehicle_id", "mode", "route", "direction", "category", "note",
    "measured_lateness_s", "lat", "long", "age_s",
]  # fmt: skip


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.result = None

    def execute(self, sql, params=None):
        self.db.executed.append((sql, params))
        if "count(*) FILTER" in sql:
            self.result = (self.db.reports_last_hour, 0)
        elif "INSERT INTO hsl_reports.rider_reports" in sql:
            self.result = (42,)
            self.db.reports.append(params)
        else:
            self.result = None

    def fetchone(self):
        return self.result


class FakeLakebase:
    """Answers the live map, Rider Reports and My Routes queries; records writes."""

    instances: ClassVar[list] = []

    def __init__(self):
        self.executed = []
        self.reports = []
        self.reports_last_hour = 0
        self.watched = ["3"]
        FakeLakebase.instances.append(self)

    def query(self, sql, params=None):
        self.executed.append((sql, params))
        if "FROM hsl_reports.rider_reports" in sql:
            note = "<script>alert(1)</script>"
            return REPORT_COLUMNS, [
                (7, NOW, "40/75", "tram", "3", "2", "crowded", note, 75, 60.18, 24.95, 90.0)
            ]
        if "FROM hsl_users.watched_routes" in sql:
            return ["route"], [(r,) for r in self.watched]
        if "SELECT DISTINCT route" in sql:
            return ["route"], [("3",), ("8",), ("9",), ("M1",)]
        rows = [
            ("40/75", "tram", "3", "2", 60.18, 24.95, 222, 75, NOW, 5.0),
            ("50/169", "metro", "M1", "2", 60.17, 24.80, 261, None, NOW, 12.0),  # no Lateness
            ("40/81", "tram", "8", "1", 60.19, 24.93, 90, 700, NOW - dt.timedelta(seconds=120), 120.0),  # fading
            ("40/99", "tram", "9", "1", 60.19, 24.93, 90, 0, NOW - dt.timedelta(seconds=400), 400.0),  # hidden
        ]  # fmt: skip
        return COLUMNS, rows

    def transaction(self, fn):
        return fn(FakeCursor(self))


class FakeWarehouse:
    """Answers each analytics query by recognising the table and columns it asks for."""

    prefix = "cat.sch"

    def __init__(self, coverage_s=600.0, live=True):
        self.coverage_s = coverage_s
        self.live = live
        self.queries = []

    def table(self, name):
        return name

    def query(self, statement, params=None):
        self.queries.append((statement, params))
        ts = pd.Timestamp(NOW)
        if "last_minute" in statement:  # health strip
            hb = ts if self.live else ts - pd.Timedelta(minutes=10)
            return pd.DataFrame(
                [
                    {
                        "last_heartbeat_at": hb,
                        "events_per_s": 412.0,
                        "last_event_at": hb,
                        "last_gap_start": ts - pd.Timedelta(hours=1),
                        "last_gap_minutes": 13,
                        "now": ts,
                    }
                ]
            )
        if "AS covered_s" in statement:
            return pd.DataFrame({"covered_s": [self.coverage_s]})
        if "c.coverage = 0" in statement:
            return pd.DataFrame({"minute_start": [ts.floor("min") - pd.Timedelta(minutes=20)]})
        if "AS punctuality" in statement:
            return pd.DataFrame(
                {
                    "route": ["10", "4", "4"],
                    "route_name": ["Ullanlinna - Pikku Huopalahti", "Katajanokka - Munkkiniemi", None],
                    "direction": ["1", "1", "2"],
                    "departures": [10, 20, 30],
                    "punctuality": [0.5, 0.75, 1.0],
                    "avg_lateness_s": [200.0, 30.0, -5.0],
                    "p90_lateness_s": [400, 120, 50],
                    "lateness_unknown": [0, 1, 0],
                }
            )
        if "bucket_start" in statement:
            return pd.DataFrame(
                {
                    "bucket_start": [ts.floor("5min") - pd.Timedelta(minutes=5)],
                    "departures": [12],
                    "on_time": [9],
                }
            )
        if "AS routes" in statement:
            return pd.DataFrame(
                {
                    "stop_id": ["1020455"],
                    "stop_name": ["Senaatintori"],
                    "lat": [60.169],
                    "long": [24.95],
                    "departures": [4],
                    "avg_lateness_s": [15.25],
                    "routes": ["4, 7"],
                }
            )
        if "silver_routes" in statement:
            return pd.DataFrame({"route": ["10", "4", "1"]})
        if "hsl_vehicle_type = 0" in statement:
            return pd.DataFrame({"stop_name": ["Rautatientori", "Kaivopuisto"]})
        if "bike_walk_or_wait(" in statement:
            return pd.DataFrame([ADVICE])
        if "tram_bunching(" in statement:
            return pd.DataFrame(BUNCHING_PAIRS)
        if "bunching_now(" in statement:
            return pd.DataFrame([BUNCHING_PAIRS[0]])
        raise AssertionError(f"unexpected query: {statement}")


ADVICE = {
    "choice": "wait", "reason": "the tram gets you there in 20 min; no bike: gusts up to 13 m/s are forecast",
    "from_stop": "Kaivopuisto", "to_stop": "Rautatientori", "tram_route": "3", "tram_direction": "1",
    "tram_departs_in_min": 2, "tram_arrives_in_min": 20, "tram_departure_realtime": True,
    "measured_lateness_s": None, "measured_age_s": None, "feed_live": False,
    "walk_arrives_in_min": 25, "bike_arrives_in_min": 16, "bike_station": "Laivasillankatu",
    "bikes_available": 4.0, "dock_station": "Porthania", "docks_free": 17.0, "precipitation_mm_h": 0.0,
    "rain_probability_pct": 4.0, "gust_ms": 12.9, "temperature_c": 12.4, "in_bike_season": True,
    "problems": "our HSL feed is not running, so measured Lateness is unknown",
}  # fmt: skip


BUNCHING_PAIRS = [
    {"route": "3", "direction": "2", "stop_id": "1", "stop_name": "Kaivopuisto", "stop_lat": 60.16,
     "stop_long": 24.95, "leader_vehicle_id": "40/75", "follower_vehicle_id": "40/81", "headway_s": 70,
     "scheduled_headway_s": 600, "leader_lateness_s": 300, "follower_lateness_s": -20, "bunching": True},
    {"route": "3", "direction": "2", "stop_id": "2", "stop_name": "Eira", "stop_lat": 60.157,
     "stop_long": 24.94, "leader_vehicle_id": "40/81", "follower_vehicle_id": "40/90", "headway_s": 580,
     "scheduled_headway_s": 600, "leader_lateness_s": -20, "follower_lateness_s": 10, "bunching": False},
]  # fmt: skip


def fake_invoke(messages, tools):
    """A model that calls bike_walk_or_wait once, then answers from the tool result."""
    if messages[-1]["role"] == "tool":
        return {"role": "assistant", "content": "Wait for the tram: gusts are too strong for a bike."}
    if "boom" in messages[-1]["content"]:
        raise RuntimeError("endpoint unavailable")
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "bike_walk_or_wait", "arguments": '{"from_stop": "Kaivopuisto", "to_stop": "Rautatientori"}'}}],
    }  # fmt: skip


@pytest.fixture
def run_app(monkeypatch):
    def run(warehouse=None, genie_space="space-1", viewer="rider@example.com"):
        warehouse = warehouse or FakeWarehouse()
        FakeLakebase.instances.clear()
        if viewer:
            monkeypatch.setenv("HSL_DEV_VIEWER_EMAIL", viewer)
        else:
            monkeypatch.delenv("HSL_DEV_VIEWER_EMAIL", raising=False)
        monkeypatch.setitem(sys.modules, "db", types.SimpleNamespace(Lakebase=FakeLakebase))
        monkeypatch.setitem(sys.modules, "warehouse", types.SimpleNamespace(Warehouse=lambda: warehouse))
        if genie_space:
            monkeypatch.setenv("GENIE_SPACE_ID", genie_space)
        else:
            monkeypatch.delenv("GENIE_SPACE_ID", raising=False)
        monkeypatch.setenv("AGENT_ENDPOINT", "fake-model")
        monkeypatch.syspath_prepend(str(APP_DIR))
        import agent

        monkeypatch.setattr(agent, "endpoint_invoke", lambda endpoint=None: fake_invoke)
        import streamlit as st
        from streamlit.testing.v1 import AppTest

        st.cache_data.clear()
        st.cache_resource.clear()
        at = AppTest.from_file(str(APP_DIR / "app.py"), default_timeout=30).run()
        assert not at.exception, [e.value for e in at.exception]
        return at, warehouse

    return run


def test_app_renders_without_exceptions(run_app):
    at, _ = run_app()
    assert not at.error, [e.value for e in at.error]
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Vehicles on map"] == "3"  # the 400 s old vehicle is hidden
    assert metrics["Fresh (≤ 30 s)"] == "2"
    assert metrics["Punctuality, all tram routes"] == "83%"  # (5 + 15 + 30) / 60
    assert metrics["Departures"] == "60"
    assert [t.label for t in at.tabs] == [
        "Live map", "Punctuality", "Stop Lateness", "Bunching", "Ask", "Bike, walk or wait"
    ]  # fmt: skip


def test_low_coverage_warns(run_app):
    at, _ = run_app(FakeWarehouse(coverage_s=60.0))
    assert any("Coverage of this window" in w.value for w in at.warning)


def test_on_time_slider_requeries(run_app):
    at, wh = run_app()
    at.slider(key="punct_on_time").set_value((-30, 120)).run()
    assert not at.exception
    sent = [p for q, p in wh.queries if "AS punctuality" in q]
    assert sent[-1]["early_s"] == -30 and sent[-1]["late_s"] == 120


def test_stopped_pipeline_warns_in_ask(run_app):
    at, _ = run_app(FakeWarehouse(live=False))
    assert any("pipeline is stopped" in w.value for w in at.warning)


def test_ask_shows_genie_answer_and_survives_errors(run_app, monkeypatch):
    import genie

    calls = []

    class FakeGenie:
        def ask(self, question, conversation_id=None):
            calls.append((question, conversation_id))
            if "boom" in question:
                raise RuntimeError("Genie timed out")
            return genie.Answer(
                conversation_id="conv-1",
                text="Tram 4 is on time.",
                sql="SELECT 1",
                table=pd.DataFrame({"route": ["4"], "lateness_s": [12]}),
            )

    monkeypatch.setattr(genie, "Genie", FakeGenie)
    at, _ = run_app()
    at.chat_input(key="ask_input").set_value("Is tram 4 on time right now?").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Tram 4 is on time." in m.value for m in at.markdown)
    at.chat_input(key="ask_input").set_value("boom").run()
    assert not at.exception
    assert any("Genie couldn't answer" in e.value for e in at.error)
    assert calls[1] == ("boom", "conv-1")  # follow-up stays in the conversation


def test_ask_without_genie_space(run_app):
    at, _ = run_app(genie_space=None)
    assert any("Ask isn't configured" in i.value for i in at.info)


def test_rider_report_is_written_with_hashed_reporter(run_app):
    at, _ = run_app()
    assert not at.error, [e.value for e in at.error]
    at.selectbox(key="report_route").set_value("3").run()
    at.radio(key="report_category").set_value("late").run()
    at.text_input(key="report_note").set_value("stuck at Kallio").run()
    at.button(key="report_send").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Report #42 saved" in s.value for s in at.success)
    db = FakeLakebase.instances[-1]
    reporter, category, note, vehicle = db.reports[-1]
    assert (category, note, vehicle) == ("late", "stuck at Kallio", "40/75")
    assert reporter != "rider@example.com" and len(reporter) == 64  # FR-13.3: SHA-256, never the email


def test_rate_limited_report_shows_warning(run_app):
    at, _ = run_app()
    FakeLakebase.instances[-1].reports_last_hour = 20
    at.button(key="report_send").click().run()
    assert any("limit" in w.value for w in at.warning)
    assert not FakeLakebase.instances[-1].reports


def test_report_note_is_escaped_in_tooltip(run_app):
    at, _ = run_app()
    decks = [d.proto.json for d in at.get("deck_gl_json_chart")]
    live = next(d for d in decks if "Rider Report" in d)
    assert "&lt;script&gt;" in live and "<script>" not in live  # viewer text never becomes HTML


def test_my_routes_strip_and_saving(run_app):
    at, _ = run_app()
    assert any("Route 3" in m.value for m in at.markdown)  # FR-14.2 strip for the saved route
    at.multiselect(key="my_routes_picker").set_value(["3", "M1"]).run()
    assert not at.exception, [e.value for e in at.exception]
    writes = [
        p for sql, p in FakeLakebase.instances[-1].executed if "INSERT INTO hsl_users.watched_routes" in sql
    ]
    assert ("rider@example.com", "M1") in writes


def test_anonymous_viewer_cannot_report(run_app):
    at, _ = run_app(viewer=None)
    assert any("Sign in through Databricks to send Rider Reports" in i.value for i in at.info)


def test_advice_form_shows_the_rule_choice(run_app):
    at, wh = run_app()
    at.selectbox(key="advice_from").set_value("Kaivopuisto")
    at.selectbox(key="advice_to").set_value("Rautatientori")
    next(b for b in at.button if b.label == "Advise me").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Wait for the tram" in m.value for m in at.markdown)
    assert any("unknown right now" in c.value for c in at.caption)  # FR-15.4
    sent = [p for q, p in wh.queries if "bike_walk_or_wait(" in q]
    assert sent[-1] == {"a": "Kaivopuisto", "b": "Rautatientori"}


def test_agent_chat_answers_and_survives_endpoint_errors(run_app):
    at, _ = run_app()
    at.chat_input(key="agent_input").set_value("I'm at Kaivopuisto, bike to Rautatientori?").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("gusts are too strong" in m.value for m in at.markdown)
    at.chat_input(key="agent_input").set_value("boom").run()
    assert not at.exception
    assert any("The agent couldn't answer" in e.value for e in at.error)


def test_bunching_tab_live_and_history(run_app):
    at, wh = run_app()
    assert not at.error, [e.value for e in at.error]
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Departures compared"] == "2" and metrics["Bunched departures"] == "1"
    assert metrics["Share bunched"] == "50.0%" and metrics["Tram pairs bunched"] == "1"
    assert any("bunching_now(" in q for q, _ in wh.queries)  # feed live: the "now" list is queried
    decks = [d.proto.json for d in at.get("deck_gl_json_chart")]
    assert any("LineLayer" in d for d in decks)  # leader 40/75 and follower 40/81 are both on the map


def test_bunching_now_waits_for_the_feed(run_app):
    at, wh = run_app(FakeWarehouse(live=False))
    assert any("no live Bunching" in i.value for i in at.info)
    assert not any("bunching_now(" in q for q, _ in wh.queries)


def test_bunching_share_slider_requeries(run_app):
    at, wh = run_app()
    at.slider(key="bunch_share").set_value(40).run()
    assert not at.exception
    sent = [p for q, p in wh.queries if "tram_bunching(" in q]
    assert sent[-1]["max_share"] == 0.4
