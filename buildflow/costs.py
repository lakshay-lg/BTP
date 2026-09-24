"""Pipeline step: activity cost, monthly allocation, cash flow and S-curve.

Runs AFTER buildflow.dates. Uses each activity's quantity x rate (the BOQ rate)
and its start/end dates. Adds to each activity:

    cost            quantity * rate, or None when the rate is missing
    monthly_cost    {"YYYY-MM": amount}, spread evenly over its working days

and, for a dict input, top-level `cost_summary`, `cashflow_config` and
`monthly` (one row per month) whose fields feed the cost charts:

    work_value, overhead, expenditure     what is billed / spent that month
    cumulative_work_value, cumulative_percent   the S-curve
    receipt                 cash received that month (certified value of earlier
                            months, less retention and advance recovery)
    retention_withheld, retention_released, advance_received, advance_recovered
    net_cash_flow, cumulative_net_cash      cumulative_net_cash minimum = peak funding need

Simplifying assumption: expenditure equals the billed (BOQ) value plus site
overhead, so net cash flow reflects payment timing, not profit.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from buildflow.calendar import is_working_day, parse_date

DEFAULTS = {"indirect_pct": 0.0, "retention_pct": 0.0, "payment_lag_months": 1,
            "mobilisation_advance_pct": 0.0}


class CostError(ValueError):
    pass


def _month(day: date) -> str:
    return day.strftime("%Y-%m")


def _shift(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    i = y * 12 + m - 1 + n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _month_range(first: str, last: str) -> list[str]:
    out, cur = [], first
    while cur <= last:
        out.append(cur)
        cur = _shift(cur, 1)
    return out


def _allocate(cost: float, start: date, end: date, workweek: int, holidays: set[date]) -> dict[str, float]:
    days, cur = [], start
    while cur <= end:
        if is_working_day(cur, workweek) and cur not in holidays:
            days.append(cur)
        cur += timedelta(days=1)
    if not days:
        days = [start]
    per_day = cost / len(days)
    monthly: dict[str, float] = defaultdict(float)
    for d in days:
        monthly[_month(d)] += per_day
    rounded = {m: round(v, 2) for m, v in monthly.items()}
    last = max(rounded)
    rounded[last] = round(rounded[last] + (round(cost, 2) - sum(rounded.values())), 2)
    return dict(sorted(rounded.items()))


def add_costs(data, cashflow: dict | None = None):
    """Return (enriched copy, report). Raises CostError if dates are missing."""
    cfg = {**DEFAULTS, **(cashflow or {})}
    for key, hi in (("indirect_pct", 100), ("retention_pct", 100), ("mobilisation_advance_pct", 100)):
        if not 0 <= cfg[key] <= hi:
            raise CostError(f"{key} must be between 0 and {hi}")
    if int(cfg["payment_lag_months"]) < 0:
        raise CostError("payment_lag_months cannot be negative")

    activities = data["activities"] if isinstance(data, dict) else data
    bad = [a.get("activity_id") for a in activities if not a.get("start_date") or not a.get("end_date")]
    if bad:
        raise CostError("no start/end dates for: " + ", ".join(map(str, bad)) + " (run the dates step first)")
    workweek = data.get("workweek_days", 6) if isinstance(data, dict) else 6
    holidays = {parse_date(h) for h in (data.get("holidays", []) if isinstance(data, dict) else [])}

    out, no_rate = [], []
    work: dict[str, float] = defaultdict(float)
    for a in activities:
        qty, rate = a.get("quantity"), a.get("rate")
        if qty is None or rate is None:
            no_rate.append(a.get("activity_id"))
            out.append({**a, "cost": None, "monthly_cost": {}})
            continue
        cost = round(float(qty) * float(rate), 2)
        monthly = _allocate(cost, parse_date(a["start_date"]), parse_date(a["end_date"]), workweek, holidays)
        for m, v in monthly.items():
            work[m] += v
        out.append({**a, "cost": cost, "monthly_cost": monthly})

    total = round(sum(a["cost"] or 0 for a in out), 2)
    report = {"total": len(out), "priced": len(out) - len(no_rate), "no_rate": no_rate, "total_cost": total}
    result = {**data, "activities": out} if isinstance(data, dict) else out
    if not isinstance(result, dict) or not work:
        return result, report

    lag = int(cfg["payment_lag_months"])
    active = _month_range(min(work), max(work))
    overhead_total = total * cfg["indirect_pct"] / 100
    overhead = {m: overhead_total / len(active) for m in active}
    advance = total * cfg["mobilisation_advance_pct"] / 100
    ret_pct, adv_pct = cfg["retention_pct"] / 100, cfg["mobilisation_advance_pct"] / 100

    receipt, ret_w, adv_rec = defaultdict(float), defaultdict(float), defaultdict(float)
    for m in active:
        bill = work.get(m, 0.0)
        ret_w[m] = bill * ret_pct
        adv_rec[m] = bill * adv_pct
        receipt[_shift(m, lag)] += bill - ret_w[m] - adv_rec[m]
    release_month = _shift(active[-1], lag)
    retention_released = {release_month: sum(ret_w.values())} if ret_pct else {}
    if advance:
        receipt[active[0]] += advance
    months = _month_range(active[0], max(max(receipt, default=active[-1]), release_month))

    rows, cum_work, cum_cash = [], 0.0, 0.0
    for m in months:
        w, oh = work.get(m, 0.0), overhead.get(m, 0.0)
        rel = retention_released.get(m, 0.0)
        rec = receipt.get(m, 0.0) + rel
        net = rec - (w + oh)
        cum_work += w
        cum_cash += net
        rows.append({"month": m, "work_value": round(w, 2), "overhead": round(oh, 2),
                     "expenditure": round(w + oh, 2), "receipt": round(rec, 2),
                     "retention_withheld": round(ret_w.get(m, 0.0), 2),
                     "retention_released": round(rel, 2),
                     "advance_received": round(advance if (advance and m == active[0]) else 0.0, 2),
                     "advance_recovered": round(adv_rec.get(m, 0.0), 2),
                     "net_cash_flow": round(net, 2), "cumulative_net_cash": round(cum_cash, 2),
                     "cumulative_work_value": round(cum_work, 2),
                     "cumulative_percent": round(100 * cum_work / total, 2) if total else 0.0})
    trough = min(rows, key=lambda r: r["cumulative_net_cash"])
    peak = max(rows, key=lambda r: r["work_value"])
    result["cashflow_config"] = cfg
    result["monthly"] = rows
    result["cost_summary"] = {
        "total_work_value": total, "total_overhead": round(overhead_total, 2),
        "peak_spend_month": peak["month"], "peak_spend_value": peak["work_value"],
        "peak_funding_requirement": round(max(0.0, -trough["cumulative_net_cash"]), 2),
        "peak_funding_month": trough["month"], "final_net_cash": rows[-1]["cumulative_net_cash"]}
    return result, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add costs, monthly allocation and cash flow.")
    ap.add_argument("input", help="output of the dates step")
    ap.add_argument("output")
    ap.add_argument("--cashflow", help='JSON of cash-flow settings, e.g. {"retention_pct": 5}')
    args = ap.parse_args(argv)
    try:
        result, rep = add_costs(json.loads(Path(args.input).read_text()),
                                json.loads(args.cashflow) if args.cashflow else None)
    except CostError as exc:
        print(f"costs failed: {exc}", file=sys.stderr)
        return 1
    Path(args.output).write_text(json.dumps(result, indent=2))
    print(f"priced {rep['priced']}/{rep['total']} activities, total {rep['total_cost']:,.2f}")
    for a in rep["no_rate"]:
        print(f"  NO RATE  {a}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
