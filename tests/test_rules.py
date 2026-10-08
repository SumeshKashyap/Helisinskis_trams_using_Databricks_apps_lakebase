"""Bike, walk or wait rule (FR-15.3, FR-15.5)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "agents"))

import rules

DRY = {
    "precipitation_mm_h": 0.0,
    "rain_probability_pct": 10,
    "gust_ms": 5.0,
    "temperature_c": 12.0,
    "in_bike_season": True,
}


def decide(tram, walk, bike, **weather):
    return rules.decide(tram, walk, bike, **{**DRY, **weather})


def test_late_tram_dry_weather_takes_the_bike():
    r = decide(tram=1200, walk=1500, bike=600)
    assert r["choice"] == "bike"
    assert "10 min" in r["reason"] and "20 min" in r["reason"]


def test_rain_rules_out_the_bike():
    r = decide(tram=1200, walk=1500, bike=600, precipitation_mm_h=1.2)
    assert r == {"choice": "wait", "reason": "the tram gets you there in 20 min; no bike: rain is forecast"}


def test_likely_rain_rules_out_the_bike():
    assert decide(tram=1200, walk=1500, bike=600, rain_probability_pct=80)["choice"] == "wait"


def test_gusts_rule_out_the_bike():
    r = decide(tram=1200, walk=1500, bike=600, gust_ms=12.4)
    assert r["choice"] == "wait" and "gusts up to 12 m/s" in r["reason"]


def test_freezing_rules_out_the_bike():
    assert decide(tram=1200, walk=1500, bike=600, temperature_c=-1.0)["choice"] == "wait"


def test_out_of_season_drops_the_bike():
    r = decide(tram=1200, walk=1500, bike=600, in_bike_season=False)
    assert r["choice"] == "wait" and "out of season" in r["reason"]


def test_no_bike_available():
    r = decide(tram=1200, walk=1500, bike=None)
    assert r["choice"] == "wait" and "no city bike nearby" in r["reason"]


def test_bike_must_save_two_minutes():
    assert decide(tram=700, walk=1500, bike=600)["choice"] == "wait"
    assert decide(tram=720, walk=1500, bike=600)["choice"] == "bike"


def test_walk_wins_when_no_later_than_the_tram():
    r = decide(tram=600, walk=600, bike=None)
    assert r["choice"] == "walk"


def test_bike_beats_walk_only_with_a_real_saving():
    assert decide(tram=1200, walk=900, bike=800)["choice"] == "walk"
    assert decide(tram=1200, walk=900, bike=781)["choice"] == "walk"
    assert decide(tram=1200, walk=900, bike=600)["choice"] == "bike"


def test_short_walk_is_fine_in_rain_long_walk_is_not():
    assert decide(tram=600, walk=500, bike=None, precipitation_mm_h=2.0)["choice"] == "walk"
    assert decide(tram=1200, walk=900, bike=None, precipitation_mm_h=2.0)["choice"] == "wait"


def test_no_tram_picks_the_fastest_allowed_option():
    assert decide(tram=None, walk=900, bike=500)["choice"] == "bike"
    assert decide(tram=None, walk=900, bike=500, gust_ms=15.0)["choice"] == "walk"


def test_no_option_at_all():
    r = decide(tram=None, walk=1500, bike=None, precipitation_mm_h=3.0)
    assert r["choice"] == "none"


def test_unknown_weather_does_not_block_the_bike():
    r = decide(tram=1200, walk=1500, bike=600, precipitation_mm_h=None, rain_probability_pct=None,
               gust_ms=None, temperature_c=None)
    assert r["choice"] == "bike"
