# Validation protocol

## 1. Freeze evidence and provenance

For every case, create a folder containing the original tender BOQ, tender/contract duration, actual or approved baseline schedule, available monthly IPC/cash records, project metadata and a provenance sheet. Record document dates, source URLs/file hashes, redactions and who supplied the data.

Do not use the demonstration CSVs as research ground truth. Do not fill absent tender rates with unsourced market estimates for the final accuracy calculation.

## 2. Create expert ground truth before tuning

Have a planner map each usable BOQ row to:

- work package and track;
- one or more actual/baseline activities;
- allocation share if the row spans stages;
- applicable productivity source;
- predecessor relationships; and
- known missing-scope/temporary-works risks.

Freeze a held-out project before changing taxonomy examples, keyword weights or templates. If only three projects are available, use leave-one-project-out testing and report the severe sample-size limitation.

## 3. Reader evaluation

Compare `rules`, `retrieval`, `hybrid` and optional AI fallback on the same frozen rows. Report:

- accuracy and macro-F1;
- unknown/abstention rate;
- per-package and per-typology confusion matrices;
- calibration by confidence band; and
- review effort: rows corrected and minutes required.

## 4. Schedule evaluation

Compare the generated schedule with the approved baseline/actual record using:

- activity coverage: expert activities represented;
- precedence precision/recall after mapping activity granularity;
- cycle/missing-link count;
- activity-duration MAE/MAPE where mappings are meaningful;
- total project-duration error;
- critical-activity overlap (Jaccard); and
- planner correction count/time before acceptance.

Report both raw output and planner-corrected output. Do not call a plausible Gantt accurate solely because its project finish is close.

## 5. Cash-flow evaluation

Against verified monthly planned value, IPC or expenditure data, report:

- monthly MAE/MAPE (handle zero months explicitly);
- cumulative-value error at 25%, 50%, 75% and completion;
- peak-month displacement;
- peak-value error; and
- area between normalized cumulative curves.

Evaluate the independent phase curve first. Then compare it with the schedule-linked curve to answer the optional question: does a detailed CPM materially improve the cash forecast over contract-duration phase patterns?

## 6. Typology stress test

Keep results separate for:

- deep monolithic STP tank;
- linear/network MEP works; and
- parallel RWH tanks plus specialist borewell.

This is not enough to claim generalization to all construction. It is enough to demonstrate failure modes when structural topology changes and to motivate typology-conditioned planning.

## 7. Acceptance gates for a first-draft tool

A project output should not be handed to a planner unless:

- 100% of priced value reconciles to activities;
- the CPM graph is acyclic and all predecessors exist;
- every low-confidence/unpriced/unmapped row is listed;
- high-severity temporary-works/testing findings are acknowledged; and
- all non-source assumptions appear in the export.

