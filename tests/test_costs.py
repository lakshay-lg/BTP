import pytest

from buildflow.costs import CostError, add_costs


def proj(*acts, **top):
    return {"workweek_days": 6, "holidays": [], "activities": list(acts), **top}


def act(i, qty, rate, start, end):
    return {"activity_id": i, "quantity": qty, "rate": rate, "start_date": start, "end_date": end}


def test_cost_and_monthly_split_over_working_days_sums_exactly():
    # Wed 2026-09-30 .. Fri 2026-10-02: three working days, one in Sep, two in Oct
    r, rep = add_costs(proj(act("A", 10, 300, "2026-09-30", "2026-10-02")))
    a = r["activities"][0]
    assert a["cost"] == 3000.0
    assert a["monthly_cost"] == {"2026-09": 1000.0, "2026-10": 2000.0}
    assert rep["total_cost"] == 3000.0


def test_sunday_and_holiday_excluded_from_allocation():
    # Sat 3 Oct, Sun 4 Oct, Mon 5 Oct (holiday), Tue 6 Oct -> Sat and Tue only, but same month
    r, _ = add_costs(proj(act("A", 1, 100, "2026-10-03", "2026-10-06"), holidays=["2026-10-05"]))
    assert r["activities"][0]["monthly_cost"] == {"2026-10": 100.0}


def test_cashflow_lag_retention_advance_and_s_curve():
    p = proj(act("A", 1, 1000, "2026-10-01", "2026-10-01"), act("B", 1, 1000, "2026-11-02", "2026-11-02"))
    r, _ = add_costs(p, {"retention_pct": 10, "payment_lag_months": 1, "mobilisation_advance_pct": 20})
    rows = {m["month"]: m for m in r["monthly"]}
    assert rows["2026-10"]["cumulative_percent"] == 50.0 and rows["2026-11"]["cumulative_percent"] == 100.0
    # Oct bill 1000: retention 100, advance recovery 200 -> receipt 700 in Nov
    assert rows["2026-11"]["receipt"] == 700.0
    assert rows["2026-10"]["advance_received"] == 400.0 and rows["2026-10"]["receipt"] == 400.0
    # all retention (200) released in the last receipt month (Dec)
    assert rows["2026-12"]["retention_released"] == 200.0
    # over the whole job, cash in equals work value (advance fully recovered, retention released)
    assert sum(m["receipt"] for m in r["monthly"]) == 2000.0
    assert r["cost_summary"]["total_work_value"] == 2000.0


def test_missing_rate_reported_and_dates_required():
    r, rep = add_costs(proj(act("A", 5, None, "2026-10-01", "2026-10-01"),
                            act("B", 1, 10, "2026-10-01", "2026-10-01")))
    assert rep["no_rate"] == ["A"] and r["activities"][0]["cost"] is None
    with pytest.raises(CostError, match="dates"):
        add_costs(proj({"activity_id": "A", "quantity": 1, "rate": 1}))
    with pytest.raises(CostError):
        add_costs(proj(act("A", 1, 1, "2026-10-01", "2026-10-01")), {"retention_pct": 150})
