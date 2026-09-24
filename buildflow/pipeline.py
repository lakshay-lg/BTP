"""Run the whole schedule half in one go:
[rules ->] productivity -> durations -> CPM -> calendar dates -> costs and cash flow.

With --apply-rules the input is the normalized items (no predecessors); the project-type
rule file assigns them. Without it the input must already carry predecessors.

The output JSON holds, per activity, everything a bar chart needs: id,
description, predecessors, duration_days, start_date, end_date, critical,
total_float, cost and monthly_cost, plus top-level `monthly` cash-flow rows and
cost_summary (and the productivity figures behind each duration).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from buildflow.costs import CostError, add_costs
from buildflow.cpm import CpmError, run_cpm
from buildflow.dates import add_dates
from buildflow.durations import add_durations
from buildflow.rules import RulesError, apply_rules, load_rules
from buildflow.productivity import (DEFAULT_LIBRARY, attach_productivity,
                                    load_library_with_type)


def run_pipeline(data, start_date, library_path=DEFAULT_LIBRARY, overrides=None,
                 workweek_days: int = 6, holidays=(), cashflow=None,
                 use_rules=False, structures=None, quantity_basis="per_structure"):
    """Return (result, reports). Raises CpmError/CostError on missing durations, a bad network or bad settings."""
    lib_type, library = load_library_with_type(library_path)
    warnings, rules_report = [], None
    if use_rules:
        source = data.get("project_type") if isinstance(data, dict) else None
        data, rules_report = apply_rules(data, load_rules(source or lib_type), structures, quantity_basis)
        for u in rules_report["unruled"]:
            warnings.append(f"{u['activity_id']}: no precedence rule for class {u['activity_class']}")
    if isinstance(data, dict) and lib_type and data.get("project_type") not in (None, lib_type):
        warnings.append(f"input project_type {data['project_type']} but library is {lib_type}")
    data, prod_report = attach_productivity(data, library)
    data, dur_report = add_durations(data, overrides)
    data = run_cpm(data)
    data = add_dates(data, start_date, workweek_days, holidays)
    data, cost_report = add_costs(data, cashflow)
    return data, {"rules": rules_report, "productivity": prod_report, "durations": dur_report,
                  "costs": cost_report, "warnings": warnings}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="activity JSON from the precedence step")
    ap.add_argument("output")
    ap.add_argument("--start", required=True, help="project start date, YYYY-MM-DD")
    ap.add_argument("--library", default=str(DEFAULT_LIBRARY))
    ap.add_argument("--overrides", help="optional JSON of gang / fixed-day overrides")
    ap.add_argument("--workweek", type=int, default=6)
    ap.add_argument("--holidays", nargs="*", default=[])
    ap.add_argument("--apply-rules", action="store_true", help="input is normalized items; assign predecessors from the project-type rules first")
    ap.add_argument("--structures", type=int, help="number of structures (tanks) for the rules step")
    ap.add_argument("--quantity-basis", default="per_structure", choices=["per_structure", "project_total"])
    ap.add_argument("--cashflow", help='JSON of cash-flow settings, e.g. \'{"retention_pct": 5, "payment_lag_months": 1}\'')
    args = ap.parse_args(argv)

    overrides = json.loads(Path(args.overrides).read_text()) if args.overrides else {}
    try:
        result, rep = run_pipeline(json.loads(Path(args.input).read_text()), args.start,
                                   args.library, overrides, args.workweek, args.holidays,
                                   json.loads(args.cashflow) if args.cashflow else None,
                                   args.apply_rules, args.structures, args.quantity_basis)
    except (CpmError, CostError, RulesError) as exc:
        print(f"pipeline failed: {exc}", file=sys.stderr)
        return 1
    Path(args.output).write_text(json.dumps(result, indent=2))

    for w in rep["warnings"]:
        print(f"warning: {w}")
    p = rep["productivity"]
    print(f"productivity matched {p['matched']}/{p['total']}")
    for m in p["missing"]:
        print(f"  MISSING  {m['activity_id']}: {m['activity_class']}")
    for u in p["unit_mismatch"]:
        print(f"  UNIT     {u['activity_id']}: BOQ '{u['boq_unit']}' vs library '{u['library_unit']}'")
    for n in p["not_exact"]:
        print(f"  {n['match_quality'].upper():8} {n['activity_id']}: not an exact DAR/IS match")
    c = rep["costs"]
    print(f"priced {c['priced']}/{c['total']} activities, total {c['total_cost']:,.2f}")
    for a in c["no_rate"]:
        print(f"  NO RATE  {a}")
    if isinstance(result, dict):
        print(f"{result['project_start_date']} -> {result['project_end_date']} "
              f"({result['project_duration_days']} working days)")
        print("critical path: " + " -> ".join(result["critical_path"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
