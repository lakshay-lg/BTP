# Normalized BOQ Import Implementation Plan

**Goal:** Locally verify chat-normalized JSON against original workbooks and provide an explicit review gate before calculations.

**Architecture:** Separate strict schema parsing, source verification and calculation admission. Preserve full nullable intake data. Only verified and explicitly reviewed compatible items enter the existing engineering engine; retain their classifications and source evidence.

**Tech Stack:** Python, jsonschema, openpyxl, Flask, existing JavaScript/CSS.

**Spec:** `docs/normalization/WORKFLOW.md` and `CHAT_NORMALIZER_PROMPT.md`. User approved this direction on 19 September 2026. Execute sequentially in the existing workspace; preserve unrelated/untracked work, no commits or external AI calls.

## Constraints

- Schema version `buildflow.normalized-boq.v0.1`.
- Read uploaded bytes only; never resolve paths from model data.
- Preserve missing versus zero prices and source quantities.
- Reject changes to source facts, omissions and unsupported scheduling operations.
- AI proposals are not review approval. Approval requires a separate human action.
- Missing prices block complete cash forecasts, not an otherwise valid quantity schedule.
- Independent forecast requires an explicitly confirmed independent duration.

## Tasks

- [ ] Add tests with real in-memory XLSX fixtures: valid quantities, changed quantities, duplicate/omitted rows, fabricated evidence, strict JSON, zero/missing prices, operation/unit compatibility and unconfirmed approval.
  Run `python3 -m pytest tests/test_normalization.py -q`; observe missing feature failures before implementation.
- [x] Implement `buildflow/normalization_schema.py`: `load_document(content: bytes) -> dict`; strict JSON loader, bounded v0.1 JSON Schema, public schema endpoint.
- [x] Implement `buildflow/normalization.py`: `review_document(content: bytes, sources: dict[str, bytes]) -> dict`; source hashes, independent workbook inventory, exact cell/number checks, issue ledger and separate source/schedule/cash statuses.
- [x] Add tested calculation admission in `buildflow/normalized_analysis.py`: revalidate every submitted original/JSON pair; require separate reviewer, description/inventory approval, project-total scope confirmation, selected typology/start and independent-duration confirmation when requested. Preserve classifications and unavailable monetary outputs.
- [x] Extend `ProjectAgent.run` through a reviewed path without changing existing sample classification; preserve review provenance through replan/export.
- [x] Add review/analyze and prompt/schema download endpoints. Endpoint tests must assert that approval cannot bypass source errors and invalid JSON never becomes a 500.
- [x] Add a separate chat-import page linked from the workbench, with two upload inputs, review table, source issues, explicit approval controls and a reviewed-draft result/download. Use existing visual style and escaped text. Keep legacy upload unchanged.
- [x] Verify full tests and browser flow at desktop/mobile widths. Update README, prompt/schema alignment and workflow implementation status, including conservative unsupported-operation limitations.

## Review cases

Verification on 19 September 2026: `python3 -m pytest -o addopts='' -q` — 63 passed; JavaScript syntax check passed. Local browser exercised original+JSON upload, matched review, explicit planner confirmation, draft generation and Excel download (HTTP 200, 14,272 bytes). Mobile viewport 390 px had document width 390 px. Synthetic fixtures verify the mechanism; actual consumer-chat RWH extraction remains a separate pilot.

`quantity: 999` with source `D3: 12` must fail even if all summary counts match. Removing a quantity line from both JSON items and its inventory must fail independently. A source zero amount stays zero, an absent amount stays null. A claimed testing operation with m3 must be blocked. Review approval cannot remove any source-verification failure. Changing a source file between review and calculation requires fresh verification.
