"""Build the two-tank RWH network and the gang overrides used to compare with the original plan.

NOTE: the network part is superseded by buildflow/rules.py + data/rules/rwh_precedence.json (keyed by class).
This script is still used to write data/rwh_gang_overrides.json.

Reads the single-tank activity JSON (data/rwh_activities.json) and writes
  data/rwh_two_tank_activities.json   two tanks, mobilisation, curing waits, testing & handover
  data/rwh_gang_overrides.json        gangs per activity class, calibrated to the original plan's days

Choices made here (all editable):
  * per-tank quantity = BOQ quantity for every row, including the G (bore well) rows, as the
    original schedule does; totals are therefore twice the BOQ
  * tank 2 follows tank 1 on every crew-limited trade (one crew per trade)
  * vent pipe follows the bore screen pipe, as in the original
  * 7-day curing wait after the RCC pour; backfill and filter media follow curing, as in the original
  * mobilisation and testing are lump sums taken from the original workbook
  * gangs per class = ceil(one-gang days / original days), so durations land at or under the original's
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = ["A01", "A03", "A04", "A05", "A06", "A11"]          # crew-limited: tank 2 follows tank 1
ORIG_DAYS = {"A01": 12, "A07": 5, "A02": 2, "A04": 12, "A03": 12, "A05": 4, "A06": 9, "A08": 4,
             "A09": 3, "A10": 3, "A11": 6, "A12": 3, "A15": 1, "A13": 1, "A16": 2, "A17": 2,
             "A18": 1, "A14": 1}


def build():
    base = json.loads((ROOT / "data" / "rwh_activities.json").read_text())
    lib = {e["activity_class"]: e for e in json.loads((ROOT / "data" / "productivity.json").read_text())["items"]}
    acts = {a["activity_id"]: a for a in base["activities"]}

    # single-tank predecessors, adjusted: curing wait after RCC; backfill then filter media
    preds = {k: list(v["predecessors"]) for k, v in acts.items()}
    preds["A07"] = ["CUR"]
    preds["A08"] = ["A07"]
    preds["A16"] = ["A12"]        # vent pipe belongs to the bore-well assembly (partner's rule had it after the RCC)
    out = [{"activity_id": "MOB", "activity_class": "MOBILISATION", "description": "Mobilization & site setup",
            "quantity": 1, "unit": "LS", "rate": 150000, "fixed_days": 6, "predecessors": [],
            "rate_basis": "original_workbook", "rate_source": "lump sum from the original cash-flow sheet"}]
    for t in (1, 2):
        pre = f"T{t}_"
        for aid, a in acts.items():
            p = [pre + x for x in preds[aid]]
            if aid == "A01":
                p = ["MOB"] if t == 1 else []
            if t == 2 and aid in SHARED:
                p.append(f"T1_{aid}")
            out.append({**{k: v for k, v in a.items() if k not in ("predecessors", "activity_id")},
                        "activity_id": pre + aid, "description": f"Tank {t}: {a['description']}",
                        "predecessors": p})
        out.append({"activity_id": pre + "CUR", "activity_class": "CURING_DESHUTTERING",
                    "description": f"Tank {t}: Curing & deshuttering (7-day wait)", "quantity": 1,
                    "unit": "LS", "rate": 0, "fixed_days": 7, "predecessors": [pre + "A06"],
                    "rate_basis": "none", "rate_source": "waiting time, no cost"})
    has_succ = {p for a in out for p in a["predecessors"]}
    out.append({"activity_id": "TEST", "activity_class": "TESTING_HANDOVER", "description": "Joint testing & handover",
                "quantity": 1, "unit": "LS", "rate": 100000, "fixed_days": 6,
                "predecessors": [a["activity_id"] for a in out if a["activity_id"] not in has_succ],
                "rate_basis": "original_workbook", "rate_source": "lump sum from the original cash-flow sheet"})

    project = {"project": base["project"] + " (two tanks)", "project_type": base["project_type"],
               "_note": base["_note"] + " Two-tank network built by scripts/build_two_tank_rwh.py: mobilisation, "
                        "curing and testing added; G-row quantities not doubled in the BOQ are used per tank here.",
               "activities": out}
    (ROOT / "data" / "rwh_two_tank_activities.json").write_text(json.dumps(project, indent=2))

    gangs, calib = {}, {}
    for aid, orig in ORIG_DAYS.items():
        a = acts[aid]; e = lib[a["activity_class"]]
        one = a["quantity"] / e["output_per_gang_day"]
        g = max(1, math.ceil(one / orig - 1e-9))
        people = sum(v for k, v in e["default_gang"].items() if k not in {"excavator", "loader", "rig", "truck", "mixer", "vibrator", "compressor"})
        gangs[a["activity_class"]] = {"gangs": g}
        calib[aid] = {"class": a["activity_class"], "one_gang_days": round(one, 1), "original_days": orig,
                      "gangs": g, "labour_headcount": g * people}
    ov = {"_note": "Gangs calibrated so each duration is at or under the original plan's days. "
                   "labour_headcount is the manpower this implies under DAR/IS productivity.",
          "by_class": gangs, "_calibration": calib}
    (ROOT / "data" / "rwh_gang_overrides.json").write_text(json.dumps(ov, indent=2))
    return calib


if __name__ == "__main__":
    for aid, c in build().items():
        print(f"{aid} {c['class']:24} one gang {c['one_gang_days']:>5}d vs original {c['original_days']:>2}d -> {c['gangs']} gang(s), ~{c['labour_headcount']} labour")
