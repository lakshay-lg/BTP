# Existing-chat BOQ normalisation: immediate workflow

Status: the v0.1 prompt, strict JSON importer, source checks, field decision editor and separate planner approval page are implemented at `/` and `/normalize`. Reviewed drafts use `buildflow.reviewed-boq.v0.1` and retain the original proposal. Ready for a supervised pilot; not yet evaluated with actual outputs from consumer chat services. Unsupported operations remain blocked.

## Decision

Use the client's existing chat service for a deliberate, manual document-normalisation step. Upload an original BOQ with `CHAT_NORMALIZER_PROMPT.md`, download `normalized_boq.json`, then submit both the original and the normalised file at `/normalize`.

This removes the need for an app API call during normalisation. Usage remains subject to the user's chosen chat service and plan. A user should use the provider their organisation permits for the BOQ. No automatic access to consumer chat sessions, subscription credentials or browser scraping is required.

The same prompt can be used as a normal chat message; a privileged system prompt is unnecessary. Provider-specific attachment and download abilities may differ. The prompt includes an honest failure path if the model cannot inspect the source and a JSON-code-block fallback if it cannot create a downloadable file.

## Why this is useful here

The original RWH workbook's structure is a natural document-reading task: descriptions are spread across parent and child rows. A capable chat model can propose reconstruction, labels and ambiguity notes. The code then checks preservation and compatibility. Scheduling and cash arithmetic continue to run locally.

The legacy ingestion path accepts flat CSV/XLSX. A cleaned description alone is insufficient: the classifier could still select formwork from an exclusion or excavation from backfill wording. The new importer preserves proposed labels, validates them, and honours human-approved classifications without silently running the keyword classifier over them again.

Three options were considered:

| Option | Advantage | Limitation | Decision |
|---|---|---|---|
| Ask chat for a flat CSV and upload today | Quick demonstration | Drops structured evidence/issues; current parser collapses blanks to zero and current classifier can reintroduce errors | Do not use as a trusted workflow |
| Ask chat for structured JSON and validate against the original locally | No app API key, explicit nulls, parent-cell evidence and review questions | Requires a small new import/review path | Recommended immediate pilot |
| Call an LLM API automatically | Fewer manual steps for repeat users | Additional credentials, billing and integration | Optional later using the same contract |

JSON is the canonical exchange, not the required user-facing format. The application can later present editable tables and Excel exports. Keeping one versioned contract avoids three incompatible provider-specific output formats.

## Do immediately

1. Test the prompt in a fresh chat using only `BOQ RWH (1).xlsx`. Do not attach the companion generated schedule/cash workbook as source truth.
2. Save the full response/file. Record provider, displayed model label, date and prompt version separately; do not invent model details the UI does not disclose.
3. Return the JSON alongside the original workbook for local verification. Confirm civil quantity scope with the estimator before doubling anything.
4. Inspect local checks and confirm scope and descriptions before generating a draft. Address the RWH operation/unit/template gaps before expecting a complete RWH schedule; never relabel unsupported work merely to pass validation.

## Implemented pilot limits

- Original uploads are XLSX/XLSM, with a combined request limit of 20 MB; chat JSON is limited to 2 MB and reviewed envelopes to 5 MB. Workbook layout detection is conservative and does not support every tender layout. Failed inventory checks require investigation, not an approval override.
- Supported operations currently cover excavation, return fill, plain/reinforced concrete, formwork, reinforcement, bore drilling and waterproofing, subject to unit and template compatibility. Filter media, bore cleaning and other unsupported work block a complete schedule.
- Effective quantities must be confirmed project totals. Explicit quantity overrides remain labelled; reasons are optional. Automatic per-tank scaling is not implemented. Contract durations entered in the review page are working days.
- Schedule-only output permits missing prices, displays monetary totals as unavailable, and emits no cash curve. Cash comparison requires complete prices and a separately confirmed contract duration.
- Review records include original evidence, file hashes, reviewer name and timestamp, including in Excel exports. The name is a local acknowledgement, not authenticated identity or a digital signature.
- The page edits quantity, unit, rate, amount, normalized description, work package and operation through recorded decisions. It offers deterministic evidence repair where source cells are unambiguous. Structural errors without a safe correction (missing rows, ambiguous headers/bidders, invalid identities) require correcting the normalized JSON and resubmitting. Source checking verifies preservation, not semantic correctness or calibrated productivity.

## Review, correct, save and run

Every reported check includes repair guidance. Eligible item fields show the Excel value, original AI proposal and available decision choices. Source suggestions are derived locally, not generated by another AI call. Decisions require a reviewer; reasons are optional and blank/omitted reasons are recorded as **Not provided**. The original proposal is never rewritten. **Use AI values for all editable fields** selects all eligible proposals, including null values, using an optional shared reason. Existing choices require replacement confirmation; evidence repairs are untouched. Individual choices can still be edited after bulk selection.

**Recheck changes** recomputes effective inputs and remaining blockers on the server. **Save reviewed JSON** downloads a reusable draft envelope with decisions and original workbook hashes. The save does not grant approval and can retain unresolved checks. **Run reviewed model** uses the effective values after fresh planner confirmation and all applicable checks. Editing invalidates the prior ready/result state.

To resume, upload the saved reviewed JSON plus exactly the same original workbooks. Changed workbook bytes cause a hash mismatch, requiring a new review. Saved reviewer names/timestamps are historical acknowledgements, not authenticated provenance.

Source-matched, corrected-to-source, override-acknowledged and unresolved states remain distinct. Results using assumptions or values not supported as source facts show **Planner-overridden inputs** and include the full decision ledger in Excel. Structural omissions, fabricated quotations without repair, unsupported operations, invalid workloads and incompatible units cannot be approved away. Unit changes require an explicit quantity decision; inconsistent effective amounts require explicit monetary review before cash-flow output. Reasons are optional for both.

A good pilot is one successful whole-workbook extraction with no numeric changes, no dropped items and explicit unresolved issues. A finish date close to 90 days is not a normalisation acceptance test.

## Local validation design

Validation must read the original file independently. Checking that a JSON document is syntactically valid or that its self-reported counts agree is not enough.

### Structural and source checks

- Require the exact schema version and `complete: true`; reject duplicate JSON keys, missing required fields, non-finite numbers, extra unrecognised fields and oversized payloads.
- Match source filenames to explicitly supplied local files, calculate their hashes locally, and never fetch a path or URL supplied inside JSON.
- Check sheet/cell existence and compare raw description, parent quotations, units, quantities and selected prices against those cells. Source numeric precision must be preserved with decimal-safe comparison. Report any necessary locale interpretation instead of guessing.
- Recompute eligible quantity × rate amounts locally. Preserve source zero and missing price separately. Detect formula cells without caches and retain the formula and unresolved status. Do not execute arbitrary formulas supplied as strings by the model.
- Reject duplicate item IDs and duplicate commercial source rows. Independently enumerate candidate item rows; reconcile them with the AI inventory, including zero and missing-quantity lines, merged cells and multi-row headings. Flag ambiguous candidates for human review. An AI inventory is evidence to check, not the completeness authority.
- Preserve source descriptions alongside normalised descriptions. Exact cell matching verifies extraction, not semantic faithfulness; a reviewer must inspect reconstructed scope and exclusions.

### Engineering and monetary checks

- Separate the normalisation vocabulary from the scheduler's supported operation/unit registry. A valid proposed category must not imply supported scheduling.
- Require compatible operation/unit/productivity definitions. No generic “1 unit/day” duration for unresolved or unsupported work. Keep missing productivity as a blocking item.
- Require confirmed quantity basis before allocating per structure. Do not multiply quantities during import.
- Treat model work packages as proposals. Record human review/override, reviewer and time locally. The AI cannot approve its own result. Permit explicit abstention.
- Block complete monetary forecasts on missing prices; show pricing completeness. Quantity-based scheduling can proceed independently if its own inputs are sufficient.
- Require independently provided contract duration and its time basis for the independent phase forecast. Never fill it from CPM dates or a reference schedule silently.
- Preserve each finding as either extraction-blocking, schedule-blocking or cash-flow-blocking. Valid source extraction can coexist with an unavailable baseline.

### Integration boundary

Add a dedicated `NormalizedBOQDocument` intake model rather than immediately turning every JSON item into today's `BOQItem`. The latter defaults missing amounts to numeric zero and has no representation for source-parent context, quantity basis or review approvals.

Convert only reviewed, compatible records into the calculation model. Retain unsupported rows in the project review ledger; do not omit their scope or make the resulting partial schedule look complete. Record every accepted label and planner decision in the audit trail. Preserve a rules/retrieval run as an evaluation baseline, not an automatic overwrite of reviewed labels.

## RWH pilot acceptance cases

Use the original file's `CS for RWH` sheet. These are developer/reviewer checks; keep them separate from a general prompt performance test to avoid supplying the model with expected answers.

| Source rows | Expected extraction/behaviour |
|---|---|
| 10, with parent 9 | 1,027.6 Cum unchanged; excavation reconstructed from B9; not testing |
| 11 | 205.52 Cum unchanged; backfill despite “excavated earth” |
| 14, with parent 13 | 10.65 Cum of PCC 1:4:8; excluded formwork remains an exclusion |
| 17, with parents 15–16 | 608.96 Sqm of formwork |
| 21, with parents 18–20 | 14,840.57 Kg of reinforcement; no kg-to-tonne numeric change |
| 26–27, with parents 22–25 | M30 concrete: 50 and 104.53 Cum; neither becomes formwork |
| 29–31 | Three filter layers, each 49.98 Cum; mark missing scheduler operation support |
| 33–34 | Drilling and screen installation are separate operations, each 120 meter |
| 36 | 12 foot rests; access metalwork, not RCC production |
| 37 | 8 cum of bore packing; do not use drilling metres/day |
| 38 | 64 meter of vent piping, not road reinstatement |
| 39–40 | 5 and 1 cover/frame installations; not whole chamber construction |
| 41 | 8 Hrs remains hours; no fabricated days or bore lengths |
| 44 | Record two-tank scope text and ambiguity; do not double line quantities |
| 50 | Completion duration absent; do not populate 90 or 150 days |
| Bidder columns E:J | All leaf price cells blank; preserve null rate/amount, not ₹0 project value |

Expected item rows: 10, 11, 14, 17, 21, 26, 27, 29, 30, 31, 33, 34, 36, 37, 38, 39, 40, 41. Expected total count: 18. Missing prices and unresolved scope should remain visible even if all descriptions are reconstructed correctly.

## How to evaluate the prompt

Use the same original source and prompt version for each selected chat service. Keep the model output untouched. Score independently:

1. Item recall and duplicate rate against manually reviewed source rows.
2. Exact quantity/unit preservation, with explicit missing-versus-zero checks.
3. Source-reference validity and faithful inclusion/exclusion reconstruction.
4. Proposed family and operation correctness, including abstentions and unsupported categories.
5. Unresolved issues correctly surfaced, plus manual corrections/time required.

Run a second extraction to assess repeatability before choosing a default workflow. Save both attempts, including failed ones. Do not compare only the best response from each provider. Human approvals are separate from AI classification scores.

The RWH case is development evidence already used to design this prompt. Use at least one other project held out from prompt/rule development for any reported generalisation claim. Real schedule/cash-flow accuracy still requires approved baseline/actual records; normalisation success does not supply those records.
