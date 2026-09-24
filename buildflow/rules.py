"""Pipeline step: assign dependencies to normalized activities from a project-type rule file.

Input  (from the normalization step): items with activity_id, activity_class, quantity, unit, rate
       (optionally amount, description ...). Any predecessors already present are ignored.
Output: the same items plus `predecessors`, one activity per BOQ item per structure, plus the
       fixed-duration activities the rules add (mobilisation, curing, testing ...).

Rules live in data/rules/<project type>_precedence.json and are keyed by activity_class, never by
activity id. See docs/RULES.md for the format. Semantics:

  after        classes that must finish first. A class absent from the BOQ is bypassed: successors
               attach to that class's own predecessors, so one rule file serves BOQs with or
               without, say, a bore well.
  scope        "structure" classes are repeated for each structure (tank); "project" classes occur once.
  stagger      for structure k > 1, also wait for structure k-1's activities of class `stagger.after`
               (default: the same class), which models a shared crew.
  synthetic    activities the rules add (mobilisation, curing, testing): id, fixed_days, rate ...
  ALL_TERMINAL a project-scope class that waits for every activity with no successor.

Items whose class has no rule are still scheduled (after mobilisation, before the terminal
class) and reported under `unruled` so a planner can decide where they belong.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent / "data" / "rules"
ALL_TERMINAL = "ALL_TERMINAL"


class RulesError(ValueError):
    pass


def norm_class(value) -> str:
    return str(value).strip().upper().replace(" ", "_").replace("-", "_")


def rules_path_for(project_type: str) -> Path:
    """data/rules/rain_water_harvesting_precedence.json, or rwh_precedence.json via the alias below."""
    key = norm_class(project_type).lower()
    aliases = {"rain_water_harvesting": "rwh", "rainwater_harvesting": "rwh"}
    return RULES_DIR / f"{aliases.get(key, key)}_precedence.json"


def load_rules(path_or_type) -> dict:
    path = Path(path_or_type)
    if not path.suffix:  # a project type such as RAIN_WATER_HARVESTING
        path = rules_path_for(str(path_or_type))
    if not path.exists():
        raise RulesError(f"no rule file for '{path_or_type}' (looked for {path})")
    rules = json.loads(path.read_text())
    validate_rules(rules)
    return rules


def validate_rules(rules: dict) -> None:
    classes = rules.get("classes")
    if not classes:
        raise RulesError("rule file has no classes")
    for cls, r in classes.items():
        if cls != norm_class(cls):
            raise RulesError(f"class name must be UPPER_SNAKE_CASE: {cls}")
        if r.get("scope") not in ("structure", "project"):
            raise RulesError(f"{cls}: scope must be 'structure' or 'project'")
        after = r.get("after", [])
        if after != ALL_TERMINAL:
            for a in after:
                if a not in classes:
                    raise RulesError(f"{cls}: 'after' names unknown class {a}")
        stagger = r.get("stagger")
        if stagger and stagger.get("after", cls) not in classes:
            raise RulesError(f"{cls}: stagger names unknown class {stagger['after']}")
        if stagger and r["scope"] != "structure":
            raise RulesError(f"{cls}: only structure-scope classes can stagger")
        syn = r.get("synthetic")
        if syn and not {"id", "description", "fixed_days"} <= set(syn):
            raise RulesError(f"{cls}: synthetic needs id, description and fixed_days")
    ids = [r["synthetic"]["id"] for r in classes.values() if r.get("synthetic")]
    if len(ids) != len(set(ids)):
        raise RulesError("synthetic ids must be unique")
    # class-level cycle check
    state = {}
    def visit(c):
        if state.get(c) == 1:
            raise RulesError(f"precedence cycle through {c}")
        if state.get(c) == 2:
            return
        state[c] = 1
        after = classes[c].get("after", [])
        for a in ([] if after == ALL_TERMINAL else after):
            visit(a)
        state[c] = 2
    for c in classes:
        visit(c)


def _items_of(data):
    if isinstance(data, list):
        return data
    for key in ("activities", "items"):
        if key in data:
            return data[key]
    raise RulesError("input needs an 'activities' or 'items' list")


def apply_rules(data, rules: dict, structures: int | None = None, quantity_basis: str = "per_structure"):
    """Return (result, report). Raises RulesError on bad input."""
    if quantity_basis not in ("per_structure", "project_total"):
        raise RulesError("quantity_basis must be 'per_structure' or 'project_total'")
    validate_rules(rules)
    n = int(structures or rules.get("structures", {}).get("default_count", 1))
    if n < 1:
        raise RulesError("structures must be at least 1")
    classes, s_cfg = rules["classes"], rules.get("structures", {})
    prefix, label = s_cfg.get("prefix", "S"), s_cfg.get("label", "Structure")
    items = _items_of(data)
    ids = [i.get("activity_id") for i in items]
    if len(set(ids)) != len(ids) or any(not i for i in ids):
        raise RulesError("every item needs a unique activity_id")

    by_class: dict[str, list[dict]] = {}
    unruled = []
    for it in items:
        cls = norm_class(it.get("activity_class", ""))
        if cls in classes:
            by_class.setdefault(cls, []).append(it)
        else:
            unruled.append(it)

    present = set(by_class)
    for cls, r in classes.items():
        syn = r.get("synthetic")
        if not syn:
            continue
        cond = syn.get("include_if_any")
        if cond is None or any(c in present for c in cond):
            present.add(cls)

    def make_id(base, k, scope):
        return base if (scope == "project" or n == 1) else f"{prefix}{k}_{base}"

    # 1. instantiate activities per class and structure
    acts: dict[tuple[str, int], list[dict]] = {}     # (class, k) -> activities; k = 0 for project scope
    for cls in classes:
        if cls not in present:
            continue
        r, syn = classes[cls], classes[cls].get("synthetic")
        ks = [0] if r["scope"] == "project" else range(1, n + 1)
        for k in ks:
            made = []
            if syn:
                a = {"activity_id": make_id(syn["id"], k, r["scope"]), "activity_class": cls,
                     "description": (f"{label} {k}: " if k and n > 1 else "") + syn["description"],
                     "quantity": syn.get("quantity", 1), "unit": syn.get("unit", "LS"),
                     "rate": syn.get("rate", 0), "fixed_days": syn["fixed_days"],
                     "rate_basis": "rules", "rate_source": f"rule file synthetic activity {cls}"}
                made.append(a)
            else:
                for it in by_class[cls]:
                    a = {k2: v for k2, v in it.items() if k2 != "predecessors"}
                    a["activity_id"] = make_id(it["activity_id"], k, r["scope"])
                    a["activity_class"] = cls
                    if k and n > 1:
                        a["description"] = f"{label} {k}: {it.get('description', '')}".rstrip(": ")
                        if quantity_basis == "project_total":
                            for f in ("quantity", "amount"):
                                if isinstance(a.get(f), (int, float)):
                                    a[f] = a[f] / n
                    made.append(a)
            for a in made:
                a["structure"] = k or None
                a["predecessors"] = []
            acts[(cls, k)] = made
    for it in unruled:
        a = {k2: v for k2, v in it.items() if k2 != "predecessors"}
        a.update(structure=None, predecessors=[], rule_status="unruled")
        acts[(f"__UNRULED__{it['activity_id']}", 0)] = [a]

    # 2. wire predecessors from class-level rules, bypassing absent classes
    def resolve(cls_list, k):
        out, seen = [], set()
        def go(c):
            if c in seen:
                return
            seen.add(c)
            if c in present:
                scope = classes[c]["scope"]
                out.extend(a["activity_id"] for a in acts.get((c, 0 if scope == "project" else k), []))
            else:
                for p in classes[c].get("after", []):
                    go(p)
        for c in cls_list:
            go(c)
        return out

    bypassed = sorted(c for c in classes if c not in present and c != ALL_TERMINAL)
    for (cls, k), made in list(acts.items()):
        if cls.startswith("__UNRULED__"):
            made[0]["predecessors"] = resolve(["MOBILISATION"], 0) if "MOBILISATION" in classes else []
            continue
        r = classes[cls]
        if r.get("after") == ALL_TERMINAL:
            continue
        preds = resolve(r.get("after", []), max(k, 1))
        if r.get("stagger") and k > 1:
            preds += [a["activity_id"] for a in acts.get((r["stagger"].get("after", cls), k - 1), [])]
        for a in made:
            a["predecessors"] = list(dict.fromkeys(preds))
    # class with ALL_TERMINAL waits for every activity that nothing else waits for
    for cls, r in classes.items():
        if r.get("after") == ALL_TERMINAL and cls in present:
            everyone = [a for v in acts.values() for a in v]
            used = {p for a in everyone for p in a["predecessors"]}
            target = acts[(cls, 0)]
            terminal = [a["activity_id"] for a in everyone if a["activity_id"] not in used
                        and a["activity_id"] not in {t["activity_id"] for t in target}]
            for a in target:
                a["predecessors"] = terminal

    order = {c: i for i, c in enumerate(classes)}
    activities = [a for v in acts.values() for a in v]
    activities.sort(key=lambda a: (order.get(a["activity_class"], len(order)), a["structure"] or 0, a["activity_id"]))
    report = {"project_type": rules.get("project_type"), "structures": n, "quantity_basis": quantity_basis,
              "input_items": len(items), "activities": len(activities),
              "classes_bypassed": bypassed,
              "unruled": [{"activity_id": i.get("activity_id"), "activity_class": i.get("activity_class")} for i in unruled],
              "synthetic_added": sorted({a["activity_id"] for a in activities if a.get("rate_basis") == "rules"})}
    top = {} if isinstance(data, list) else {k: v for k, v in data.items() if k not in ("activities", "items")}
    if isinstance(data, list):
        return activities, report
    return {**top, "activities": activities}, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Assign dependencies from a project-type rule file.")
    ap.add_argument("input", help="normalized items JSON")
    ap.add_argument("output")
    ap.add_argument("--rules", help="rule file path or project type (default: from the input's project_type)")
    ap.add_argument("--structures", type=int, help="number of structures, e.g. tanks (default from the rule file)")
    ap.add_argument("--quantity-basis", default="per_structure", choices=["per_structure", "project_total"])
    args = ap.parse_args(argv)
    data = json.loads(Path(args.input).read_text())
    try:
        source = args.rules or (data.get("project_type") if isinstance(data, dict) else None)
        if not source:
            raise RulesError("give --rules or set project_type in the input JSON")
        result, rep = apply_rules(data, load_rules(source), args.structures, args.quantity_basis)
    except RulesError as exc:
        print(f"rules failed: {exc}", file=sys.stderr)
        return 1
    Path(args.output).write_text(json.dumps(result, indent=2))
    print(f"{rep['input_items']} items -> {rep['activities']} activities ({rep['structures']} structure(s), {rep['quantity_basis']})")
    if rep["synthetic_added"]:
        print("  added by rules:", ", ".join(rep["synthetic_added"]))
    if rep["classes_bypassed"]:
        print("  classes not in this BOQ (bypassed):", ", ".join(rep["classes_bypassed"]))
    for u in rep["unruled"]:
        print(f"  UNRULED  {u['activity_id']}: class {u['activity_class']} has no rule; scheduled after mobilisation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
