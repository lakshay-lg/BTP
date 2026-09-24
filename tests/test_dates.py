import json

import pytest

from buildflow.dates import add_dates
from buildflow.pipeline import run_pipeline


def acts(*rows):
    return {"activities": [{"activity_id": i, "ES": es, "EF": ef} for i, es, ef in rows]}


def dates(result):
    return {a["activity_id"]: (a["start_date"], a["end_date"]) for a in result["activities"]}


def test_sunday_skipped_and_one_day_activity_same_date():
    # 2026-10-02 is a Friday; six-day week
    r = add_dates(acts(("A", 0, 1), ("B", 1, 2), ("C", 2, 3)), "2026-10-02")
    assert dates(r) == {"A": ("2026-10-02", "2026-10-02"),   # Fri
                        "B": ("2026-10-03", "2026-10-03"),   # Sat
                        "C": ("2026-10-05", "2026-10-05")}   # Mon (Sunday skipped)
    assert r["project_end_date"] == "2026-10-05"


def test_start_on_sunday_moves_to_monday_and_holidays_skipped():
    r = add_dates(acts(("A", 0, 2)), "2026-10-04", holidays=["2026-10-06"])
    assert dates(r)["A"] == ("2026-10-05", "2026-10-07")  # Mon, (Tue holiday), Wed
    assert r["project_start_date"] == "2026-10-05"


def test_five_day_week_and_milestone():
    r = add_dates(acts(("A", 0, 3), ("M", 3, 3)), "2026-10-01", workweek_days=5)
    d = dates(r)
    assert d["A"] == ("2026-10-01", "2026-10-05")          # Thu, Fri, Mon
    assert d["M"][0] == d["M"][1] == "2026-10-06"


def test_requires_cpm_fields():
    with pytest.raises(ValueError, match="CPM"):
        add_dates({"activities": [{"activity_id": "A"}]}, "2026-10-01")


def test_full_pipeline_gives_chart_ready_fields():
    data = json.load(open("data/rwh_activities.json"))
    out, rep = run_pipeline(data, "2026-10-01")
    a = out["activities"][0]
    for key in ("activity_id", "description", "predecessors", "duration_days",
                "start_date", "end_date", "critical", "total_float"):
        assert key in a
    assert out["project_duration_days"] == 114 and rep["productivity"]["matched"] == 18
    assert out["project_start_date"] == "2026-10-01"
