"""Attach productivity from the library (data/productivity.json) to activity JSON.

Input is the activity list from the classification step, either a bare list or
{"activities": [...]}. Each activity is matched on `activity_class` and gets a
`productivity` block copied from the library. Unmatched classes get
`productivity: None` and are reported, never guessed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_LIBRARY = Path(__file__).resolve().parent.parent / "data" / "productivity.json"

# Spellings of the same unit that appear in BOQs; anything else must match exactly.
UNIT_ALIASES = {
    "cum": "cum", "m3": "cum", "cu.m": "cum", "cubic metre": "cum",
    "sqm": "sqm", "m2": "sqm", "sq.m": "sqm",
    "m": "m", "meter": "m", "metre": "m", "mtr": "m", "rm": "m",
    "kg": "kg", "no": "no", "nos": "no", "no.": "no", "each": "no",
    "hr": "hr", "hrs": "hr", "h": "hr", "hour": "hr", "hours": "hr",
}


def norm_class(value: str) -> str:
    return str(value).strip().upper().replace(" ", "_").replace("-", "_")


def norm_unit(value: str) -> str:
    key = str(value).strip().lower()
    return UNIT_ALIASES.get(key, key)


def load_library(path: str | Path = DEFAULT_LIBRARY) -> dict[str, dict]:
    """Return {activity_class: entry}. Duplicate classes are an error."""
    return load_library_with_type(path)[1]


def load_library_with_type(path: str | Path = DEFAULT_LIBRARY) -> tuple[str | None, dict[str, dict]]:
    """Return (project_type of the dataset, {activity_class: entry})."""
    data = json.loads(Path(path).read_text())
    entries = data["items"] if isinstance(data, dict) else data
    library: dict[str, dict] = {}
    for entry in entries:
        key = norm_class(entry["activity_class"])
        if key in library:
            raise ValueError(f"duplicate activity_class in library: {key}")
        library[key] = entry
    return (data.get("project_type") if isinstance(data, dict) else None), library


def _block(entry: dict) -> dict:
    return {
        "source": entry["source"],
        "match_quality": entry["match_quality"],
        "unit": entry["unit"],
        "coefficients_days_per_unit": entry["coefficients_days_per_unit"],
        "default_gang": entry["default_gang"],
        "output_per_gang_day": entry["output_per_gang_day"],
        "note": entry.get("note", ""),
    }


def derive(entry: dict, quantity) -> dict | None:
    """Work out what this activity's quantity needs from the library entry."""
    if quantity is None:
        return None
    coeff, gang = entry["coefficients_days_per_unit"], entry["default_gang"]
    qty = float(quantity)
    per_gang = {r: gang[r] / c for r, c in coeff.items() if r in gang and c > 0}
    limiting = min(per_gang, key=per_gang.get)
    return {
        "resource_days": {r: round(qty * c, 2) for r, c in coeff.items()},
        "limiting_resource": limiting,
    }


def attach_productivity(data, library: dict[str, dict]):
    """Return (enriched copy of data, report). Input is not modified."""
    activities = data["activities"] if isinstance(data, dict) else data
    out, missing, unit_mismatch, proxied = [], [], [], []
    for act in activities:
        act = dict(act)
        entry = library.get(norm_class(act.get("activity_class", "")))
        if entry is None:
            act["productivity"] = None
            act["derived"] = None
            if act.get("fixed_days") is None:   # fixed-duration items need no productivity
                missing.append({"activity_id": act.get("activity_id"),
                                "activity_class": act.get("activity_class")})
        else:
            act["productivity"] = _block(entry)
            act["derived"] = derive(entry, act.get("quantity"))
            if norm_unit(act.get("unit", "")) != norm_unit(entry["unit"]):
                unit_mismatch.append({"activity_id": act.get("activity_id"),
                                      "boq_unit": act.get("unit"),
                                      "library_unit": entry["unit"]})
            if entry["match_quality"] != "exact":
                proxied.append({"activity_id": act.get("activity_id"),
                                "match_quality": entry["match_quality"]})
        out.append(act)
    report = {"total": len(out), "matched": len(out) - len(missing),
              "missing": missing, "unit_mismatch": unit_mismatch,
              "not_exact": proxied}
    enriched = {**data, "activities": out} if isinstance(data, dict) else out
    return enriched, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="activity JSON from the classification step")
    ap.add_argument("output", help="where to write the enriched JSON")
    ap.add_argument("--library", default=str(DEFAULT_LIBRARY))
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any class is missing or a unit mismatches")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.input).read_text())
    lib_type, library = load_library_with_type(args.library)
    if isinstance(data, dict) and lib_type and data.get("project_type") not in (None, lib_type):
        print(f"warning: input project_type {data['project_type']} but library is {lib_type}")
    enriched, report = attach_productivity(data, library)
    Path(args.output).write_text(json.dumps(enriched, indent=2))

    print(f"matched {report['matched']}/{report['total']} activities")
    for m in report["missing"]:
        print(f"  MISSING  {m['activity_id']}: {m['activity_class']} (add it to the library)")
    for u in report["unit_mismatch"]:
        print(f"  UNIT     {u['activity_id']}: BOQ '{u['boq_unit']}' vs library '{u['library_unit']}'")
    for n in report["not_exact"]:
        print(f"  {n['match_quality'].upper():8} {n['activity_id']}: not an exact DAR/IS match")
    bad = report["missing"] or report["unit_mismatch"]
    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
