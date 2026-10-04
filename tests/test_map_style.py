import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "app"))

from map_style import format_lateness, freshness_alpha, lateness_band


def test_freshness_fresh_fading_hidden():
    assert freshness_alpha(0) == 255
    assert freshness_alpha(30) == 255
    mid = freshness_alpha(165)
    assert 40 < mid < 255
    assert freshness_alpha(300) == 40
    assert freshness_alpha(301) is None
    assert freshness_alpha(None) is None


def test_freshness_fades_monotonically():
    alphas = [freshness_alpha(s) for s in range(30, 301, 10)]
    assert alphas == sorted(alphas, reverse=True)


def test_lateness_bands_default_window():
    assert lateness_band(None) == "unknown"
    assert lateness_band(-61) == "early"
    assert lateness_band(-60) == "on_time"
    assert lateness_band(0) == "on_time"
    assert lateness_band(180) == "on_time"
    assert lateness_band(181) == "late"
    assert lateness_band(600) == "late"
    assert lateness_band(601) == "very_late"


def test_format_lateness():
    assert format_lateness(None) == "not published"
    assert format_lateness(75) == "+1:15 (late)"
    assert format_lateness(-30) == "−0:30 (early)"
    assert format_lateness(0) == "0:00 (on schedule)"
