"""Pipeline step: add a duration to every activity once precedence is known.

Runs AFTER the productivity step (buildflow.productivity). Reads its output
(list or {"activities": [...]}); every activity must already carry a
`productivity` block, otherwise it gets no duration. Each activity gains:

    duration_days       whole working days, or None if it cannot be computed
    gangs_used          number of parallel gangs assumed
    duration_basis      "computed" | "fixed" | "missing_productivity" | "missing_quantity"

    duration = ceil(quantity / (output_per_gang_day * gangs * efficiency)), at least min_days

An activity may carry its own `fixed_days` (curing, mobilisation, testing); an
override can still replace it.

Overrides (optional JSON file), most specific wins:
    {"defaults": {"gangs": 1, "efficiency": 1.0, "min_days": 1},
     "by_class":    {"STEEL_REINFORCEMENT": {"gangs": 3}},
     "by_activity": {"A16": {"fixed_days": 2}}}
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from buildflow.productivity import norm_class

BASE_DEFAULTS = {"gangs": 1, "efficiency": 1.0, "min_days": 1}


def _settings(act: dict, overrides: dict) -> dict:
    cfg = {**BASE_DEFAULTS, **overrides.get("defaults", {})}
    if act.get("fixed_days") is not None:   # waits and lump-sum items carry their own duration
        cfg["fixed_days"] = act["fixed_days"]
    cfg.update(overrides.get("by_class", {}).get(norm_class(act.get("activity_class", "")), {}))
    cfg.update(overrides.get("by_activity", {}).get(act.get("activity_id"), {}))
    if cfg["gangs"] <= 0 or cfg["efficiency"] <= 0 or cfg["min_days"] < 0:
        raise ValueError(f"invalid override for {act.get('activity_id')}: {cfg}")
    return cfg


def compute_duration(act: dict, overrides: dict | None = None) -> dict:
    """Return the duration fields for one activity (does not modify it)."""
    cfg = _settings(act, overrides or {})
    if "fixed_days" in cfg:
        return {"duration_days": int(cfg["fixed_days"]), "gangs_used": None,
                "duration_basis": "fixed"}
    prod = act.get("productivity")
    if not prod:
        return {"duration_days": None, "gangs_used": None,
                "duration_basis": "missing_productivity"}
    qty = act.get("quantity")
    if qty is None or float(qty) <= 0:
        return {"duration_days": None, "gangs_used": None,
                "duration_basis": "missing_quantity"}
    per_day = prod["output_per_gang_day"] * cfg["gangs"] * cfg["efficiency"]
    days = max(cfg["min_days"], math.ceil(float(qty) / per_day - 1e-9))
    return {"duration_days": days, "gangs_used": cfg["gangs"], "duration_basis": "computed"}


def add_durations(data, overrides: dict | None = None):
    """Return (enriched copy, report). Input is not modified."""
    overrides = overrides or {}
    activities = data["activities"] if isinstance(data, dict) else data
    out, problems = [], []
    for act in activities:
        act = {**act, **compute_duration(act, overrides)}
        if act["duration_days"] is None:
            problems.append({"activity_id": act.get("activity_id"), "reason": act["duration_basis"]})
        out.append(act)
    total = sum(a["duration_days"] or 0 for a in out)
    report = {"total": len(out), "with_duration": len(out) - len(problems),
              "problems": problems, "sum_of_durations": total}
    return ({**data, "activities": out} if isinstance(data, dict) else out), report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add durations to the activity JSON.")
    ap.add_argument("input", help="output of the productivity step")
    ap.add_argument("output")
    ap.add_argument("--overrides", help="optional JSON of gang / fixed-day overrides")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.input).read_text())
    overrides = json.loads(Path(args.overrides).read_text()) if args.overrides else {}
    enriched, report = add_durations(data, overrides)
    Path(args.output).write_text(json.dumps(enriched, indent=2))

    print(f"durations for {report['with_duration']}/{report['total']} activities "
          f"(sum of durations {report['sum_of_durations']} days, before CPM overlap)")
    for p in report["problems"]:
        print(f"  NO DURATION  {p['activity_id']}: {p['reason']}")
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
