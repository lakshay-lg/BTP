# Reviewed BOQ corrections and explicit overrides

## Status and objective

**Subsequent user-approved amendment:** Reasons are now optional for individual and bulk decisions; blank/omitted reasons are stored as `Not provided`. Reviewer identity, explicit quantity/amount decisions, source evidence preservation, override warnings and fresh checks remain required. A bulk **Use AI values for all editable fields** action selects existing proposals, asks before replacing choices, and leaves evidence repairs untouched. This amendment supersedes mandatory-reason requirements below.

Approved design, implemented in the current workspace. The user approved allowing AI/manual values that contradict Excel when a reason and an overridden-input warning are recorded. Verification is recorded in the implementation plan.

Turn source-check findings into an actionable workflow: inspect source evidence, choose source/AI/manual values, recheck, download a reusable reviewed JSON file, and calculate from the reviewed values. Existing plain v0.1 normalization files remain accepted. No new AI API calls are introduced.

## Design choice

Use a separate, versioned review envelope around the untouched AI normalization document. Store corrections and overrides in a decision ledger and derive effective calculation inputs locally.

Alternatives rejected:

- Overwriting the AI document loses the distinction between original claims and reviewer decisions.
- Ignoring all errors after approval allows missing scope and invalid engineering inputs to enter calculations.

The selected design preserves evidence while permitting intentional, visible planning assumptions.

## User experience

Each check shows its explanation and a specific next action. Field-level findings show the item, field, original Excel value and cell, AI-proposed value, selected effective value, and resolution status.

Available actions, only when meaningful:

- **Use Excel:** adopt a locally read, unambiguous source value.
- **Accept AI value:** retain the proposal explicitly. A source conflict requires a reason.
- **Enter manually:** type a replacement, with a reason and field-specific validation.
- **Undo decision:** restore the unresolved state without modifying the original proposal.

Suggestions computed from Excel are labelled **Source-based suggestion**. AI values mean proposals already present in the uploaded chat output, not newly generated advice. Where neither source nor AI offers a usable value, show manual entry or specific repair guidance; never fabricate a suggestion.

Every finding receives actionable guidance, but not every finding gets an override button. File/row/reference errors require repairing evidence; unsupported engineering operations require a supported model definition, not approval alone.

Controls:

1. **Recheck changes:** validates decisions against uploaded originals and recomputes readiness.
2. **Save reviewed JSON:** downloads a versioned review envelope including unresolved findings as a clearly labelled draft. This saves to the user's device, not a new server database.
3. **Run reviewed model:** enabled only after fresh checks, explicit planner approval and no remaining calculation blockers.

Reuploading a reviewed JSON plus its original workbook(s) restores decisions. Re-running still requires current confirmation; a saved reviewer name is not authenticated approval. Changing any decision or uploaded file invalidates the last readiness result and hides stale output.

## Editable scope

Initial field decisions cover quantity, canonical unit, rate, amount, normalized description, work package and operation. Coupled fields are validated together: operation/package/unit compatibility and quantity/rate/amount consistency.

Raw source descriptions, quotations, formulas, filenames and cell identities are evidence, not editable planning assumptions. Where there is exactly one independently identifiable correct source cell/value, offer a source-based correction patch to the extracted evidence. Otherwise show how to repair the original normalized file and resubmit. Do not allow an invented source quotation to be accepted as authentic.

Unit changes never silently convert quantities. If the proposed quantity and chosen unit imply a conversion, require explicit compatible decisions and a reason. Null is distinct from zero. Reject booleans, non-finite numbers, invalid enum values and out-of-range values.

For monetary changes, offer a local quantity × rate suggestion only when the basis is explicit. Preserve a conflicting source amount as evidence; require an explicit amount decision before claiming consistent monetary inputs. Do not silently retain stale amounts after quantity/rate edits. Missing prices continue to allow schedule-only runs but block complete cash forecasts.

## Validation and admission

Maintain separate statuses:

- **Source matched:** the relevant extracted facts agree with Excel.
- **Corrected to source:** the reviewer accepted a verified extraction correction.
- **Override acknowledged:** effective planning values differ from source, with a recorded reason.
- **Unresolved:** still blocks the affected output.

Keep the original source-check results. An acknowledged discrepancy remains visible and must not be relabelled source matched. Calculation readiness is a separate server-computed decision, not the current single `source_verified` flag.

Checks that remain non-overridable include malformed schemas, missing/duplicate item scope, missing originals, ambiguous source identities, incompatible operation/unit definitions, invalid workload and unsupported template allocations. Suggestions for these explain the required repair. Existing independent-duration and project-total-scope confirmations remain required.

Overrides apply to exact fields and exact eligible checks, never to every finding with the same code. After decisions, rerun engineering and monetary checks against effective inputs. Client-supplied status, totals, suggestions or claims of approval are never authority.

## Saved contract

New envelope version: `buildflow.reviewed-boq.v0.1`.

Required contents:

- `normalized_document`: original v0.1 proposal, unchanged.
- `source_sha256`: locally computed hashes of each uploaded original.
- `decisions`: stable item/field targets, action (`source`, `ai`, `manual`), selected value, reason, reviewer and recorded timestamp. Evidence repairs use a restricted separate correction type with locally derived patches.
- `review_settings`: project inputs, not a reusable approval token.
- `review_state`: draft metadata; always recomputed at import.

Use bounded strict schemas, reject duplicate decision targets and unknown item IDs, and impose an explicit reviewed-envelope size limit of 5 MB within the existing 20 MB request limit. Preserve null values and decimal-safe comparison. Do not embed entire workbook binaries or editable cached source values as verification evidence.

The server derives source/AI action values itself. It rejects inconsistent submitted values, altered originals and stale targets. A user editing a downloaded JSON cannot bypass validation. Hashes detect a change of source, not authenticated authorship; local reviewer names are acknowledgements rather than signatures.

## Component boundaries

- `normalization.py`: enrich check objects with stable targets, source/proposed values, resolution eligibility and repair guidance; keep original validation behaviour for plain files.
- New resolution module: validate the review envelope and ledger; apply restricted source corrections; derive effective rows and an audit record; recompute readiness.
- `normalized_analysis.py`: consume effective rows only after admission. Preserve current conservative capability checks and disable automatic reclassification.
- `web.py`: accept either plain or reviewed JSON; add a recheck/save path sharing the same server resolution logic as analysis. No permissive alternate calculation endpoint.
- `normalize.html` and dedicated review JavaScript: render source/AI/manual controls, show draft/dirty states, submit decisions and download the envelope. Escape all source and user text.
- `exporter.py` and replan flow: retain decisions, original evidence, effective values and a prominent override warning.

Existing API callers submitting plain normalization JSON without decisions keep strict source checking. The new envelope is not silently treated as the legacy v0.1 schema. The standard CSV/Excel workbench remains unchanged.

## Output behaviour

Calculate schedule/cash curves from effective values, not original proposals. Results show **Planner-overridden inputs** whenever effective inputs depart from source or add assumptions unsupported by it. Include item, field, source value, AI value, chosen value and reason in the audit/export. No increased confidence score is invented from approval.

Overrides do not make unsupported RWH operations schedulable; additional productivity/template support remains separate work.

## Verification

- Excel quantity 120 / AI 240: source choice uses 120; AI acceptance uses 240 only with a reason and warning; manual choice uses the valid entered value.
- Changing a decision changes effective workload; source evidence stays 120.
- Missing reason, invalid numeric input, duplicate targets and fabricated source patches fail server validation.
- Editing quantity/rate exposes stale amount conflicts and requires resolution for cash runs.
- Unit-only changes cannot silently reuse incompatible quantities or productivity.
- Missing prices remain unavailable rather than zero; explicit zero remains zero.
- Missing rows, invalid references and unsupported operations cannot be waived by a field decision.
- Save/reload preserves decisions; changed original bytes trigger a source-hash mismatch.
- Imported status/approval cannot enable a run without current checks and confirmation.
- Browser checks cover editing, recheck, draft save, reload, blocked run, valid run, stale-result invalidation, keyboard use and mobile layout.
- Regression suite covers existing plain imports, legacy workbench, exports and replanning.

## Non-goals

No new AI API integration, new productivity models, arbitrary spreadsheet editing, universal evidence repair, authentication system, database or automatic cloud saving. No assertion that human overrides validate real project schedule accuracy.
