"""Pipeline step: turn CPM working-day offsets into calendar dates.

Runs AFTER buildflow.cpm. Day 1 is the first working day on or after the
project start date. An activity with ES=k and duration d starts on working
day k+1 and finishes on working day k+d (both inclusive), so a 1-day activity
starts and ends on the same date. Zero-duration activities (milestones) get
start_date == end_date. Each activity gains `start_date` and `end_date`
(ISO strings); a dict input also gains project_start_date, project_end_date,
workweek_days and holidays.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

from buildflow.calendar import is_working_day, parse_date


def working_calendar(start: date, count: int, workweek_days: int = 6,
                     holidays: frozenset[date] = frozenset()) -> list[date]:
    """First `count` working dates on or after `start` (1-indexed by position+1)."""
    days, cur = [], start
    while len(days) < count:
        if is_working_day(cur, workweek_days) and cur not in holidays:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def add_dates(data, start_date, workweek_days: int = 6, holidays=()):
    """Return an enriched copy of data. Raises ValueError if CPM fields are missing."""
    activities = data["activities"] if isinstance(data, dict) else data
    missing = [a.get("activity_id") for a in activities if a.get("ES") is None or a.get("EF") is None]
    if missing:
        raise ValueError("no ES/EF for: " + ", ".join(map(str, missing)) + " (run the CPM step first)")
    start = parse_date(start_date)
    hol = frozenset(parse_date(h) for h in holidays)
    horizon = max((math.ceil(a["EF"]) for a in activities), default=0) + 1
    cal = working_calendar(start, horizon, workweek_days, hol)

    out = []
    for a in activities:
        es, ef = math.ceil(a["ES"]), math.ceil(a["EF"])
        first = cal[es]                     # working day es+1
        last = cal[ef - 1] if ef > es else first
        out.append({**a, "start_date": first.isoformat(), "end_date": last.isoformat()})
    if not isinstance(data, dict):
        return out
    end = max((date.fromisoformat(a["end_date"]) for a in out), default=cal[0])
    return {**data, "activities": out, "project_start_date": cal[0].isoformat(),
            "project_end_date": end.isoformat(), "workweek_days": workweek_days,
            "holidays": sorted(h.isoformat() for h in hol)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add calendar dates to the CPM output.")
    ap.add_argument("input", help="output of the CPM step")
    ap.add_argument("output")
    ap.add_argument("--start", required=True, help="project start date, YYYY-MM-DD")
    ap.add_argument("--workweek", type=int, default=6, help="working days per week (default 6, Sunday off)")
    ap.add_argument("--holidays", nargs="*", default=[], help="non-working dates, YYYY-MM-DD")
    args = ap.parse_args(argv)
    try:
        result = add_dates(json.loads(Path(args.input).read_text()), args.start,
                           args.workweek, args.holidays)
    except ValueError as exc:
        print(f"dates failed: {exc}", file=sys.stderr)
        return 1
    Path(args.output).write_text(json.dumps(result, indent=2))
    if isinstance(result, dict):
        print(f"{result['project_start_date']} -> {result['project_end_date']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
