"""Pipeline step: critical path method on the JSON activities.

Runs AFTER buildflow.durations. Finish-to-start links only, with an optional
per-link lag (e.g. curing wait). Times are working-day offsets from project
start: an activity with ES=0 and duration 3 has EF=3 and occupies days 1-3.

Input activity fields used: activity_id, predecessors, duration_days and,
optionally, `lags` = {"<predecessor id>": days}. Each activity gains:

    ES, EF, LS, LF        early/late start and finish (working-day offsets)
    total_float           LS - ES
    free_float            days it can slip without delaying any successor
    critical              True when total_float == 0
    (top level, if the input is a dict) project_duration_days, critical_path
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path


class CpmError(ValueError):
    pass


def _num(x):
    """Whole numbers stay ints (lags may be fractional, so the maths is float)."""
    return int(x) if float(x).is_integer() else x


def _topological_order(ids: list[str], preds: dict[str, list[str]]) -> list[str]:
    succs = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for i in ids:
        for p in preds[i]:
            succs[p].append(i)
            indeg[i] += 1
    queue = deque(i for i in ids if indeg[i] == 0)
    order = []
    while queue:
        i = queue.popleft()
        order.append(i)
        for s in succs[i]:
            indeg[s] -= 1
            if indeg[s] == 0:
                queue.append(s)
    if len(order) != len(ids):
        stuck = sorted(set(ids) - set(order))
        raise CpmError(f"dependency cycle involving: {', '.join(stuck)}")
    return order


def run_cpm(data):
    """Return an enriched copy of data. Raises CpmError on bad input."""
    activities = data["activities"] if isinstance(data, dict) else data
    ids = [a["activity_id"] for a in activities]
    if len(set(ids)) != len(ids):
        raise CpmError("duplicate activity_id values")
    by_id = {a["activity_id"]: a for a in activities}

    no_duration = [i for i in ids if by_id[i].get("duration_days") is None]
    if no_duration:
        raise CpmError("no duration_days for: " + ", ".join(no_duration)
                       + " (run productivity and durations first)")
    preds = {i: list(by_id[i].get("predecessors") or []) for i in ids}
    for i, ps in preds.items():
        unknown = [p for p in ps if p not in by_id]
        if unknown:
            raise CpmError(f"{i} has unknown predecessor(s): {', '.join(unknown)}")
    lag = {i: {p: float((by_id[i].get("lags") or {}).get(p, 0)) for p in preds[i]} for i in ids}
    dur = {i: int(by_id[i]["duration_days"]) for i in ids}

    order = _topological_order(ids, preds)
    es, ef = {}, {}
    for i in order:
        es[i] = max((ef[p] + lag[i][p] for p in preds[i]), default=0)
        ef[i] = es[i] + dur[i]
    project = max(ef.values(), default=0)

    succs = {i: [] for i in ids}
    for i in ids:
        for p in preds[i]:
            succs[p].append(i)
    ls, lf = {}, {}
    for i in reversed(order):
        lf[i] = min((ls[s] - lag[s][i] for s in succs[i]), default=project)
        ls[i] = lf[i] - dur[i]

    out = []
    for a in activities:
        i = a["activity_id"]
        tf = ls[i] - es[i]
        ff = min((es[s] - lag[s][i] - ef[i] for s in succs[i]), default=project - ef[i])
        out.append({**a, "ES": _num(es[i]), "EF": _num(ef[i]), "LS": _num(ls[i]),
                    "LF": _num(lf[i]), "total_float": _num(tf), "free_float": _num(ff),
                    "critical": tf == 0})
    result = {**data, "activities": out} if isinstance(data, dict) else out
    if isinstance(result, dict):
        result["project_duration_days"] = _num(project)
        result["critical_path"] = [i for i in sorted(order, key=lambda k: (es[k], k))
                                   if ls[i] == es[i]]
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Critical path method on the activity JSON.")
    ap.add_argument("input", help="output of the durations step")
    ap.add_argument("output")
    args = ap.parse_args(argv)
    try:
        result = run_cpm(json.loads(Path(args.input).read_text()))
    except CpmError as exc:
        print(f"CPM failed: {exc}", file=sys.stderr)
        return 1
    Path(args.output).write_text(json.dumps(result, indent=2))
    if isinstance(result, dict):
        print(f"project duration: {result['project_duration_days']} working days")
        print("critical path: " + " -> ".join(result["critical_path"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
