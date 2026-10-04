"""Builds the HSL Live Transit architecture diagram as a 1600x900 SVG (16:9 slide).

Brand logos come from Simple Icons (CC0, icons/); the trademarks belong to their owners.
Regenerate: python3 make_diagram.py, then
  google-chrome --headless=new --hide-scrollbars --force-device-scale-factor=2 \
    --window-size=1600,900 --virtual-time-budget=5000 --screenshot=architecture.png file://$PWD/render.html
"""
import re, pathlib, html

HERE = pathlib.Path(__file__).parent
W, H = 1600, 900

NAVY = "#1B3139"
RED = "#FF3621"
INK = "#1B3139"
MUTED = "#5A6B73"
LINE = "#C9D1D6"
ZONE = "#F4F6F7"
CARD = "#FFFFFF"
BRAND = {"databricks": "#FF3621", "mqtt": "#660066", "postgresql": "#4169E1",
         "streamlit": "#FF4B4B", "apachespark": "#E25A1C"}

def brand_path(name):
    s = (HERE / "icons" / f"{name}.svg").read_text()
    return re.search(r'<path d="([^"]+)"', s).group(1)

out = []
def add(s): out.append(s)
def esc(t): return html.escape(t, quote=True)

def text(x, y, t, size=15, weight=400, fill=INK, anchor="start", family="body", italic=False):
    fam = "var(--f-head)" if family == "head" else ("var(--f-mono)" if family == "mono" else "var(--f-body)")
    st = ' font-style="italic"' if italic else ""
    add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" style="font-family:{fam}"{st}>{esc(t)}</text>')

def logo(name, x, y, size=26, color=None):
    s = size / 24
    add(f'<g transform="translate({x},{y}) scale({s})"><path d="{brand_path(name)}" fill="{color or BRAND[name]}"/></g>')

def glyph(kind, x, y, size=26, color=NAVY):
    """Simple line glyphs for components without a public icon, drawn on a 24-unit grid."""
    s = size / 24
    g = f'<g transform="translate({x},{y}) scale({s})" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
    body = {
        "warehouse": '<path d="M3 9 L12 4 L21 9"/><rect x="4" y="9" width="16" height="11" rx="1"/><path d="M8 20v-6h8v6"/><path d="M8 17h8"/>',
        "genie": '<path d="M4 5h16v11H11l-4 4v-4H4z"/><path d="M12 7.5l1 2.3 2.3 1-2.3 1-1 2.3-1-2.3-2.3-1 2.3-1z" fill="' + color + '" stroke="none"/>',
        "dashboard": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 16v-4M11 16V8M15 16v-6M19 16v-2"/>',
        "job": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/>',
        "users": '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.6 2.7-6 6-6s6 2.4 6 6"/><circle cx="17" cy="9" r="2.6"/><path d="M15.5 14.3c3 .2 5.5 2.4 5.5 5.7"/>',
        "file": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M9 12h6M9 15h6M9 18h4"/>',
        "cdf": '<path d="M4 9a8 8 0 0 1 14-3l2 2"/><path d="M20 4v4h-4"/><path d="M20 15a8 8 0 0 1-14 3l-2-2"/><path d="M4 20v-4h4"/>',
        "delta": '<path d="M12 4 L21 19 H3 Z"/><path d="M12 10 L16.5 17 H7.5 Z"/>',
        "bundle": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
        "catalog": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16M10 10v10"/>',
    }[kind]
    add(g + body + "</g>")

def card(x, y, w, h, fill=CARD, stroke=LINE, r=10, dash=False, sw=1.4):
    d = ' stroke-dasharray="6 5"' if dash else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

def node(x, y, w, h, title, sub=None, icon=None, sub2=None, accent=None):
    """A component card: icon left, title + up to two subtitle lines."""
    card(x, y, w, h)
    if accent:
        add(f'<rect x="{x}" y="{y+10}" width="4" height="{h-20}" rx="2" fill="{accent}"/>')
    tx = x + 14
    if icon:
        kind, name = icon
        (logo if kind == "logo" else glyph)(name, x + 14, y + (h - 28) / 2, 28)
        tx = x + 54
    lines = [title] + ([sub] if sub else []) + ([sub2] if sub2 else [])
    total = 18 + 16 * (len(lines) - 1)
    ty = y + (h - total) / 2 + 14
    text(tx, ty, title, 15.5, 650, INK, family="head")
    for i, t in enumerate(lines[1:]):
        text(tx, ty + 18 + 16 * i, t, 12.5, 400, MUTED)

def arrow(points, label=None, lpos=None, color=NAVY, dash=False, lanchor="middle", width=1.8):
    d = "M" + " L".join(f"{x},{y}" for x, y in points)
    da = ' stroke-dasharray="6 5"' if dash else ""
    add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" marker-end="url(#ah)"{da}/>')
    if label:
        lx, ly = lpos
        # label pill
        wpx = 7.0 * len(label) + 14
        bx = lx - wpx / 2 if lanchor == "middle" else (lx if lanchor == "start" else lx - wpx)
        add(f'<rect x="{bx}" y="{ly-13}" width="{wpx}" height="19" rx="9.5" fill="#FFFFFF" stroke="{LINE}" stroke-width="1"/>')
        text(bx + wpx / 2, ly + 1, label, 11.5, 600, NAVY, "middle")

# ---------------------------------------------------------------- canvas
add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">')
add("""<defs>
<style>
  :root { --f-head: 'Source Sans 3', 'Segoe UI', Arial, sans-serif; --f-body: 'Source Sans 3', 'Segoe UI', Arial, sans-serif; --f-mono: 'JetBrains Mono', Consolas, monospace; }
</style>
<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
  <path d="M0,0 L10,5 L0,10 z" fill="#1B3139"/>
</marker>
</defs>""")
add(f'<rect width="{W}" height="{H}" fill="#FFFFFF"/>')

# title
text(40, 56, "HSL Live Transit: reference architecture", 30, 700, INK, family="head")
text(40, 84, "Helsinki tram and metro positions, from an open MQTT feed to a live Databricks App and back into the lakehouse", 16, 400, MUTED)
add(f'<rect x="40" y="98" width="64" height="4" rx="2" fill="{RED}"/>')

# ---------------------------------------------------------------- source zone
card(30, 130, 220, 640, fill=ZONE, stroke=ZONE, r=14)
text(48, 158, "HSL OPEN DATA", 12.5, 700, MUTED, family="head")
node(46, 190, 188, 104, "HFP feed", "mqtt.hsl.fi · MQTT/TLS", ("logo", "mqtt"), "~4 msgs / tram / s")
node(46, 470, 188, 90, "GTFS timetable", "stops, routes, daily", ("glyph", "file"))

# ---------------------------------------------------------------- databricks zone
DX, DY, DW, DH = 280, 130, 1110, 640
card(DX, DY, DW, DH, fill=ZONE, stroke=ZONE, r=14)
logo("databricks", DX + 18, DY + 14, 24)
text(DX + 52, DY + 32, "DATABRICKS PLATFORM", 12.5, 700, MUTED, family="head")
glyph("catalog", DX + 250, DY + 15, 20, MUTED)
text(DX + 276, DY + 31, "Unity Catalog · my_databricks_workspace.hsl_live_transit", 12.5, 400, MUTED)

# pipeline container
PX, PY, PW, PH = 300, 190, 560, 200
card(PX, PY, PW, PH, fill="#FFFFFF", stroke=LINE, r=12)
logo("apachespark", PX + 16, PY + 14, 22)
text(PX + 46, PY + 31, "Lakeflow Declarative Pipeline", 15.5, 700, INK, family="head")
text(PX + 270, PY + 31, "continuous · serverless", 12.5, 400, MUTED)
text(PX + PW - 16, PY + 31, "~26 s p95 to the map", 12.5, 700, RED, "end")
add(f'<line x1="{PX+16}" y1="{PY+46}" x2="{PX+PW-16}" y2="{PY+46}" stroke="{LINE}"/>')

# medallion stages
stages = [
    ("Python Data", "Source", "hsl_mqtt", None),
    ("Bronze", "raw payload", "5-day retention", "#B87333"),
    ("Silver", "typed, deduped", "expectations", "#8C99A6"),
    ("Gold", "AUTO CDC · CDF", "coverage", "#D4A017"),
]
sx, sy, sw_, sh = PX + 16, PY + 66, 120, 110
gap = (PW - 32 - 4 * sw_) / 3
for i, (t, s1, s2, col) in enumerate(stages):
    x = sx + i * (sw_ + gap)
    card(x, sy, sw_, sh, fill="#FFFFFF", stroke=LINE, r=10)
    if col:
        add(f'<rect x="{x}" y="{sy}" width="{sw_}" height="8" rx="4" fill="{col}"/>')
        add(f'<rect x="{x}" y="{sy+4}" width="{sw_}" height="4" fill="{col}"/>')
        text(x + sw_ / 2, sy + 42, t, 16, 700, INK, "middle", family="head")
        text(x + sw_ / 2, sy + 66, s1, 12, 400, MUTED, "middle")
        text(x + sw_ / 2, sy + 84, s2, 12, 400, MUTED, "middle")
    else:
        logo("apachespark", x + sw_ / 2 - 11, sy + 14, 22)
        text(x + sw_ / 2, sy + 56, t, 14, 700, INK, "middle", family="head")
        text(x + sw_ / 2, sy + 72, s1, 14, 700, INK, "middle", family="head")
        text(x + sw_ / 2, sy + 92, s2, 11.5, 400, MUTED, "middle", family="mono")
    if i < 3:
        arrow([(x + sw_ + 3, sy + sh / 2), (x + sw_ + gap - 3, sy + sh / 2)])
# gold tables card (bottom left)
card(300, WY_G := 640, 270, 96)
add(f'<rect x="300" y="{WY_G+10}" width="4" height="76" rx="2" fill="#D4A017"/>')
glyph("delta", 314, WY_G + 34, 28)
text(354, WY_G + 26, "Gold Delta tables", 15.5, 650, INK, family="head")
for i, t in enumerate(["gold_vehicle_current", "gold_departures", "gold_coverage_minutely"]):
    text(354, WY_G + 47 + 17 * i, t, 12, 400, MUTED, family="mono")

# jobs under the pipeline
node(300, 470, 270, 90, "Lakeflow Job: GTFS loader", "daily → silver_stops, silver_routes", ("glyph", "job"))
node(590, 470, 270, 90, "Lakeflow Job: demo_session", "starts and stops pipeline, sync, app", ("glyph", "job"))

# serving column
SX, SW = 895, 245
node(SX, 190, SW, 84, "Lakebase synced table", "Postgres · ~20 ms reads", ("logo", "postgresql"), accent="#4169E1")
node(SX, 296, SW, 84, "SQL warehouse", "serverless 2X-Small · 0.5–2.6 s", ("glyph", "warehouse"))
node(SX, 402, SW, 84, "Genie space", "questions → SQL · Conversation API", ("glyph", "genie"))
node(SX, 508, SW, 84, "AI/BI dashboard", "SQL via the warehouse", ("glyph", "dashboard"))

# app
AX, AY, AW, AH = 1180, 190, 192, 402
card(AX, AY, AW, AH, fill="#FFFFFF", stroke=LINE, r=12)
logo("streamlit", AX + 16, AY + 16, 26)
text(AX + 52, AY + 30, "Databricks App", 15.5, 700, INK, family="head")
text(AX + 52, AY + 47, "Streamlit · pydeck", 12.5, 400, MUTED)
add(f'<line x1="{AX+16}" y1="{AY+62}" x2="{AX+AW-16}" y2="{AY+62}" stroke="{LINE}"/>')
tabs = [("Live map", "every 4 s"), ("Punctuality", "per route"), ("Stop lateness", "stop map"),
        ("Ask", "Genie chat"), ("Rider Reports", "write"), ("My Routes", "write")]
for i, (t, s) in enumerate(tabs):
    ty = AY + 78 + i * 52
    add(f'<rect x="{AX+16}" y="{ty}" width="{AW-32}" height="40" rx="8" fill="{ZONE}"/>')
    text(AX + 28, ty + 25, t, 13.5, 650, INK, family="head")
    text(AX + AW - 28, ty + 25, s, 11.5, 400, MUTED, "end")

# write-back row
WY, WH = 640, 96
node(1180, WY, 192, WH, "Lakebase Postgres", "hsl_reports, hsl_users", ("logo", "postgresql"), "app-owned tables", accent="#4169E1")
node(SX, WY, SW, WH, "Lakebase Change Data Feed", "Postgres WAL → Delta, ~0.3 s", ("glyph", "cdf"), "Public Preview")
node(590, WY, 270, WH, "Delta table in Unity Catalog", "lb_rider_reports_history", ("glyph", "delta"), "queried by Genie")

# users
card(1420, 130, 150, 640, fill=ZONE, stroke=ZONE, r=14)
text(1438, 158, "PEOPLE", 12.5, 700, MUTED, family="head")
node(1432, 340, 126, 104, "Riders", "browser", ("glyph", "users"), "with SSO")

# bundle bar
card(280, 790, 1110, 56, fill="#FFFFFF", stroke=NAVY, r=12, sw=1.4)
glyph("bundle", 298, 804, 28)
text(338, 815, "Declarative Automation Bundle", 15.5, 700, INK, family="head")
text(338, 834, "One databricks bundle deploy creates the pipeline, jobs, Lakebase project and synced table, warehouse, Genie space, dashboard and app", 12.5, 400, MUTED)

# ---------------------------------------------------------------- arrows
# source -> pipeline
arrow([(234, 262), (PX + 16 - 2, 262)])
# GTFS -> loader
arrow([(234, 515), (298, 515)])
# loader -> silver
silver_x = sx + 2 * (sw_ + gap) + sw_ / 2
arrow([(435, 470), (435, 455), (silver_x, 455), (silver_x, sy + sh + 3)])
# gold -> lakebase synced, gold -> warehouse
gold_x = sx + 3 * (sw_ + gap) + sw_
arrow([(gold_x + 2, sy + 30), (880, sy + 30), (880, 232), (SX - 2, 232)])
arrow([(gold_x + 2, sy + 80), (880, sy + 80), (880, 338), (SX - 2, 338)])
# warehouse runs Genie's SQL and the dashboard's datasets
arrow([(SX + SW / 2, 380 + 2), (SX + SW / 2, 402 - 2)])
# serving -> app
arrow([(SX + SW + 2, 232), (AX - 2, 232)])
arrow([(SX + SW + 2, 338), (AX - 2, 338)])
arrow([(SX + SW + 2, 444), (AX - 2, 444)])
# app <-> users
arrow([(AX + AW + 2, 392), (1430, 392)])
arrow([(1430, 410), (AX + AW + 2, 410)])
# app -> postgres (writes)
arrow([(AX + AW / 2, AY + AH + 2), (AX + AW / 2, WY - 2)], "writes", (AX + AW / 2 + 40, AY + AH + 26))
# postgres -> cdf -> delta
arrow([(AX - 2, WY + WH / 2), (SX + SW + 2, WY + WH / 2)])
arrow([(SX - 2, WY + WH / 2), (860 + 2, WY + WH / 2)])
# delta -> genie (dashed: Genie also reads the history table)
arrow([(725, WY - 2), (725, 618), (877, 618), (877, 470), (SX - 2, 470)], dash=True, color=MUTED, width=1.4)
# end-to-end freshness callout

add("</svg>")
svg = "\n".join(out)
(HERE / "architecture.svg").write_text(svg)
(HERE / "render.html").write_text(
    '<!doctype html><html><head><meta charset="utf-8">'
    '<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;650;700&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet">'
    '<style>html,body{margin:0;padding:0;background:#fff}svg{display:block}</style></head><body>' + svg + "</body></html>")
print("ok")
