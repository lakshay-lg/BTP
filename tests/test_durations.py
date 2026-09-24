import pytest

from buildflow.durations import add_durations, compute_duration

PROD = {"output_per_gang_day": 5.56, "coefficients_days_per_unit": {"beldar": 2.0},
        "default_gang": {"beldar": 12}, "source": "DAR", "match_quality": "exact", "unit": "cum"}


def act(**kw):
    return {"activity_id": "A5", "activity_class": "RCC_FOOTING", "quantity": 50,
            "productivity": PROD, **kw}


def test_default_duration_rounds_up():
    assert compute_duration(act())["duration_days"] == 9  # 50 / 5.56 = 8.99


def test_more_gangs_shorten_and_min_days_applies():
    ov = {"by_class": {"RCC_FOOTING": {"gangs": 3}}}
    assert compute_duration(act(), ov)["duration_days"] == 3
    assert compute_duration(act(quantity=0.1))["duration_days"] == 1


def test_activity_override_beats_class_and_fixed_days():
    ov = {"by_class": {"RCC_FOOTING": {"gangs": 3}}, "by_activity": {"A5": {"fixed_days": 4}}}
    out = compute_duration(act(), ov)
    assert out["duration_days"] == 4 and out["duration_basis"] == "fixed"


def test_missing_productivity_or_quantity_gives_none():
    assert compute_duration({"activity_id": "X", "productivity": None})["duration_basis"] == "missing_productivity"
    assert compute_duration(act(quantity=None))["duration_basis"] == "missing_quantity"


def test_bad_override_rejected():
    with pytest.raises(ValueError):
        compute_duration(act(), {"defaults": {"gangs": 0}})


def test_activity_without_productivity_step_gets_no_duration_and_predecessors_kept():
    data = {"activities": [{"activity_id": "A1", "activity_class": "RCC_FOOTING",
                            "quantity": 12, "predecessors": ["A0"]},
                           act(activity_id="A2")]}
    out, report = add_durations(data)
    a1, a2 = out["activities"]
    assert a1["duration_days"] is None and a1["duration_basis"] == "missing_productivity"
    assert a1["predecessors"] == ["A0"]
    assert a2["duration_days"] == 9 and report["with_duration"] == 1


def test_activity_level_fixed_days_and_override_precedence():
    a = {"activity_id": "CUR", "activity_class": "CURING", "quantity": 1, "fixed_days": 7, "productivity": None}
    assert compute_duration(a)["duration_days"] == 7
    assert compute_duration(a, {"by_activity": {"CUR": {"fixed_days": 3}}})["duration_days"] == 3
