"""Pure parts of Rider Reports and My Routes (FR-13, FR-14). The SQL was tested on a Lakebase branch."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "app"))

import analytics as an
import store

pd = pytest.importorskip("pandas")


def test_reporter_id_is_a_stable_hash_not_the_email():
    a = store.reporter_id("Rider@Example.com ")
    assert a == store.reporter_id("rider@example.com")
    assert len(a) == 64 and "rider" not in a


def test_clean_note():
    assert store.clean_note("  hi  ") == "hi"
    assert store.clean_note("   ") is None
    assert len(store.clean_note("x" * 500)) == store.NOTE_MAX


def test_rate_limit_message():
    assert store.rate_limit_message(0, 0) is None
    assert "2 minutes" in store.rate_limit_message(1, 1)
    assert "limit" in store.rate_limit_message(store.PER_HOUR_LIMIT, 0)


def test_category_check_matches_categories():
    assert all(f"'{c}'" in store.SCHEMA_SQL[2] for c in store.CATEGORIES)


def test_my_routes_summary():
    vehicles = pd.DataFrame(
        {"route": ["4", "4", "M1"], "lateness_s": [30, 250, None]},
    )
    reports = pd.DataFrame({"route": ["4", "4", "9"]})
    out = {r["route"]: r for r in an.my_routes_summary(vehicles, reports, ["4", "M1", "10"])}
    assert out["4"] == {"route": "4", "vehicles": 2, "worst_lateness_s": 250, "reports": 2}
    assert out["M1"]["worst_lateness_s"] is None  # metro: Lateness not published
    assert out["10"]["vehicles"] == 0
    empty = an.my_routes_summary(pd.DataFrame(), pd.DataFrame(), ["4"])
    assert empty == [{"route": "4", "vehicles": 0, "worst_lateness_s": None, "reports": 0}]
