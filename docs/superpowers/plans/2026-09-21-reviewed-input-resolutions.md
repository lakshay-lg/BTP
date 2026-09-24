# Reviewed-input resolutions implementation plan

**Goal:** Edit source-check resolutions, save/reload reviewed JSON, and calculate from explicitly reviewed effective inputs without erasing evidence.

**Architecture:** Keep plain normalization checking strict. Add a bounded review envelope, locally derived field suggestions and restricted evidence repairs, then a resolver producing effective inputs and separate admission checks. The UI submits decisions, never trusted readiness flags.

**Spec:** `docs/superpowers/specs/2026-09-21-reviewed-input-resolutions-design.md` (approved).

**Execution:** Sequentially in the existing user workspace; the current untracked project is the running app. Preserve unrelated files and do not create commits or a divergent worktree that omits this code. No agents or external AI calls.

## Constraints

- Plain v0.1 remains strict; reviewed envelope v0.1 capped at 5 MB.
- Source/AI values derived on server; originals bound by SHA-256.
- Non-source decisions require reviewer and reason; no persisted approval token.
- Field-level decisions cannot waive structural/evidence/capability errors.
- Recheck all effective engineering and monetary values. Preserve null versus zero.

## Tasks

- [x] Tests: add `tests/test_resolutions.py` using `fixture_pair`, HTTP review/analyze calls and explicit decisions. Assert quantity 999 rejected plain, source choice 12 accepted, AI/manual choice used with warning, missing reason denied, source mismatch persists as evidence, missing rows/unsupported units remain blocking. Run and observe failures before implementation.
- [x] Schema/source suggestions: create `buildflow/review_schema.py`, `buildflow/review_options.py`. Share strict parser with existing schema; derive actual source values from independently recognized columns. Offer evidence repair only for unique valid identities/columns, never ambiguous bidder selection.
- [x] Resolver: create `buildflow/resolutions.py` exposing `review_input(content, sources)`. Validate envelope/hash/targets, derive effective rows and amount consistency, retain original checks and decision audit. Test save/reload, modified originals, stale amounts, unsupported operation, unit/quantity coupling, malformed input and forged source selection.
- [x] Admission/integration: route review/analyze/save through resolver; calculation uses `calculation_checks` and `admission_ready`, not a forged source_verified flag. Preserve decisions and warnings in replan/export. Test real export workbook and replan output.
- [x] UI: add dedicated `static/review-decisions.js`, field editors with source/AI/manual choices and evidence repair; modify normalize UI to recheck/save/run with dirty state, retained settings, fresh approval and warnings. Browser-test valid/blocked paths and reupload of saved envelope.
- [x] Verification/docs: full pytest and JS tests, desktop/mobile browser checks, update workflow limits, report exact tests and remaining repair limitations. No server restart without resolving the listening process.

## Test commands

```sh
python3 -m pytest tests/test_resolutions.py -q
python3 -m pytest -o addopts='' -q
node tests/test_chat_handoff.cjs
node --check static/normalize.js
node --check static/review-decisions.js
```

## Acceptance examples

```python
assert resolved['items'][0]['quantity'] == 12  # Use Excel, not AI 999
assert resolved['document']['items'][0]['quantity'] == 999  # original proposal retained
assert not accepted_ai['source_verified']
assert accepted_ai['admission_ready']  # explicit field decision, no structural failures
assert accepted_ai['has_overrides']
```

Download is a review envelope plus decisions, not a plain edited proposal. Reupload requires the exact originals and fresh human confirmation. All diagnostics retain repair guidance even if no automatic patch exists.

## Verification completed — 21 September 2026

- Full pytest: **89 passed**. Node behavior tests: **7 passed**. Both modified JavaScript files pass syntax checks.
- Independent read-only code review found no critical or important issues in the resolver/admission path.
- Browser: mismatched quantity initially blocks; manual decision changes effective quantity to 24 while Excel 12 and AI 999 remain recorded. Dirty edits disable save/run. Recheck, saved JSON download, model run, saved-file reupload and fresh confirmation verified.
- Export returned HTTP 200 (16,091 bytes); automated workbook tests confirm Input Decisions, correct effective quantities, missing-price preservation and warning retention through replan.
- Desktop 1440 px and mobile 390 px fit without page overflow; inspected screenshots. Browser console: zero errors or warnings.
- No new productivity support or universal structural repair is claimed. Source-guided repairs require unambiguous evidence; remaining errors show explicit repair guidance.
