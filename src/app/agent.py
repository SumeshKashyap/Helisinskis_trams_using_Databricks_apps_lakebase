"""Bike, walk or wait agent (FR-15) and Bunching spotter (FR-16): a tool-calling loop on a Foundation Model API endpoint (ADR-0008).

The tools are the Unity Catalog functions from src/agents/functions.sql, run on the analytics SQL
Warehouse as the app's service principal, plus the FR-12 Genie space for history questions. The
wait/walk/bike choice comes from the `decide_trip` rule inside `bike_walk_or_wait`; the model only
picks tools and explains their results. scripts/setup_agents.py reuses SYSTEM_PROMPT for the
Supervisor Agent once that is available.
"""

import json
import os
from dataclasses import dataclass, field

import pandas as pd

MAX_STEPS = 6  # tool rounds per question before giving up
MAX_ROWS = 20  # rows of a tool result sent to the model
HISTORY = 12  # earlier user/assistant turns kept for follow-up questions

SYSTEM_PROMPT = """\
You help riders of Helsinki trams (HSL). Use these words: Stop (not station), Route (e.g. tram "4"),
Vehicle, Lateness (seconds behind schedule, positive = late; never call it "delay").

Should I wait, walk or bike?
- Call bike_walk_or_wait with the Stop the rider is at and the Stop they want to reach.
- Give the choice and the reason exactly as the tool returns them. Never change the choice: the rules
  behind it are fixed and tested. Then give the numbers behind it in a few short lines: when the tram
  leaves and arrives, walking and bike arrival, the city bike station (bikes available) and the dock
  station (free docks), and the weather for the next hour (rain mm/h, chance of rain %, gusts m/s,
  temperature).
- measured_lateness_s is our own measurement of that tram. If it is null, say our measured Lateness is
  unknown right now. Never say a tram is on time when Lateness is unknown.
- If problems is not empty, say what was missing.
- If the advice is to wait, call bunching_now with the tram's Route. If that Route has Bunching, add one
  line: on Route N two trams left <Stop> only H s apart, so another tram may come right after this one.

Bunching (two trams of a Route running almost together, leaving a long gap behind):
- "Are trams bunching?" or "Is another tram right behind?": bunching_now (Route or empty for all). The
  follower is the tram behind. If feed_live is false, our feed is not running: say Bunching is unknown
  right now, never "no Bunching". A row with feed_live true and no route means no Bunching now. Never
  claim the tram behind is emptier: HSL publishes no tram occupancy.
- If a Stop is not found or the rider names a place that is not a Stop, call find_stop and ask the
  rider to pick one.

Other questions:
- "Is tram N late right now?": tram_lateness. Only fresh or fading rows count as live; metro Lateness is
  not published by HSL.
- Weather only: weather_outlook (use find_stop for a Stop's coordinates).
- Questions about history, Punctuality or Rider Reports: punctuality_history.

Answer in a few short lines. Credit sources when you use them: weather from the Finnish Meteorological
Institute (CC BY 4.0), journey planner and city bikes from HSL Digitransit.
"""


def _tool(name, description, /, **params):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {k: {"type": t, "description": d} for k, (t, d) in params.items()},
                "required": list(params),
            },
        },
    }


TOOLS = [
    _tool(
        "bike_walk_or_wait",
        "Should a rider at a tram Stop wait for the tram, walk or take a city bike to another Stop? "
        "Returns the advice, the reason and the numbers behind it (tram times, measured Lateness, "
        "city bikes, weather).",
        from_stop=("string", 'Stop the rider is at, by name, e.g. "Kaivopuisto"'),
        to_stop=("string", 'Stop the rider wants to reach, by name, e.g. "Rautatientori"'),
    ),
    _tool(
        "tram_lateness",
        "Current Lateness of each Vehicle on a Route from our live HSL feed, with Freshness.",
        route=("string", 'Route as riders know it, e.g. "4" or "15"'),
    ),
    _tool(
        "weather_outlook",
        "FMI forecast (rain, chance of rain, wind, gusts, temperature) for the next two hours at a point.",
        lat=("number", "Latitude"),
        lon=("number", "Longitude"),
    ),
    _tool(
        "bunching_now",
        "Tram Bunching right now (last 5 minutes): pairs of trams on a Route leaving the same Stop with a "
        "Headway far below the planned frequency. The follower is the tram behind.",
        route=("string", 'Route as riders know it, e.g. "4"; empty string for all tram Routes'),
    ),
    _tool("find_stop", "Find HSL Stops by name, with coordinates.", name=("string", "Stop name or its start")),
    _tool(
        "punctuality_history",
        "Ask the Genie space about tram Punctuality, departures, Stop Lateness, Coverage and Rider "
        "Reports over the last 30 days. Pass one clear question.",
        question=("string", "The question for Genie"),
    ),
]

SQL = {
    "bike_walk_or_wait": "SELECT * FROM {p}.bike_walk_or_wait(:from_stop, :to_stop)",
    "tram_lateness": "SELECT * FROM {p}.tram_lateness(:route)",
    "weather_outlook": "SELECT * FROM {p}.weather_outlook(CAST(:lat AS DOUBLE), CAST(:lon AS DOUBLE))",
    "find_stop": "SELECT * FROM {p}.find_stop(:name)",
    "bunching_now": "SELECT * FROM {p}.bunching_now(:route)",
}


@dataclass
class ToolCall:
    name: str
    args: dict
    table: pd.DataFrame | None = None
    text: str = ""
    sql: str = ""
    error: str = ""


@dataclass
class Reply:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    error: str = ""


def message_text(message):
    """Text of an assistant message. Some models return a list of parts (reasoning + text)."""
    content = message.get("content")
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return content or ""


def to_records(df):
    return json.loads(df.head(MAX_ROWS).to_json(orient="records", date_format="iso"))


class Agent:
    """`invoke(messages, tools)` returns the endpoint's assistant message as a dict; `warehouse` has
    `query(sql, params)` and `prefix`; `genie` has `ask(question)` (may be None)."""

    def __init__(self, invoke, warehouse, genie=None):
        self.invoke = invoke
        self.warehouse = warehouse
        self.genie = genie

    def run_tool(self, name, args):
        call = ToolCall(name=name, args=args)
        try:
            if name in SQL:
                params = {k: args.get(k) for k in TOOLS_BY_NAME[name]}
                call.table = self.warehouse.query(SQL[name].format(p=self.warehouse.prefix), params)
                return call, {"rows": to_records(call.table), "row_count": len(call.table)}
            if name == "punctuality_history" and self.genie is not None:
                a = self.genie.ask(args.get("question", ""))
                call.text, call.sql, call.table, call.error = a.text or a.description, a.sql, a.table, a.error
                rows = to_records(a.table) if a.table is not None else []
                return call, {"answer": call.text, "rows": rows, "error": a.error or None}
            call.error = f"unknown tool {name}"
        except Exception as e:  # noqa: BLE001 - FR-15.6: the model gets the failure and says what's missing
            call.error = str(e)[:300]
        return call, {"error": call.error}

    def ask(self, question, history=()):
        """Answer one question. `history` is earlier [{"role", "content"}] turns (text only)."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *list(history)[-HISTORY:]]
        messages.append({"role": "user", "content": question})
        reply = Reply()
        try:
            for _ in range(MAX_STEPS):
                msg = self.invoke(messages, TOOLS)
                calls = msg.get("tool_calls") or []
                if not calls:
                    reply.text = message_text(msg)
                    return reply
                messages.append({"role": "assistant", "content": message_text(msg) or None, "tool_calls": calls})
                for c in calls:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    call, result = self.run_tool(c["function"]["name"], args)
                    reply.calls.append(call)
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result)})
            reply.error = "The agent needed too many steps; try a more specific question."
        except Exception as e:  # noqa: BLE001 - FR-15.6: an endpoint failure never breaks the app
            reply.error = str(e)[:300]
        return reply


TOOLS_BY_NAME = {t["function"]["name"]: list(t["function"]["parameters"]["properties"]) for t in TOOLS}


def endpoint_invoke(endpoint=None):
    """An `invoke` that calls a Foundation Model API endpoint as the app's service principal."""
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    name = endpoint or os.environ["AGENT_ENDPOINT"]

    def invoke(messages, tools):
        r = w.api_client.do(
            "POST",
            f"/serving-endpoints/{name}/invocations",
            body={"messages": messages, "tools": tools, "max_tokens": 1200, "temperature": 0},
        )
        return r["choices"][0]["message"]

    return invoke
