"""Tool-calling loop of the Bike, walk or wait agent (FR-15, ADR-0008), with a fake model and warehouse."""

import json
import sys
import types
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "app"))

import agent


class FakeWarehouse:
    prefix = "`cat`.`sch`"

    def __init__(self, fail=False):
        self.fail = fail
        self.queries = []

    def query(self, statement, params=None):
        self.queries.append((statement, params))
        if self.fail:
            raise RuntimeError("warehouse is down")
        return pd.DataFrame([{"choice": "wait", "reason": "rain is forecast", "at": pd.Timestamp("2026-10-08")}])


def call(name, args, id_="c1"):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def scripted(*replies):
    """A model that returns the given assistant messages in turn and records what it was sent."""
    seen = []

    def invoke(messages, tools):
        seen.append([dict(m) for m in messages])
        return replies[len(seen) - 1]

    return invoke, seen


def test_tool_call_then_answer():
    invoke, seen = scripted(
        {"role": "assistant", "content": None,
         "tool_calls": [call("bike_walk_or_wait", {"from_stop": "Kaivopuisto", "to_stop": "Rautatientori"})]},
        {"role": "assistant", "content": "Wait: rain is forecast."},
    )  # fmt: skip
    wh = FakeWarehouse()
    r = agent.Agent(invoke, wh).ask("Bike from Kaivopuisto to Rautatientori?")
    assert r.text == "Wait: rain is forecast." and not r.error
    assert wh.queries == [
        ("SELECT * FROM `cat`.`sch`.bike_walk_or_wait(:from_stop, :to_stop)",
         {"from_stop": "Kaivopuisto", "to_stop": "Rautatientori"})
    ]  # fmt: skip
    tool_msg = seen[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert json.loads(tool_msg["content"])["rows"][0]["choice"] == "wait"
    assert [c.name for c in r.calls] == ["bike_walk_or_wait"]


def test_system_prompt_and_history_are_sent():
    invoke, seen = scripted({"role": "assistant", "content": "Hi"})
    history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "answer"}]
    agent.Agent(invoke, FakeWarehouse()).ask("now?", history)
    sent = seen[0]
    assert sent[0]["role"] == "system" and "Never change the choice" in sent[0]["content"]
    assert [m["content"] for m in sent[1:]] == ["earlier", "answer", "now?"]


def test_only_declared_arguments_reach_sql():
    invoke, _ = scripted(
        {"role": "assistant", "content": None, "tool_calls": [call("tram_lateness", {"route": "4", "x": "DROP"})]},
        {"role": "assistant", "content": "ok"},
    )
    wh = FakeWarehouse()
    agent.Agent(invoke, wh).ask("tram 4?")
    assert wh.queries[0][1] == {"route": "4"}


def test_tool_failure_is_passed_to_the_model():
    invoke, seen = scripted(
        {"role": "assistant", "content": None, "tool_calls": [call("find_stop", {"name": "Kallio"})]},
        {"role": "assistant", "content": "The Stop lookup failed."},
    )
    r = agent.Agent(invoke, FakeWarehouse(fail=True)).ask("where is Kallio?")
    assert json.loads(seen[1][-1]["content"]) == {"error": "warehouse is down"}
    assert r.calls[0].error == "warehouse is down" and r.text == "The Stop lookup failed."


def test_unknown_tool_and_bad_arguments_do_not_crash():
    bad = {"id": "c9", "type": "function", "function": {"name": "find_stop", "arguments": "{not json"}}
    invoke, _ = scripted(
        {"role": "assistant", "content": None, "tool_calls": [call("drop_tables", {}), bad]},
        {"role": "assistant", "content": "done"},
    )
    r = agent.Agent(invoke, FakeWarehouse()).ask("?")
    assert r.calls[0].error == "unknown tool drop_tables"
    assert r.calls[1].args == {} and r.text == "done"


def test_genie_tool():
    answer = types.SimpleNamespace(text="Route 4 was worst.", description="", sql="SELECT 1",
                                   table=pd.DataFrame({"route": ["4"]}), error="")
    genie = types.SimpleNamespace(ask=lambda q: answer)
    invoke, seen = scripted(
        {"role": "assistant", "content": None, "tool_calls": [call("punctuality_history", {"question": "worst?"})]},
        {"role": "assistant", "content": "Route 4."},
    )
    r = agent.Agent(invoke, FakeWarehouse(), genie).ask("worst route today?")
    assert json.loads(seen[1][-1]["content"])["answer"] == "Route 4 was worst."
    assert r.calls[0].sql == "SELECT 1"


def test_step_limit():
    loop = {"role": "assistant", "content": None, "tool_calls": [call("find_stop", {"name": "A"})]}
    invoke, _ = scripted(*[loop] * agent.MAX_STEPS)
    r = agent.Agent(invoke, FakeWarehouse()).ask("?")
    assert "too many steps" in r.error and len(r.calls) == agent.MAX_STEPS


def test_endpoint_error_becomes_reply_error():
    def invoke(messages, tools):
        raise RuntimeError("rate limit of 0")

    r = agent.Agent(invoke, FakeWarehouse()).ask("?")
    assert r.error == "rate limit of 0" and not r.text


def test_message_text_handles_content_parts():
    msg = {"content": [{"type": "reasoning", "summary": []}, {"type": "text", "text": "Walk."}]}
    assert agent.message_text(msg) == "Walk."
    assert agent.message_text({"content": None}) == ""
