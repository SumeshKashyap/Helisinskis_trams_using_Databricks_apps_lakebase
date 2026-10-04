"""Pure styling rules for the live map (FR-7.2, FR-7.3). No Streamlit, no database."""

FRESH_S = 30  # Freshness: fresh up to here
HIDDEN_S = 300  # fading until here, hidden after
MIN_FADE_ALPHA = 40

# Default On-time window (CONTEXT.md): 60 s early … 180 s late.
ON_TIME_EARLY_S = -60
ON_TIME_LATE_S = 180
VERY_LATE_S = 600

# name: (label, RGB)
BANDS = {
    "early": ("Early (> 1 min)", (49, 130, 189)),
    "on_time": ("On-time", (44, 160, 44)),
    "late": ("Late (3–10 min)", (255, 152, 0)),
    "very_late": ("Very late (> 10 min)", (214, 39, 40)),
    "unknown": ("Lateness not published", (140, 140, 140)),
}


def freshness_alpha(age_s):
    """Opacity 0–255 from seconds since the last Position Event; None means hidden."""
    if age_s is None or age_s > HIDDEN_S:
        return None
    if age_s <= FRESH_S:
        return 255
    frac = (age_s - FRESH_S) / (HIDDEN_S - FRESH_S)
    return round(255 - frac * (255 - MIN_FADE_ALPHA))


def lateness_band(lateness_s):
    """Lateness band; unknown Lateness (all metro, ADR-0004) is never on-time."""
    if lateness_s is None:
        return "unknown"
    if lateness_s < ON_TIME_EARLY_S:
        return "early"
    if lateness_s <= ON_TIME_LATE_S:
        return "on_time"
    if lateness_s <= VERY_LATE_S:
        return "late"
    return "very_late"


def format_lateness(lateness_s):
    if lateness_s is None:
        return "not published"
    sign = "+" if lateness_s > 0 else "−" if lateness_s < 0 else ""
    m, s = divmod(abs(int(lateness_s)), 60)
    late_or_early = "late" if lateness_s > 0 else "early" if lateness_s < 0 else "on schedule"
    return f"{sign}{m}:{s:02d} ({late_or_early})"
