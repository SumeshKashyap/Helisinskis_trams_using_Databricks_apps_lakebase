"""HSL Live Transit: live map (FR-7), punctuality board (FR-8), stop lateness (FR-9),
health strip (FR-10), Ask (FR-12), Rider Reports (FR-13), My Routes (FR-14) and the Bike, walk or
wait agent (FR-15) for Helsinki trams and metro.

The live map reads Lakebase; analytics read Delta through the SQL Warehouse (ADR-0003). Rider
Reports and My Routes are written to app-owned Lakebase tables (ADR-0006).
"""

import datetime as dt
import html
import math
import os
import types
from zoneinfo import ZoneInfo

import altair as alt
import analytics as an
import pandas as pd
import pydeck as pdk
import streamlit as st
from db import Lakebase
from map_style import (
    BANDS,
    HIDDEN_S,
    ON_TIME_EARLY_S,
    ON_TIME_LATE_S,
    format_lateness,
    freshness_alpha,
    lateness_band,
)
from store import CATEGORIES, RECENT_MIN, ReportRejected, Store

st.set_page_config(page_title="HSL Live Transit", page_icon="🚋", layout="wide")

HELSINKI = ZoneInfo("Europe/Helsinki")
REFRESH_S = 4  # FR-7.1: 3–5 s
HEALTH_REFRESH_S = 30
ANALYTICS_TTL_S = 60
TABLE = f'"{os.getenv("LAKEBASE_SCHEMA", "hsl_live_transit")}"."gold_vehicle_current_online"'
VEHICLES_SQL = f"""
    SELECT vehicle_id, mode, route, direction, lat, long, heading, lateness_s, last_seen_at,
           extract(epoch FROM now() - last_seen_at)::float AS age_s
    FROM {TABLE}
    WHERE last_seen_at > now() - make_interval(secs => %s)
"""
MAP_VIEW = pdk.ViewState(latitude=60.185, longitude=24.94, zoom=11.5)


@st.cache_resource
def lakebase():
    return Lakebase()


@st.cache_resource
def store():
    """(Store, error text). Creates the app-owned schemas once per app process (FR-13, FR-14)."""
    s = Store(lakebase(), TABLE)
    try:
        s.ensure_schema()
    except Exception as e:  # noqa: BLE001 - reports and My Routes degrade; the map keeps working
        return s, str(e)
    return s, ""


def viewer_email():
    """The signed-in viewer from the Apps proxy (FR-13.7). HSL_DEV_VIEWER_EMAIL is for local runs."""
    try:
        email = st.context.headers.get("X-Forwarded-Email")
    except Exception:  # noqa: BLE001
        email = None
    return email or os.getenv("HSL_DEV_VIEWER_EMAIL") or None


@st.cache_resource
def warehouse():
    from warehouse import Warehouse

    return Warehouse()


@st.cache_resource
def genie():
    from genie import Genie

    return Genie()


@st.cache_resource
def agent():
    from agent import Agent, endpoint_invoke

    # Genie is created on its first use, so the agent works without a Genie space.
    history = types.SimpleNamespace(ask=lambda question: genie().ask(question))
    return Agent(endpoint_invoke(), warehouse(), history if os.getenv("GENIE_SPACE_ID") else None)


def utc_now():
    return dt.datetime.now(dt.UTC)


def local_hms(ts):
    return f"{pd.Timestamp(ts).tz_convert(HELSINKI):%H:%M:%S}"


def ago(seconds):
    if seconds is None or pd.isna(seconds):
        return "–"
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds} s ago"
    if seconds < 7200:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def band_legend(caption):
    items = "".join(
        f'<span style="display:inline-flex;align-items:center;margin-right:14px">'
        f'<span style="width:11px;height:11px;border-radius:50%;background:rgb{rgb};'
        f'display:inline-block;margin-right:5px"></span>{label}</span>'
        for label, rgb in BANDS.values()
    )
    st.markdown(f'<div style="font-size:0.85rem">{items}</div>', unsafe_allow_html=True)
    st.caption(caption)


# ---------------------------------------------------------------- FR-10 health strip


@st.cache_data(ttl=HEALTH_REFRESH_S - 5, show_spinner=False)
def load_health():
    df = warehouse().query(an.health_sql(warehouse().table))
    return df.iloc[0].to_dict() if not df.empty else {}


def health_state():
    """(health dict or None, error text). Never raises: the strip must not break the app."""
    try:
        return load_health(), ""
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def is_live(h):
    hb, now = h.get("last_heartbeat_at"), h.get("now")
    return hb is not None and not pd.isna(hb) and (now - hb).total_seconds() <= an.LIVE_HEARTBEAT_S


@st.fragment(run_every=HEALTH_REFRESH_S)
def health_strip():
    h, err = health_state()
    if h is None:
        st.caption(f"⚪ Health unavailable: the SQL Warehouse didn't answer ({err[:120]}).")
        return
    now = h["now"]
    live = is_live(h)
    state = "🟢 **Live**" if live else "🔴 **Stopped**"
    if not live and h.get("last_heartbeat_at") is not None and not pd.isna(h["last_heartbeat_at"]):
        state += f" since {local_hms(h['last_heartbeat_at'])}"
    eps = h.get("events_per_s")
    rate = f"{eps:,.0f} events/s" if live and eps is not None and not pd.isna(eps) else "– events/s"
    last_event = h.get("last_event_at")
    since_event = None if last_event is None or pd.isna(last_event) else (now - last_event).total_seconds()
    gap = "no Data Gap recorded"
    if h.get("last_gap_start") is not None and not pd.isna(h["last_gap_start"]):
        start = pd.Timestamp(h["last_gap_start"]).tz_convert(HELSINKI)
        gap = f"last Data Gap {start:%a %H:%M}, {int(h['last_gap_minutes'])} min"
    st.markdown(
        f'<div style="font-size:0.85rem;padding:4px 10px;border-radius:6px;background:rgba(128,128,128,0.1)">'
        f"{state} &nbsp;·&nbsp; {rate} &nbsp;·&nbsp; last event {ago(since_event)} &nbsp;·&nbsp; {gap}</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- FR-7 live map


def load_vehicles():
    cols, rows = lakebase().query(VEHICLES_SQL, (HIDDEN_S,))
    return pd.DataFrame(rows, columns=cols)


def style(df):
    df = df.copy()
    df["alpha"] = df["age_s"].map(freshness_alpha)
    df = df[df["alpha"].notna()]
    df["band"] = df["lateness_s"].map(lambda v: lateness_band(None if pd.isna(v) else v))
    df["color"] = [list(BANDS[b][1]) + [int(a)] for b, a in zip(df["band"], df["alpha"])]
    df["lateness_text"] = df["lateness_s"].map(lambda v: format_lateness(None if pd.isna(v) else v))
    df["last_seen_text"] = df["last_seen_at"].map(local_hms)
    df["age_text"] = df["age_s"].map(lambda s: f"{s:.0f} s ago")
    df["mode_label"] = df["mode"].str.capitalize()
    return df


def load_reports():
    st_, err = store()
    if err:
        return pd.DataFrame()
    try:
        cols, rows = st_.recent_reports()
    except Exception:  # noqa: BLE001 - the map must not fail because of reports
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=cols)


def vehicle_tip(df):
    return [
        f"<b>{m} {r}</b> · direction {d}<br/>Vehicle {v}<br/>Lateness: {lt}<br/>Last seen {ls} ({ag})"
        for m, r, d, v, lt, ls, ag in zip(
            df["mode_label"],
            df["route"],
            df["direction"],
            df["vehicle_id"],
            df["lateness_text"],
            df["last_seen_text"],
            df["age_text"],
        )
    ]


def report_tip(reports):
    """Notes are viewer text: escape them before they reach the tooltip HTML."""
    tips = []
    for r in reports.itertuples():
        measured = format_lateness(None if pd.isna(r.measured_lateness_s) else r.measured_lateness_s)
        note = f"<br/>“{html.escape(r.note)}”" if isinstance(r.note, str) and r.note else ""
        tips.append(
            f"<b>Rider Report: {CATEGORIES.get(r.category, r.category)}</b><br/>"
            f"{r.mode.capitalize()} {html.escape(r.route)} · vehicle {html.escape(r.vehicle_id)}<br/>"
            f"{ago(r.age_s)} · measured Lateness then: {measured}{note}"
        )
    return tips


def my_routes_strip(all_vehicles, reports, watched):
    """FR-14.2: per watched Route, vehicles now, worst current Lateness, recent Rider Reports."""
    if not watched:
        return
    summary = an.my_routes_summary(all_vehicles, reports, watched)
    cols = st.columns(min(len(summary), 6))
    for i, row in enumerate(summary):
        with cols[i % len(cols)]:
            if row["vehicles"] == 0:
                worst = "no vehicles now"
            elif row["worst_lateness_s"] is None:
                worst = "Lateness not published"
            else:
                worst = f"worst {format_lateness(row['worst_lateness_s'])}"
            st.markdown(
                f'<div style="font-size:0.85rem;padding:6px 10px;border-radius:6px;'
                f'background:rgba(128,128,128,0.1)"><b>Route {html.escape(row["route"])}</b> · '
                f"{row['vehicles']} on map<br/>{worst}<br/>{row['reports']} Rider Reports, last {RECENT_MIN} min</div>",
                unsafe_allow_html=True,
            )


@st.fragment(run_every=REFRESH_S)
def live_map(modes, routes, watched=(), only_mine=False):
    try:
        df = load_vehicles()
    except Exception as e:  # noqa: BLE001 - show the reason instead of a blank map
        st.error(f"Could not read the live table from Lakebase: {e}")
        return
    reports = load_reports()
    all_vehicles = df

    route_filter = {r.strip() for r in routes.split(",") if r.strip()}
    if only_mine and watched:
        route_filter = set(watched) if not route_filter else route_filter & set(watched)
    if not df.empty:
        df = df[df["mode"].isin(modes)]
        if route_filter:
            df = df[df["route"].isin(route_filter)]
        df = style(df)
        df["tip"] = vehicle_tip(df)
    if not reports.empty and route_filter:
        reports = reports[reports["route"].isin(route_filter)]

    my_routes_strip(all_vehicles, reports, list(watched))

    fresh = int((df["age_s"] <= 30).sum()) if not df.empty else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Vehicles on map", len(df))
    c2.metric("Fresh (≤ 30 s)", fresh)
    c3.metric(
        "Newest Position Event",
        f"{df['age_s'].min():.0f} s ago" if not df.empty else "–",
    )
    if df.empty:
        st.info("No vehicle has reported in the last 5 minutes. Is the pipeline running?")

    layers = [
        pdk.Layer(
            "ScatterplotLayer",
            data=df,
            get_position=["long", "lat"],
            get_fill_color="color",
            get_radius=45,
            radius_min_pixels=4,
            radius_max_pixels=12,
            stroked=True,
            get_line_color=[255, 255, 255, 160],
            line_width_min_pixels=1,
            pickable=True,
        )
    ]
    reports = reports.dropna(subset=["lat", "long"]) if not reports.empty else reports
    if not reports.empty:
        # FR-13.5: rings where riders reported a problem in the last 30 minutes.
        # Only position and the escaped tooltip go to the browser, never the raw note.
        report_points = reports[["lat", "long"]].assign(tip=report_tip(reports))
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=report_points,
                get_position=["long", "lat"],
                get_radius=110,
                radius_min_pixels=9,
                radius_max_pixels=20,
                filled=True,
                get_fill_color=[123, 31, 162, 40],
                stroked=True,
                get_line_color=[123, 31, 162, 230],
                line_width_min_pixels=2,
                pickable=True,
            )
        )
    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=MAP_VIEW,
            map_style=pdk.map_styles.CARTO_LIGHT,
            tooltip={"html": "{tip}"},
            height=620,
        ),
        # Only arguments the Apps runtime's Streamlit 1.38 accepts (no height= there).
        use_container_width=True,
    )
    band_legend(
        "Fully opaque if seen in the last 30 s, fading until 5 min, then hidden. "
        "Metro has no Lateness: HSL doesn't publish it. "
        f"Purple rings: Rider Reports from the last {RECENT_MIN} minutes."
    )


# ---------------------------------------------------------------- FR-13 Rider Reports


@st.fragment
def report_panel():
    """Its own fragment: picking a route or vehicle reruns only this panel, not the analytics tabs."""
    st_, err = store()
    left, right = st.columns([2, 3])
    with left:
        st.markdown("**Report a problem**")
        email = viewer_email()
        if err:
            st.error(f"Rider Reports are unavailable: Lakebase said {err[:200]}")
        elif not email:
            st.info("Sign in through Databricks to send Rider Reports.")
        else:
            report_form(st_, email)
    with right:
        st.markdown(f"**Rider Reports, last {RECENT_MIN} min**")
        reports = load_reports()
        if reports.empty:
            st.caption("No Rider Reports yet.")
        else:
            show = reports.head(15)
            st.dataframe(
                pd.DataFrame(
                    {
                        "When": [ago(a) for a in show["age_s"]],
                        "Route": show["route"],
                        "Vehicle": show["vehicle_id"],
                        "Report": [CATEGORIES.get(c, c) for c in show["category"]],
                        "Measured then": [
                            format_lateness(None if pd.isna(v) else v) for v in show["measured_lateness_s"]
                        ],
                        "Note": show["note"].fillna(""),
                    }
                ),
                hide_index=True,
                use_container_width=True,
            )
        st.caption("Rider Reports are what riders say, never mixed into measured Lateness or Punctuality.")


def report_form(st_, email):
    try:
        vehicles = load_vehicles()
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read vehicles: {e}")
        return
    vehicles = vehicles[vehicles["age_s"] <= HIDDEN_S] if not vehicles.empty else vehicles
    if vehicles.empty:
        st.caption("No vehicles on the map right now, so there's nothing to report on.")
        return
    routes = sorted(vehicles["route"].unique(), key=an.route_sort_key)
    route = st.selectbox("Route", routes, key="report_route")
    on_route = vehicles[vehicles["route"] == route].sort_values(["direction", "vehicle_id"])
    labels = {
        v.vehicle_id: f"{v.vehicle_id} · direction {v.direction} · "
        f"{format_lateness(None if pd.isna(v.lateness_s) else v.lateness_s)}"
        for v in on_route.itertuples()
    }
    vehicle = st.selectbox("Vehicle", list(labels), format_func=labels.get, key="report_vehicle")
    category = st.radio(
        "What's wrong?", list(CATEGORIES), format_func=CATEGORIES.get, horizontal=True, key="report_category"
    )
    note = st.text_input("Note (optional)", max_chars=280, key="report_note")
    if st.button("Send report", type="primary", key="report_send"):
        try:
            report_id = st_.add_report(email, vehicle, category, note)
        except ReportRejected as e:
            st.warning(str(e))
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not save the report: {e}")
        else:
            st.success(f"Thanks! Report #{report_id} saved. It shows on the map within seconds.")


# ---------------------------------------------------------------- FR-14 My Routes


def load_watched(email):
    """Cached per session: My Routes are read from Lakebase once, then kept in session state."""
    if "my_routes" not in st.session_state:
        st_, err = store()
        try:
            st.session_state["my_routes"] = [] if err else st_.watched_routes(email)
        except Exception:  # noqa: BLE001
            st.session_state["my_routes"] = []
    return st.session_state["my_routes"]


def save_watched(email):
    st_, err = store()
    picked = st.session_state.get("my_routes_picker", [])
    if err:
        st.session_state["my_routes_error"] = err
        return
    try:
        st.session_state["my_routes"] = st_.set_watched_routes(email, picked)
        st.session_state.pop("my_routes_error", None)
    except Exception as e:  # noqa: BLE001
        st.session_state["my_routes_error"] = str(e)


def route_options(saved):
    try:
        _, rows = lakebase().query(f"SELECT DISTINCT route FROM {TABLE}")
        known = {r[0] for r in rows}
    except Exception:  # noqa: BLE001
        known = set()
    return sorted(known | set(saved), key=an.route_sort_key)


# ---------------------------------------------------------------- shared analytics helpers


def window_picker(key):
    return st.radio(
        "Window",
        list(an.WINDOWS),
        format_func=an.WINDOWS.get,
        horizontal=True,
        key=f"{key}_window",
    )


def on_time_picker(key):
    # FR-8.2: viewer-adjustable On-time window; changing it re-queries.
    return st.slider(
        "On-time window (seconds, negative = early)",
        min_value=-600,
        max_value=900,
        value=(ON_TIME_EARLY_S, ON_TIME_LATE_S),
        step=15,
        key=f"{key}_on_time",
    )


def now_minute():
    """Analytics are cached per minute: re-running inside a minute reuses the result."""
    return utc_now().replace(second=0, microsecond=0)


@st.cache_data(ttl=ANALYTICS_TTL_S, show_spinner=False)
def load_coverage(window, now):
    since, until, _ = an.window_bounds(window, now)
    wh = warehouse()
    covered = wh.query(an.coverage_sql(wh.table), {"since": since, "until": until})
    gaps = wh.query(an.gap_minutes_sql(wh.table), {"since": since, "until": until})
    minutes = [pd.Timestamp(m).to_pydatetime() for m in gaps["minute_start"]] if not gaps.empty else []
    return an.coverage_fraction(float(covered["covered_s"].iloc[0]), since, until), an.gap_intervals(minutes)


def coverage_note(window, now):
    """FR-8.3 / FR-9.3: every window shows its Coverage, with a warning below 90 %."""
    coverage, gaps = load_coverage(window, now)
    if coverage is None:
        return gaps
    text = f"Coverage of this window: **{coverage:.0%}**"
    if coverage < an.COVERAGE_WARNING_BELOW:
        st.warning(
            f"{text}. The feed wasn't recorded for part of this window, so these numbers miss "
            "departures from the Data Gaps. A gap is missing data, not missing trams."
        )
    else:
        st.caption(text)
    return gaps


# ---------------------------------------------------------------- FR-8 punctuality board


@st.cache_data(ttl=ANALYTICS_TTL_S, show_spinner=False)
def load_punctuality(window, now, early_s, late_s):
    wh = warehouse()
    flt, params = an.window_params(window, now)
    df = wh.query(
        an.punctuality_sql(wh.table, flt),
        {**params, "early_s": early_s, "late_s": late_s},
    )
    chart = wh.query(
        an.departures_over_time_sql(wh.table, flt),
        {**params, "early_s": early_s, "late_s": late_s},
    )
    return df, chart


def departures_chart(chart, gaps, now, window):
    """FR-10.2: departures over event time, with Data Gaps shaded."""
    since, until, _ = an.window_bounds(window, now)
    to_local = lambda t: pd.Timestamp(t).tz_convert(HELSINKI).tz_localize(None)
    bars = pd.DataFrame(
        {
            "time": [to_local(t) for t in chart["bucket_start"]],
            "On-time": chart["on_time"].astype(int),
            "Not on-time": (chart["departures"] - chart["on_time"]).astype(int),
        }
    ).melt("time", var_name="Departures", value_name="count")
    gap_df = pd.DataFrame({"start": [to_local(a) for a, _ in gaps], "end": [to_local(b) for _, b in gaps]})
    time_scale = alt.Scale(domain=[to_local(since), to_local(until)])
    x = alt.X("time:T", title="Helsinki time", scale=time_scale)
    layers = []
    if not gap_df.empty:
        layers.append(
            alt.Chart(gap_df)
            .mark_rect(color="#9e9e9e", opacity=0.25)
            .encode(
                x=alt.X("start:T", scale=time_scale),
                x2="end:T",
                tooltip=[
                    alt.Tooltip("start:T", title="Data Gap from", format="%H:%M"),
                    alt.Tooltip("end:T", title="to", format="%H:%M"),
                ],
            )
        )
    layers.append(
        alt.Chart(bars)
        .mark_bar()
        .encode(
            x=x,
            y=alt.Y("count:Q", stack=True, title=f"Departures per {an.CHART_BUCKET_MIN} min"),
            color=alt.Color(
                "Departures:N",
                scale=alt.Scale(domain=["On-time", "Not on-time"], range=["#2ca02c", "#ff9800"]),
            ),
            tooltip=["time:T", "Departures:N", "count:Q"],
        )
    )
    st.altair_chart(alt.layer(*layers).properties(height=220), use_container_width=True)
    st.caption("Grey bands are Data Gaps: the feed wasn't recorded then.")


def punctuality_tab():
    st.subheader("Route punctuality (trams)")
    c1, c2 = st.columns([1, 2])
    with c1:
        window = window_picker("punct")
    with c2:
        early_s, late_s = on_time_picker("punct")
    now = now_minute()
    try:
        gaps = coverage_note(window, now)
        df, chart = load_punctuality(window, now, early_s, late_s)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not query the SQL Warehouse: {e}")
        return
    if df.empty:
        st.info("No tram departures in this window. Start the pipeline, or pick a longer window.")
        return

    total = int(df["departures"].sum())
    on_time = float((df["punctuality"] * df["departures"]).sum()) / total
    m1, m2, m3 = st.columns(3)
    m1.metric("Punctuality, all tram routes", f"{on_time:.0%}")
    m2.metric("Departures", f"{total:,}")
    m3.metric("Routes", df["route"].nunique())

    df = (
        df.assign(_order=df["route"].map(an.route_sort_key))
        .sort_values(["_order", "direction"])
        .drop(columns="_order")
    )
    st.dataframe(
        df.assign(punctuality=df["punctuality"] * 100),
        hide_index=True,
        use_container_width=True,
        column_order=[
            "route",
            "direction",
            "route_name",
            "punctuality",
            "departures",
            "avg_lateness_s",
            "p90_lateness_s",
            "lateness_unknown",
        ],
        column_config={
            "route": "Route",
            "direction": "Direction",
            "route_name": "Route name",
            "punctuality": st.column_config.ProgressColumn(
                "Punctuality", format="%.0f%%", min_value=0, max_value=100
            ),
            "departures": "Departures",
            "avg_lateness_s": st.column_config.NumberColumn("Avg Lateness (s)", format="%.0f"),
            "p90_lateness_s": st.column_config.NumberColumn("p90 Lateness (s)", format="%.0f"),
            "lateness_unknown": st.column_config.NumberColumn(
                "Lateness unknown", help="Counted as not on-time."
            ),
        },
    )
    st.caption(
        f"On-time = departed between {early_s} s and +{late_s} s of schedule. Positive Lateness = late. "
        "Metro isn't here: HSL publishes no metro Lateness or Stop Events."
    )
    departures_chart(chart, gaps, now, window)


# ---------------------------------------------------------------- FR-9 stop-level lateness


@st.cache_data(ttl=ANALYTICS_TTL_S, show_spinner=False)
def load_stops(window, now, route):
    wh = warehouse()
    flt, params = an.window_params(window, now)
    if route:
        params = {**params, "route": route}
    return wh.query(an.stop_lateness_sql(wh.table, flt, by_route=bool(route)), params)


@st.cache_data(ttl=3600, show_spinner=False)
def load_tram_routes():
    wh = warehouse()
    df = wh.query(f"SELECT DISTINCT r.route FROM {wh.table('silver_routes')} r WHERE r.mode = 'tram'")
    return sorted(df["route"], key=an.route_sort_key)


def stops_tab():
    st.subheader("Stop-level Lateness (trams)")
    c1, c2 = st.columns([1, 2])
    with c1:
        window = window_picker("stops")
    with c2:
        try:
            routes = load_tram_routes()
        except Exception:  # noqa: BLE001
            routes = []
        route = st.selectbox("Route", ["All routes", *routes], key="stops_route")
    route = None if route == "All routes" else route
    now = now_minute()
    try:
        coverage_note(window, now)
        df = load_stops(window, now, route)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not query the SQL Warehouse: {e}")
        return
    if df.empty:
        st.info("No tram departures in this window.")
        return

    df = df.copy()
    df["band"] = df["avg_lateness_s"].map(lambda v: lateness_band(None if pd.isna(v) else v))
    df["color"] = [list(BANDS[b][1]) + [210] for b in df["band"]]
    df["radius"] = df["departures"].map(lambda n: 25 + 18 * math.sqrt(n))
    df["lateness_text"] = df["avg_lateness_s"].map(lambda v: format_lateness(None if pd.isna(v) else v))
    layer = pdk.Layer(
        "ScatterplotLayer",
        data=df,
        get_position=["long", "lat"],
        get_fill_color="color",
        get_radius="radius",
        radius_min_pixels=3,
        radius_max_pixels=22,
        pickable=True,
    )
    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=MAP_VIEW,
            map_style=pdk.map_styles.CARTO_LIGHT,
            tooltip={
                "html": "<b>{stop_name}</b><br/>Routes {routes}<br/>"
                "{departures} departures<br/>Average Lateness: {lateness_text}",
            },
        ),
        use_container_width=True,
    )
    band_legend("Colour = average departure Lateness at the stop; size = number of departures.")


# ---------------------------------------------------------------- FR-12 Ask (Genie)

EXAMPLES = [
    "Is tram 4 on time right now?",
    "Which tram routes were most late today?",
    "Which stops had the worst Lateness in the last hour?",
]


def show_answer(a):
    if a.error:
        st.error(f"Genie couldn't answer: {a.error}")
        return
    if a.text:
        st.markdown(a.text)
    if a.description and a.description != a.text:
        st.caption(a.description)
    if a.table is not None and not a.table.empty:
        st.dataframe(a.table, hide_index=True, use_container_width=True)
    if a.sql:
        with st.expander("SQL Genie ran"):  # FR-12.4: show how the answer was made
            st.code(a.sql, language="sql")


def ask_tab():
    st.subheader("Ask about trams and metro")
    st.caption(
        "Answers come from Genie over the gold tables through the SQL Warehouse, so they're a few "
        "seconds behind the live map. Positive Lateness = late. Metro Lateness isn't published by HSL."
    )
    if not os.getenv("GENIE_SPACE_ID"):
        st.info("Ask isn't configured: the app has no Genie space resource.")
        return
    h, _ = health_state()
    if h is not None and not is_live(h):
        st.warning(
            'The pipeline is stopped, so answers about "right now" may be stale. Check the Last seen times.'
        )

    chat = st.session_state.setdefault("ask_history", [])
    for role, content in chat:
        with st.chat_message(role):
            if role == "assistant":
                show_answer(content)
            else:
                st.markdown(content)

    cols = st.columns(len(EXAMPLES))
    picked = next((q for c, q in zip(cols, EXAMPLES) if c.button(q, key=f"ex_{q}")), None)
    question = st.chat_input("Ask a question, e.g. Is tram 9 late?", key="ask_input") or picked
    if not question:
        return

    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Genie is writing and running SQL…"):
            try:
                answer = genie().ask(question, st.session_state.get("ask_conversation"))
            except Exception as e:  # noqa: BLE001 - FR-12.6: a Genie failure never breaks the app
                from genie import Answer

                answer = Answer(conversation_id=None, error=str(e))
        show_answer(answer)
    if answer.conversation_id:
        st.session_state["ask_conversation"] = answer.conversation_id
    chat.extend([("user", question), ("assistant", answer)])


# ---------------------------------------------------------------- FR-15 Bike, walk or wait

CHOICE_LABEL = {"wait": "🚋 Wait for the tram", "walk": "🚶 Walk", "bike": "🚲 Take a city bike", "none": "🤷 No advice"}
AGENT_EXAMPLES = [
    "I'm at Kaivopuisto going to Rautatientori. Should I take a city bike?",
    "Is tram 4 late right now?",
    "Will it rain at Hakaniemi in the next hour?",
]


@st.cache_data(ttl=3600, show_spinner=False)
def load_tram_stop_names():
    wh = warehouse()
    df = wh.query(f"SELECT DISTINCT stop_name FROM {wh.table('silver_stops')} WHERE hsl_vehicle_type = 0")
    return sorted(df["stop_name"])


def minutes(v):
    return "–" if v is None or pd.isna(v) else f"{int(v)} min"


def show_advice(row):
    """The bike_walk_or_wait result as a card (FR-15.1, FR-15.4)."""
    st.markdown(f"### {CHOICE_LABEL.get(row['choice'], row['choice'])}")
    st.write(row["reason"].capitalize())
    c1, c2, c3 = st.columns(3)
    tram = f"Tram {row['tram_route']}" if row["tram_route"] else "Tram"
    c1.metric(f"{tram}: arrive in", minutes(row["tram_arrives_in_min"]),
              help=f"Leaves in {minutes(row['tram_departs_in_min'])} (HSL real-time: "
              f"{'yes' if row['tram_departure_realtime'] else 'no'}).")
    c2.metric("Walk: arrive in", minutes(row["walk_arrives_in_min"]))
    c3.metric("City bike: arrive in", minutes(row["bike_arrives_in_min"]),
              help="Off season or no free bike or dock nearby." if pd.isna(row["bike_arrives_in_min"]) else None)
    lateness = row["measured_lateness_s"]
    if lateness is None or pd.isna(lateness):
        st.caption("Our measured Lateness for this tram: unknown right now (FR-15.4).")
    else:
        st.caption(f"Our measured Lateness for this tram: {int(lateness):+d} s "
                   f"(seen {ago(row['measured_age_s'])}).")
    if row["bike_station"]:
        st.caption(f"Bike from {row['bike_station']} ({int(row['bikes_available'])} bikes) "
                   f"to {row['dock_station']} ({int(row['docks_free'])} free docks).")
    if row["precipitation_mm_h"] is not None and not pd.isna(row["precipitation_mm_h"]):
        st.caption(f"Next hour: rain {row['precipitation_mm_h']:.1f} mm/h, chance of rain "
                   f"{row['rain_probability_pct']:.0f} %, gusts {row['gust_ms']:.0f} m/s, "
                   f"{row['temperature_c']:.0f} °C.")
    if row["problems"]:
        st.warning(f"Missing: {row['problems']}")


def show_reply(r):
    if r.error:
        st.error(f"The agent couldn't answer: {r.error}")
    if r.text:
        st.markdown(r.text)
    if r.calls:
        with st.expander("Tools the agent used"):  # like FR-12.4: show how the answer was made
            for c in r.calls:
                st.markdown(f"**{c.name}**(`{', '.join(f'{k}={v!r}' for k, v in c.args.items())}`)")
                if c.error:
                    st.caption(f"Error: {c.error}")
                if c.table is not None and not c.table.empty:
                    st.dataframe(c.table, hide_index=True, use_container_width=True)


def advice_tab():
    st.subheader("Bike, walk or wait")
    st.caption(
        "Is the tram worth waiting for? Compares the next tram (HSL real-time) with walking and a city "
        "bike, and checks the weather. Fixed rules make the choice; nothing about you is stored."
    )
    if not os.getenv("AGENT_ENDPOINT"):
        st.info("The agent isn't configured: the app has no AGENT_ENDPOINT.")
        return
    try:
        names = load_tram_stop_names()
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load Stops: {e}")
        return
    with st.form("advice"):
        c1, c2 = st.columns(2)
        start = c1.selectbox("I'm at (tram Stop)", names, index=None, placeholder="e.g. Kaivopuisto", key="advice_from")
        end = c2.selectbox("Going to", names, index=None, placeholder="e.g. Rautatientori", key="advice_to")
        go = st.form_submit_button("Advise me")
    if go and start and end:
        with st.spinner("Checking trams, city bikes and weather…"):
            try:
                wh = warehouse()
                df = wh.query(f"SELECT * FROM {wh.prefix}.bike_walk_or_wait(:a, :b)", {"a": start, "b": end})
            except Exception as e:  # noqa: BLE001 - FR-15.6
                st.error(f"Could not get advice: {str(e)[:300]}")
                df = None
        if df is not None and not df.empty:
            row = df.iloc[0].to_dict()
            print(f"advice choice={row['choice']} reason={row['reason']!r}")  # FR-15.7: outcome only
            show_advice(row)
    elif go:
        st.info("Pick both Stops.")

    st.divider()
    st.markdown("**Ask the agent**")
    st.caption("It uses the same tools, plus Genie for history. Model: " + os.getenv("AGENT_ENDPOINT", ""))
    chat = st.session_state.setdefault("agent_history", [])
    for role, content in chat:
        with st.chat_message(role):
            if role == "assistant":
                show_reply(content)
            else:
                st.markdown(content)
    cols = st.columns(len(AGENT_EXAMPLES))
    picked = next((q for c, q in zip(cols, AGENT_EXAMPLES) if c.button(q, key=f"agent_ex_{q}")), None)
    question = st.chat_input("e.g. I'm at Hakaniemi going to Kallio, should I walk?", key="agent_input") or picked
    if not question:
        return
    turns = [
        {"role": role, "content": content if role == "user" else content.text}
        for role, content in chat
        if role == "user" or content.text
    ]
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("The agent is calling its tools…"):
            reply = agent().ask(question, turns)
        show_reply(reply)
    chat.extend([("user", question), ("assistant", reply)])


# ---------------------------------------------------------------- layout

st.title("HSL Live Transit")
health_strip()

with st.sidebar:
    st.header("Live map filters")
    modes = st.multiselect("Mode", ["tram", "metro"], default=["tram", "metro"], format_func=str.capitalize)
    routes = st.text_input("Routes (comma-separated, empty = all)", "")
    st.header("My Routes")
    email = viewer_email()
    watched, only_mine = [], False
    if email:
        watched = load_watched(email)
        st.multiselect(
            "Routes I watch",
            route_options(watched),
            default=watched,
            key="my_routes_picker",
            on_change=save_watched,
            args=(email,),
            help="Saved in Lakebase for your account (FR-14).",
        )
        if st.session_state.get("my_routes_error"):
            st.error(f"Could not save My Routes: {st.session_state['my_routes_error'][:150]}")
        only_mine = st.toggle("Only my Routes on the map", value=False, disabled=not watched)
    else:
        st.caption("Sign in through Databricks to save My Routes.")
    if st.button("New Ask conversation"):
        st.session_state.pop("ask_conversation", None)
        st.session_state["ask_history"] = []
        st.session_state["agent_history"] = []
    st.divider()
    st.caption(
        "Data: Helsinki Region Transport (HSL), CC BY 4.0. digitransit.fi. "
        "Weather: Finnish Meteorological Institute, CC BY 4.0."
    )

tab_map, tab_punct, tab_stops, tab_ask, tab_advice = st.tabs(
    ["Live map", "Punctuality", "Stop Lateness", "Ask", "Bike, walk or wait"]
)
with tab_map:
    live_map(modes, routes, tuple(watched), only_mine)
    report_panel()
with tab_punct:
    punctuality_tab()
with tab_stops:
    stops_tab()
with tab_ask:
    ask_tab()
with tab_advice:
    advice_tab()
