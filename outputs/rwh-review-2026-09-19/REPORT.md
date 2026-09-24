# RWH workbook evaluation — 19 September 2026

## Finding

BuildFlow is not yet reliable on this real BOQ layout. It preserves the 18 quantity-bearing rows and their quantities, and detects the RWH typology, but loses parent descriptions and produces materially wrong work packages and durations. Its displayed classification percentage measures coverage, not accuracy. The companion planning workbook also has scope and arithmetic inconsistencies and cannot currently serve as verified schedule/cash-flow ground truth.

This review ran the existing model without changing production code, either source workbook, or calling an external AI API. It includes controlled in-memory diagnostic adaptations, clearly separated from unmodified results. Source and model-code SHA-256 hashes, complete results, classified rows, activities, cash curves and assumptions are retained in `results.json`.

## 1. What the supplied files contain

### Original BOQ: `BOQ RWH (1).xlsx`

Sheet `CS for RWH` is a comparative statement for the Narsi Village road redevelopment RWH works. It has 18 quantity-bearing line items and three bidder rate/amount pairs. All 108 bidder price cells for those lines are blank. The completion-time fields in row 50 are blank. Thus **₹0 is not the project price**: price and actual duration are unavailable.

Descriptions are hierarchical: section headings, parent specifications and leaf quantities occur on separate rows. Examples:

- B9 contains the excavation specification; B10 only says “All kinds of soil”, with 1,027.6 m³ in D10.
- B13 gives the concrete specification; B14 contains the 1:4:8 mix and 10.65 m³.
- B19:B20 contain the steel work specification; B21 supplies the grade and 14,840.57 kg.
- B23:B25 contain the concrete specification and M30 grade; B26:B27 contain the location and quantities.

B44 states “For #2 Rain Water Harvesting Tank”. B32 says two bore wells in each tank; D33 has 120 m of drilling to 30 m depth, consistent with four wells across two tanks. This supports asking whether the quantities already cover both tanks. It does **not** establish that every civil quantity is per tank or project-wide.

### Companion: `RWH_BOQ_Schedule_CashFlow (1).xlsx`

Contains `Priced BOQ`, `Schedule`, and `Cash Flow`. `Priced BOQ!A2` explicitly calls the two-tank quantity multiplication an assumption. Its name, explanatory notes and dates match the derived workbook described in the earlier project context. Treat this as a supplied planning scenario until its rates, scope and schedule provenance are confirmed; a friend supplying a workbook does not establish that its schedule or cash flows are site actuals.

Independent arithmetic checks from the stored input values and explicit formulas:

| Check | Result | Cell evidence |
|---|---:|---|
| BOQ total, sum of quantity × rate | ₹58,36,097.52 | `Priced BOQ!D5:F23` |
| Cash-flow total | ₹31,95,230.00 | `Cash Flow!B2:G12` |
| Cash-flow shortfall relative to BOQ | ₹26,40,867.52 / 45.25% | Same ranges |
| Final cumulative total formula | `=F14+G12` | `Cash Flow!G14` |
| Result of that formula | ₹63,90,460.00, twice the cash total | F14 already contains the full cumulative total |
| Quantity multipliers against original | First 10 items ×2; remaining 8 items ×1 | Original D10:D41, companion D5:D22, matched by item |
| Schedule dates | 25 Apr–7 Aug 2026 | `Schedule!C28`, `Schedule!D29` |
| Schedule span | 90 working days / 105 inclusive calendar days | Six-day week, Sunday off |

All 24 activity rows' stated durations agree with inclusive working-day counts from their dates. That verifies calendar arithmetic, not dependencies, crew feasibility or actual completion.

The 31 formula cells have no cached numeric results. This review independently recomputed the simple products and sums; it did not recalculate or save the original workbook in Excel. The production parser uses cached values. After adapting the headers in memory, it can recover BOQ amounts through its existing quantity × rate fallback.

The cash columns are labelled “Month 1” through “Month 5”, without explicit date boundaries. The workbook also adds ₹1.5 lakh mobilisation and ₹1 lakh testing/handover in its cash table without matching BOQ rows. We cannot assume calendar-month alignment, common cost scope, or actual receipts. Consequently, no cash-forecast accuracy score against this workbook is defensible yet.

## 2. Experiment controls and limitations

- Start: 25 April 2026; six-day week; two structures; crew multiplier 1.
- Entered duration: 90 working days, derived from the companion schedule **for comparison only**, not a sourced contract duration. Additional one-structure and missing-contract tests were run.
- Overhead and retention: zero; payment lag: zero, to isolate direct planned-value arithmetic.
- External AI fallback: disabled. These results evaluate the offline rules/retrieval hybrid, not Groq/LLM quality.
- Provisional analyst labels cover all 18 source rows. They are inspectable below and require independent planner review before publication.
- Broad labels deliberately accept screen installation, gravel packing and cleaning under “borewell”, and cover/frame rows under “manhole”. This is a permissive family-level assessment; it does not validate their operation-specific productivity or sequencing.
- Four rows require categories the current taxonomy lacks: three filter-media rows and one safety-foot-rest/access-metalwork row. They count as incorrect for complete use-case coverage; supported-category results are reported separately.
- These are two representations of one project, not two independent validation projects. The existing RWH sample/template was already developed with this project context. This is a diagnostic case study, not an unseen generalisation benchmark.

## 3. Measured performance

| Input and mode | Import | Rows assigned a category | Provisional correct families | CPM working days |
|---|---|---:|---:|---:|
| Original, rules | Pass | 38.9% | 6/18 (33.3%) | 16,908 |
| Original, retrieval | Pass | 100% | 8/18 (44.4%) | 1,135 |
| Original, hybrid | Pass | 100% | 9/18 (50.0%) | 1,127 |
| Companion, unchanged upload | **Fail** | — | — | — |
| Companion, header adapter + rules | Diagnostic only | 66.7% | 10/18 (55.6%) | 584 |
| Companion, header adapter + retrieval | Diagnostic only | 94.4% | 13/18 (72.2%) | 88 |
| Companion, header adapter + hybrid | Diagnostic only | 94.4% | 11/18 (61.1%) | 212 |

Header adaptation changes only D4:F4 in an in-memory copy to `Quantity`, `Rate`, `Amount`. Production does not accept this file as-is. The original uses multi-row bidder headers; that separate issue must also be addressed before priced comparative statements can be ingested correctly.

For the 14 rows with supported family labels, original hybrid correctness is 9/14 (64.3%), and adapted-companion hybrid correctness is 11/14 (78.6%). These are provisional diagnostic scores, not calibrated model probabilities.

The original hybrid finish is 30 November 2029. The companion hybrid finish is 29 December 2026, versus the supplied scenario's 7 August 2026. The 88-day retrieval schedule happens to be close to 90 days but still misclassifies filter media and vent piping and retains a terminal miscellaneous activity. A close finish date is insufficient evidence of a correct schedule.

### Original BOQ hybrid row review

| Source row | Meaning after reading source context | Model family | Expected family | Correct at broad family level? |
|---|---|---|---|---|
| 10 | Excavation, all soils | testing | earthwork | No |
| 11 | Return fill using excavated earth | earthwork | backfill | No |
| 14 | PCC 1:4:8 | rcc | pcc | No |
| 17 | Formwork to foundations/walls | formwork | formwork | Yes |
| 21 | Fe-550D reinforcement | reinforcement | reinforcement | Yes |
| 26 | M30 foundation concrete | earthwork | rcc | No |
| 27 | M30 wall/column/beam/slab concrete | rcc | rcc | Yes |
| 29 | Boulder filter layer | rcc | filter_media | No; category absent |
| 30 | Gravel filter layer | testing | filter_media | No; category absent |
| 31 | Sand filter layer | rcc | filter_media | No; category absent |
| 33 | Bore drilling | borewell | borewell | Yes |
| 34 | Screen/slotted pipe installation | borewell | borewell | Yes; needs separate operation |
| 36 | Safety foot rests | rcc | access_metalwork | No; category absent |
| 37 | Pea gravel around bore | borewell | borewell | Yes; needs separate operation |
| 38 | PVC vent installation | road_reinstatement | plumbing | No |
| 39 | RCC manhole cover/frame | manhole | manhole | Yes; installation is not chamber construction |
| 40 | MS manhole cover/frame | manhole | manhole | Yes; installation is not chamber construction |
| 41 | Air-compressor cleaning | borewell | borewell | Yes; hours need an appropriate conversion |

## 4. Root causes established by experiments

### A. Import discards the information needed to classify

`buildflow/ingestion.py:53` accepts description/quantity leaf rows and skips parents with no quantity. It preserves numeric quantities, but not the parent work specification, section, grade or continuation lines. Exact header matching at line 14 rejects `Qty (x2 tanks)` and does not recognise the bidder headers on the following row. `read_excel` at line 113 returns the first usable sheet and does not associate reference schedules or ledgers with it.

**Controlled test:** attaching parent and section text in memory changes the original hybrid duration from 1,127 to 103 days. However, correctness remains 9/18 and mean heuristic confidence rises from 69.7% to 85.2%. This demonstrates why concatenation alone is inadequate.

### B. Negation and incidental words dominate the rule engine

`buildflow/taxonomy.py:115` uses substring matches. “excavate” matches “excavated earth” in backfill, producing a wrong earthwork label at 98% confidence. Adding parent concrete specifications exposes “excluding … centering and shuttering”; the model then labels PCC and RCC rows as formwork at 98% confidence. The companion's “screen/slotted pipe” becomes mechanical equipment because of “screen”.

Retrieval uses token-count cosine similarity against a small handwritten example set, without stopword filtering or a meaningful abstention threshold. Any positive best score can assign a class (`taxonomy.py:150`), including weak similarity from generic words. Confidence is a hand-built score (`taxonomy.py:158`), not a measured probability.

The 98%-confidence backfill mistake would bypass the current low-confidence-only AI fallback. Adding an LLM without contradiction checks would leave this error untouched.

### C. Invalid package/unit combinations become huge durations

`buildflow/scheduling.py:75` silently falls back to a generic capacity. On the original BOQ, the testing stage receives 1,027.6 m³ of excavation plus 49.98 m³ of gravel. Testing's fallback is 1 unit/day, resulting in **1,078 days of testing**.

In the adapted companion, 120 m of screen pipe is mechanical equipment with a fallback of 1 unit/day. Combined with foot rests and manhole cover/frame rows in `OTH`, it produces **144 terminal miscellaneous days**. Unknown reinforcement under the rules baseline similarly turns thousands of kg into thousands of days.

This is a dimensional validation failure. A model must not silently treat m³ as tests or metres as pieces of equipment.

### D. RWH activities and resources are incomplete

`buildflow/scheduling.py:133` lacks dedicated filter-media and access/cover-installation stages. Drilling, screen installation, packing and cleaning are grouped despite different units and sequencing. Unallocated work is appended after all other work (`scheduling.py:245`), even when it belongs earlier.

The template calls the RCC crew “shared”, but both tanks' RCC work overlaps: for the adapted companion hybrid run, Tank 1 is 12 May–20 June and Tank 2 is 26 May–4 July. This requires separate crews or resource-constrained links; the current network does not enforce the stated assumption. It also adds separate waterproofing stages without BOQ allocations, although the concrete specification mentions an admixture; the necessity of those stages requires planner confirmation.

**Oracle-label diagnostic:** manually correcting the supported families while marking the four missing categories unknown still produces 220 days. Removing those four rows reduces it to 58 days, but omits scope and is not a usable schedule. Classification improvement alone cannot fix the incomplete taxonomy, productivity compatibility and activity structure.

### E. Success metrics conceal missing evidence

`buildflow/agent.py:98` defines classified percentage as “not unknown”. It therefore displays 100% when half the provisional family labels are wrong. A zero-valued source reconciles to a zero-valued schedule; this is arithmetic equality, not monetary completeness.

`buildflow/cashflow.py:176` compares two model-generated curves, not actual cash flows. With the unpriced source it reports 0% divergence and an arbitrary independent peak month. These should be unavailable. Its average error is also diluted when a bad schedule creates many extra months; evaluation should fix the horizon and report duration/tail error alongside curve metrics.

`cashflow.py:148` uses the CPM duration when contract duration is absent, while `agent.py:111` always claims independence. The missing-contract experiment confirms the independent curve changes. This directly conflicts with the agreed independence of the two BTP projects.

Two secondary issues: the completion milestone is dated on the next working day after the last positive-duration activity, and the monsoon warning checks only project-start month (`validation.py:99`), missing April starts whose sensitive activities extend into June/July.

## 5. Prioritised improvement plan

| Priority | Change | Acceptance check |
|---|---|---|
| P0 | Preserve section/parent/continuation descriptions and source cell references; parse multi-row headers and explicit bidder selection | Recover all 18 items with complete context; accept annotated quantity headers without discarding scope; keep rates missing when blank |
| P0 | Require valid package–operation–unit combinations; abstain on unresolved rows | No excavation volume becomes test-count workload; no metre-based pipe becomes equipment-count workload; unsupported rows visibly prevent a complete baseline |
| P0 | Separate coverage, review status, pricing completeness and measured accuracy | The current unpriced BOQ shows monetary outputs unavailable, not ₹0/0%-error success |
| P1 | Add filter-media, cover/access installation and borewell suboperations | Every row has an appropriate activity and compatible productivity basis, with no arbitrary terminal placement |
| P1 | Use action-aware classification with exclusion/negation handling; contextual LLM fallback for conflicts as well as low confidence | Correct backfill and excluded-formwork examples; do not reuse their old heuristic confidence as probability |
| P1 | Model per-tank versus project-total quantities explicitly | Verify scope with estimator/drawings; never infer automatic doubling from “2 tanks”; document each allocation |
| P1 | Enforce crew constraints and represent pours, curing, filter placement and borewell prerequisites explicitly | Shared crews do not overlap; required filter/cover work precedes appropriate completion/testing milestones |
| P1 | Require an independently supplied duration for Lakshay's independent forecast | Omitted duration yields a clear missing-input state, never silent CPM substitution |
| P2 | Calibrate productivity, weather and cash phase patterns against verified site records | Evaluate on a different held-out project; report activity errors and monthly/cumulative planned-value errors separately from actual receipt errors |

The useful agent extension is a validation/review workflow: detect missing scope or incompatible units, present the affected source cells, request the specific planner input, then rerun deterministic scheduling. It should not invent rates or adjust productivity merely to hit the companion's 90-day finish.

## 6. Evidence needed from the project team

1. Confirmation of whether civil quantities are for one or two tanks, and whether all four bores are already included. Request the quantity take-off or relevant drawings.
2. The selected contractor's signed priced BOQ, approved baseline and actual start/finish dates, together with crew records for major activities.
3. Dated monthly work valuations/RA bills and, for actual cash-flow validation, receipt/payment records and advance/retention terms. Clarify the companion's five month boundaries and how its cash table was prepared.

Use the supplied files now as an ingestion/classification regression case. Do not present the 90-day schedule or ₹31.95 lakh cash total as independently verified actual outcomes.

## Reproduction and verification

From the repository root:

```sh
PYTHONPATH=. python3 outputs/rwh-review-2026-09-19/evaluate.py
python3 -m pytest -q
```

The existing test suite passed during this review despite these failures, demonstrating a coverage gap for hierarchical real-world BOQs. The evaluation script verifies that source hashes are unchanged and retains complete run details in `results.json`. No production fixes were made as part of this diagnostic review.
