# Methodology

## Research question

Can a hybrid system produce a more valid and more auditable first-draft construction schedule/cash-flow forecast from Indian BOQs than either keyword rules or unconstrained LLM output, especially when structural typology changes?

The prototype separates the problem into components that can be evaluated independently:

1. **Reading:** BOQ description → work package, phase and physical track.
2. **Planning:** quantities + productivity assumptions + typology → duration and precedence network.
3. **Calculation:** acyclic network → CPM dates/float; allocated value → monthly curves.
4. **Audit:** total reconciliation, low-confidence rows, missing scope and duration variance.

## Reader and ablations

Three offline modes share the same taxonomy:

- `rules`: phrase/action matching;
- `retrieval`: cosine similarity against a small curated example corpus; and
- `hybrid`: rule and retrieval scores combined, with action phrases weighted above contextual words.

The current labelled smoke corpus can be evaluated with:

```bash
python3 -m buildflow.benchmark data/benchmark_labels.csv
```

For dissertation results, replace/extend this with independently labelled rows from the real STP, MEP and RWH BOQs. Freeze the labels before changing prompts/rules. Report accuracy, macro-F1, unknown rate, confidence calibration and per-typology errors.

The optional AI fallback receives only low-confidence rows and must choose an enum value through Structured Outputs. It cannot propose quantities, rates, durations or dependencies. Its added value should be measured as a fourth ablation, not assumed.

## Duration calculation

For each activity and allocated BOQ row:

```text
productive days = ceil(sum(allocated quantity / daily productivity) / crew multiplier)
activity duration = max(template minimum, productive days) + explicit wait/curing allowance
```

The starter rates are centralized in `PRODUCTIVITY` in `buildflow/scheduling.py`. Every rate must eventually have a source field: published norm, contractor/site record, expert judgement or calibrated result. The current code intentionally calls them defaults/assumptions, not published CPWD norms.

Cost allocations use fixed shares only when one BOQ package must become several construction stages (for example RCC, steel and formwork split over raft, wall and roof). Shares reconcile back to each BOQ row. Any residue becomes a conservative “Unmapped / miscellaneous” terminal activity and an audit flag; it is never silently dropped.

## Typology-specific networks

### STP / monolithic tank

The core structural path is deep excavation → PCC → raft → walls → roof. Waterproofing/backfill, plant-room finishes and equipment form branches. Hydrostatic testing is an explicit hold point even if its value is absent from a civil-only BOQ. Extra-lift surcharge quantities contribute cost but not a second production quantity.

### Linear MEP

Sewer, storm-water and pressure-pipe work use separate parallel trench/lay/backfill tracks. Internal drainage is another branch. Completed external networks converge before road reinstatement and integrated testing.

### RWH

Tank 2 excavation follows Tank 1 PCC as a transparent shared-earthwork/PCC crew assumption. The tanks otherwise advance on offset tracks. Borewell drilling follows mobilisation only, representing a separate specialist rig crew. All tracks converge at commissioning.

## CPM

The scheduler rejects missing predecessor IDs and directed cycles. It then performs:

- a topological forward pass for earliest start/finish;
- a reverse pass for latest start/finish and total float; and
- critical marking where total float is zero.

Integer offsets represent working days. Calendar conversion currently supports 5-, 6- or 7-day weeks; it does not yet include holidays.

## Cash-flow models

### Schedule-linked curve

Each activity’s allocated BOQ value is spread uniformly across its working dates, then grouped by calendar month. This curve tests Arpit’s generated schedule.

### Independent phase-pattern curve

Each work package has a standard start/finish fraction of the entered contract duration. A smoothstep S-curve distributes that item’s value within the window. It does not read CPM dates, activity durations or predecessors. This preserves Lakshay’s independence requirement and provides a comparison curve when both projects are available.

Both views expose gross work value, site overhead, planned expenditure, retention, lagged certification receipts and net period flow. For the BTP, the defensible primary outcome is monthly planned value unless verified contractor expenditure/payment records are available.

## Agent framing

`ProjectAgent` is a bounded workflow agent. It calls, in order:

1. `inspect_boq`
2. `select_typology`
3. `classify_boq`
4. optional `llm_reader`
5. `build_cpm`
6. `time_phase_cost`
7. `audit_result`

This supplies interactivity and a readable decision trace without allowing generative output to bypass engineering checks. Planner duration edits call the CPM and cash-flow tools again and are recorded as overrides.

