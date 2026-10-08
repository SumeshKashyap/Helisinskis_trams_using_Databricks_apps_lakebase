"""Bike, walk or wait: the rule that picks the advice (FR-15.3).

Pure Python with no imports, because this file is also the body of the Unity Catalog function
`decide_trip` (scripts/setup_agents.py pastes it in). The language model never makes this choice;
it only calls the tools and explains the result.

All times are seconds from now. None means "no such option" (no tram found, no bike, no forecast).
"""

# Thresholds (FR-15.3). Change them here; the setup script redeploys the UC function.
RAIN_MM_H = 0.5  # forecast precipitation that rules out the bike
RAIN_PROBABILITY_PCT = 70  # or this chance of precipitation
GUST_MS = 10.0  # hourly maximum gust that rules out the bike
FREEZING_C = 0.0  # at or below this, ice: no bike
WALK_IN_RAIN_MAX_S = 600  # in rain, walk only if it takes at most 10 min
BIKE_MIN_SAVING_S = 120  # the bike must beat the tram by 2 min to be worth the hassle


def decide(
    tram_arrive_s,
    walk_arrive_s,
    bike_arrive_s,
    precipitation_mm_h,
    rain_probability_pct,
    gust_ms,
    temperature_c,
    in_bike_season,
):
    """Return {"choice": "wait" | "walk" | "bike" | "none", "reason": str}."""
    rainy = (precipitation_mm_h is not None and precipitation_mm_h >= RAIN_MM_H) or (
        rain_probability_pct is not None and rain_probability_pct >= RAIN_PROBABILITY_PCT
    )

    bike_blocker = None
    if not in_bike_season:
        bike_blocker = "city bikes are out of season (April to October only)"
    elif bike_arrive_s is None:
        bike_blocker = "no city bike nearby with a free bike and a free dock at the other end"
    elif rainy:
        bike_blocker = "rain is forecast"
    elif gust_ms is not None and gust_ms >= GUST_MS:
        bike_blocker = f"gusts up to {gust_ms:.0f} m/s are forecast"
    elif temperature_c is not None and temperature_c <= FREEZING_C:
        bike_blocker = "it is freezing, roads may be icy"

    walk_blocker = None
    if walk_arrive_s is None:
        walk_blocker = "no walking route"
    elif rainy and walk_arrive_s > WALK_IN_RAIN_MAX_S:
        walk_blocker = "it is a long walk in the rain"

    def minutes(s):
        return f"{round(s / 60)} min"

    if tram_arrive_s is None:
        options = []
        if walk_blocker is None:
            options.append((walk_arrive_s, "walk"))
        if bike_blocker is None:
            options.append((bike_arrive_s, "bike"))
        if not options:
            return {"choice": "none", "reason": "no tram found, and " + (bike_blocker or walk_blocker)}
        arrive, choice = min(options)
        return {"choice": choice, "reason": f"no tram found; you arrive in {minutes(arrive)} by {choice}"}

    if walk_blocker is None and walk_arrive_s <= tram_arrive_s:
        if bike_blocker is None and bike_arrive_s + BIKE_MIN_SAVING_S <= walk_arrive_s:
            return {
                "choice": "bike",
                "reason": f"the bike gets you there in {minutes(bike_arrive_s)}, "
                f"walking takes {minutes(walk_arrive_s)} and the tram {minutes(tram_arrive_s)}",
            }
        return {
            "choice": "walk",
            "reason": f"walking gets you there in {minutes(walk_arrive_s)}, "
            f"no later than the tram ({minutes(tram_arrive_s)})",
        }

    if bike_blocker is None and bike_arrive_s + BIKE_MIN_SAVING_S <= tram_arrive_s:
        return {
            "choice": "bike",
            "reason": f"the bike gets you there in {minutes(bike_arrive_s)}, "
            f"the tram in {minutes(tram_arrive_s)}",
        }

    why = bike_blocker or (
        f"the bike would save less than {minutes(BIKE_MIN_SAVING_S)}" if bike_arrive_s is not None else None
    )
    reason = f"the tram gets you there in {minutes(tram_arrive_s)}"
    if why:
        reason += f"; no bike: {why}"
    return {"choice": "wait", "reason": reason}
