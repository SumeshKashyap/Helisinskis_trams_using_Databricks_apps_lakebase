"""Pure helpers behind the analytics views (FR-8, FR-9, FR-10) and the Ask tab (FR-12)."""

import datetime as dt
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "app"))

import analytics as an

UTC = dt.UTC


def test_operating_day_runs_until_0430_helsinki():
    # 01:00 UTC = 04:00 Helsinki (EEST): still the previous Operating Day.
    assert an.operating_day(dt.datetime(2026, 10, 3, 1, 0, tzinfo=UTC)) == dt.date(2026, 10, 2)
    # 02:00 UTC = 05:00 Helsinki: the new one.
    assert an.operating_day(dt.datetime(2026, 10, 3, 2, 0, tzinfo=UTC)) == dt.date(2026, 10, 3)


def test_window_bounds():
    now = dt.datetime(2026, 10, 3, 16, 0, tzinfo=UTC)
    since, until, day = an.window_bounds("last_hour", now)
    assert (until - since) == dt.timedelta(hours=1) and day is None
    since, until, day = an.window_bounds("operating_day", now)
    assert since == dt.datetime(2026, 10, 3, 1, 30, tzinfo=UTC)  # 04:30 Helsinki
    assert day == dt.date(2026, 10, 3)
    with pytest.raises(ValueError):
        an.window_bounds("week", now)


def test_window_params_filter_today_by_operating_day():
    now = dt.datetime(2026, 10, 3, 16, 0, tzinfo=UTC)
    flt, params = an.window_params("operating_day", now)
    assert "operating_day = :operating_day" in flt and params["operating_day"] == dt.date(2026, 10, 3)
    flt, params = an.window_params("last_hour", now)
    assert ":since" in flt and "operating_day" not in params


def test_coverage_fraction_is_clamped():
    since = dt.datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
    until = since + dt.timedelta(hours=1)
    assert an.coverage_fraction(1800, since, until) == 0.5
    assert an.coverage_fraction(4000, since, until) == 1.0
    assert an.coverage_fraction(None, since, until) == 0.0
    assert an.coverage_fraction(10, until, since) is None


def test_gap_intervals_merges_consecutive_minutes():
    m = lambda h, mi: dt.datetime(2026, 10, 3, h, mi, tzinfo=UTC)
    assert an.gap_intervals([m(13, 40), m(13, 38), m(13, 39), m(14, 0)]) == [
        (m(13, 38), m(13, 41)),
        (m(14, 0), m(14, 1)),
    ]
    assert an.gap_intervals([]) == []


def test_route_sort_key_is_natural():
    assert sorted(["10", "4T", "2", "10H", "1"], key=an.route_sort_key) == ["1", "2", "4T", "10", "10H"]


def test_punctuality_keeps_unknown_lateness_in_denominator():
    q = an.punctuality_sql(lambda n: n, "TRUE")
    assert "/ count(*) AS punctuality" in q
    assert "BETWEEN :early_s AND :late_s" in q


def test_answer_from_message_reads_text_sql_and_table():
    pytest.importorskip("pandas")
    import genie

    ns = types.SimpleNamespace
    msg = ns(
        conversation_id="c1",
        message_id="m1",
        status="COMPLETED",
        error=None,
        attachments=[
            ns(
                attachment_id="a1",
                text=ns(content="Tram 4 is on time."),
                query=None,
                suggested_questions=None,
            ),
            ns(
                attachment_id="a2",
                text=None,
                query=ns(query="SELECT 1", description="Current tram 4 Lateness"),
                suggested_questions=ns(questions=["Is tram 9 late?"]),
            ),
        ],
    )
    resp = ns(
        manifest=ns(schema=ns(columns=[ns(name="vehicle_id"), ns(name="lateness_s")])),
        result=ns(data_array=[["40/75", "12"]]),
    )
    a = genie.answer_from_message(msg, lambda att_id: resp if att_id == "a2" else None)
    assert a.conversation_id == "c1" and a.text == "Tram 4 is on time." and a.sql == "SELECT 1"
    assert list(a.table.columns) == ["vehicle_id", "lateness_s"] and len(a.table) == 1
    assert a.suggestions == ["Is tram 9 late?"] and not a.error


def test_answer_from_message_without_content_is_an_error():
    import genie

    msg = types.SimpleNamespace(conversation_id="c1", status="FAILED", error=None, attachments=None)
    assert "FAILED" in genie.answer_from_message(msg, lambda _: None).error
