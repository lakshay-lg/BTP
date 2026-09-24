# Project-type precedence rules

Step 4 of the pipeline turns normalized items into a network of activities with `predecessors`.
The rules are data, one file per project type: `data/rules/<type>_precedence.json`
(RWH: `data/rules/rwh_precedence.json`). The code that applies them is `buildflow/rules.py`.

## The hand-off format

Input to the rules step (what the normalization step produces), one object per BOQ item:

```json
{"activity_id": "A05", "activity_class": "RCC_FOOTING", "quantity": 50, "unit": "cum", "rate": 9655.7}
```

Output: the same objects plus `predecessors`, per structure, plus the activities the rules add
(mobilisation, curing wait, testing). Rules never look at `activity_id`; only `activity_class`.

```bash
python3 -m buildflow.rules data/rwh_normalized_items.json out.json --structures 2
# or, from the normalized items through to dates and cash flow:
python3 -m buildflow.pipeline data/rwh_normalized_items.json schedule.json \
    --apply-rules --structures 2 --start 2026-04-25
```

## Rule file format

| Field | Meaning |
|---|---|
| `classes.<CLASS>.scope` | `structure` (repeated for each tank) or `project` (once) |
| `classes.<CLASS>.after` | classes that must finish first, or `"ALL_TERMINAL"` (wait for everything with no successor) |
| `classes.<CLASS>.stagger` | for structure k > 1, also wait for structure k-1's `stagger.after` class (default: same class); models a shared crew |
| `classes.<CLASS>.synthetic` | an activity the rules add: `id`, `description`, `fixed_days`, `rate`, and `include` / `include_if_any` |

Behaviour to know:
- A class that is not in the BOQ is **bypassed**: its successors attach to its predecessors, so one file serves BOQs with or without, for example, a bore well.
- An item whose class has no rule is still scheduled (after mobilisation, before the terminal activity) and listed under `unruled` so a planner can place it.
- `--quantity-basis per_structure` (default) gives every structure the BOQ quantity; `project_total` divides quantities across structures.
- Class names are UPPER_SNAKE_CASE and must match the productivity library (`data/productivity.json`).

## Adding a project type

1. Copy `data/rules/rwh_precedence.json` to `data/rules/<type>_precedence.json` and edit `project_type` and `classes`.
2. Use only class names from the shared list (add new ones to `data/productivity.json` as well).
3. Run the tests: `pytest tests/test_rules.py`. `validate_rules` rejects unknown classes and cycles.
