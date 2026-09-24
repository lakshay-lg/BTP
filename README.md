# BuildFlow

BuildFlow is a working BTP prototype for converting a priced construction BOQ into:

- classified, standardised BOQ rows with confidence and evidence;
- a typology-aware, deterministic CPM schedule;
- a schedule-linked monthly planned-value/cash-flow curve;
- an **independent** contract-duration/phase-pattern cash curve;
- risk findings, cost reconciliation and a step-by-step agent trace; and
- an Excel workbook suitable for planner review.

It implements the useful combination from the project decision trail: **AI/retrieval reads; established engineering logic plans; deterministic arithmetic totals.** The “agent” orchestrates tools and records its decisions. It never calculates dates or costs in free-form model text.

## Run it

The environment needs Python 3.11+ and the dependencies in `pyproject.toml` (including Flask, openpyxl and jsonschema).

```bash
python3 app.py
```

Open <http://127.0.0.1:5050> for the chat-normalization launcher and file review. The [standard workbench](http://127.0.0.1:5050/workbench) retains the STP, linear MEP and RWH demonstrations and legacy CSV/Excel upload.

Run the core without the browser:

```bash
python3 -m buildflow.cli data/samples/rwh_boq.csv \
  --name "RWH validation case" \
  --typology rwh \
  --start 2026-04-25 \
  --contract-days 90 \
  --structures 2 \
  --output RWH_analysis.xlsx
```

Run tests and the reader ablation:

```bash
pytest
python3 -m buildflow.benchmark
```

## What is implemented

```text
CSV/XLSX BOQ
    │
    ├─ parser + header/amount checks
    │
    ├─ rules ───────────────┐
    ├─ local retrieval ─────┼─ hybrid work-package classifier
    └─ opt-in AI fallback ──┘           │
                                        ├─ typology template (STP / MEP / RWH / building)
                                        ├─ productivity durations + CPM solver
                                        ├─ schedule-linked monthly curve
                                        └─ independent phase-pattern curve
                                                    │
                                         reconciliation + risks + Excel
```

The three scheduler templates encode different construction structures:

- **STP/deep tank:** one-pass excavation → base → raft → walls → roof sequence, with waterproofing, equipment and leak-test hold points. CPWD/DSR-style “extra lift” surcharges are treated as cost-only rows, not duplicate excavation volume.
- **Linear MEP:** sewer, storm-water, pressure-pipe and internal-drainage tracks start independently and converge before reinstatement/testing.
- **RWH:** tank work uses a shared-crew offset while borewell drilling runs as an independent specialist track.
- **RCC building:** substructure/superstructure, masonry, MEP and finishes template. Its aggregate floor cycle must be calibrated with actual floor count before research use.

The browser also allows a planner to edit durations. Replanning re-runs cycle detection, forward/backward CPM passes and the linked cash curve. The override is labelled in the exported assumptions/trace.

## Optional AI reader

### Use your existing chat subscription manually

Open <http://127.0.0.1:5050> (also available at `/normalize`). Choose ChatGPT, Claude or Gemini: the button copies the full prompt and opens the provider in a new tab. Paste it and attach your **original BOQ** yourself, then save the normalized JSON. Upload the JSON and the same original workbook(s) in the adjacent panel. If clipboard access is denied, a selectable prompt is revealed; a prompt download is also available. No workbook data is included in provider links, and this does not set a privileged system prompt.

The app checks source cells, quantities, prices, inventory and operation/unit compatibility locally. A named reviewer must confirm the descriptions and project-total scope before calculation. Missing prices remain unavailable; unsupported work blocks generation rather than receiving a generic duration. This path makes no AI API calls, and preserves approved labels instead of rerunning keyword classification.

### Resolve checks and save reviewed inputs

After uploading, use **Review and correct values**. Eligible fields offer **Use Excel**, **Accept AI value**, or **Enter manually**. AI here means the proposal in your uploaded chat JSON, not a new API response. Enter a decision reviewer; reasons are optional and blanks are recorded as **Not provided**. Use **Use AI values for all editable fields** to select all AI proposals in one action, with an optional shared reason. Replacing existing choices requires confirmation, and evidence repairs are left unchanged. Unambiguous source evidence can be repaired using the locally generated **Repair evidence from Excel** option.

Click **Recheck changes**, then **Save reviewed JSON** to download `buildflow-reviewed-boq.json`. This is a draft containing the untouched proposal, source hashes and decisions; it may still have unresolved blockers. Upload it with the exact same original workbooks to resume. Confirm the reviewed project settings and click **Run reviewed model** when the relevant checks are resolved.

Overrides remain labelled in results, replanning and the Excel **Input Decisions** sheet. Missing rows, invalid source identities and unsupported engineering cannot be waived. Changing units requires explicit quantity review; changing quantity/rate requires reviewing any inconsistent amount before cash calculation. Missing prices can still be used for schedule-only drafts. Saved reviewer names are local acknowledgements, not authenticated signatures or reusable model-run approval.

Use schedule-only mode for unpriced BOQs. Cash comparison additionally requires complete prices and an independently confirmed contract duration in working days. The current RWH source still needs additional operation support before a complete schedule can be admitted. See the [workflow and pilot limits](docs/normalization/WORKFLOW.md) and [chat prompt](docs/normalization/CHAT_NORMALIZER_PROMPT.md).

### Optional API fallback for the legacy reader

Offline `rules`, `retrieval` and `hybrid` modes work without an API key. The AI fallback is off by default and reviews only rows with confidence below `0.68`.

It uses Groq's free API tier (rate limits apply, for example 30 requests/minute and 8K tokens/minute on `openai/gpt-oss-120b`). Create a key at <https://console.groq.com/keys>, then:

```bash
export GROQ_API_KEY="..."
# optional: the smaller, faster strict-schema model (default openai/gpt-oss-120b)
export BUILDFLOW_LLM_MODEL="openai/gpt-oss-20b"  # or pass --llm-model / llm_model through the API
```

`openai/gpt-oss-120b` is an open-weights model hosted by Groq; nothing is sent to OpenAI. The integration calls Groq's OpenAI-compatible Chat Completions endpoint with a strict JSON schema, sends at most 20 rows per request, and waits for `Retry-After` on HTTP 429 before giving up after three attempts. Only row IDs, descriptions and units leave the machine, and only when the AI fallback is explicitly enabled; Groq does not retain inference data by default. Model access and data-governance approval remain the operator's responsibility. See Groq's [structured outputs reference](https://console.groq.com/docs/structured-outputs).

`qwen/qwen3.8-27b` also supports strict schemas, but on the free tier its output is capped at 1,000 tokens per minute, below what a 20-row request reserves, so it currently fails with HTTP 429.

## Expected BOQ columns

The parser scans the first 40 rows of each sheet for headers. It requires a description and quantity and recognises common variants:

| Required | Optional | Recognised examples |
|---|---|---|
| Description | Unit | `Description of Item`, `Particulars`, `UOM` |
| Quantity | Rate | `Qty`, `Unit Rate` |
|  | Amount | `Total Amount`, `Value` |

If amount is absent but quantity and rate exist, amount is calculated. If both rate and amount are absent, the row stays in the schedule but is explicitly marked unpriced and excluded from monetary totals.

## Research boundaries

This is a **first-draft planning and experiment platform**, not an approved baseline or a claim of autonomous construction planning.

- Productivity figures in [`buildflow/scheduling.py`](buildflow/scheduling.py) are editable starter assumptions. They are deliberately not described as CPWD-published norms; replace and cite them after the literature/site-data calibration.
- Typology templates do not yet model holidays, weather-loss distributions, resource levelling, lead/lag links, work calendars by trade, spatial zones or contractual milestones.
- Priced BOQ value is time-phased planned work value. A real contractor cash position additionally needs actual cost ledgers, advances, taxes, escalation, certification and payment records.
- The CSVs in `data/samples/` are **sanitised demonstration rows reconstructed from the supplied project descriptions**, not verbatim tender evidence and not validation ground truth. Use the original BOQs and site records for the dissertation results.
- No single public BOQ/schedule/cash-flow dataset is claimed. The validation protocol therefore keeps provenance and expert mapping explicit.

The system architecture and decisions are documented in the [System design document](docs/DESIGN.md). The experimental design and ground-truth procedure are in [Methodology](docs/METHODOLOGY.md) and [Validation protocol](docs/VALIDATION_PROTOCOL.md).

## Repository map

- `buildflow/ingestion.py` — CSV/Excel standardisation
- `buildflow/taxonomy.py` — rules, local retrieval, typology inference
- `buildflow/llm.py` — optional structured AI classification
- `buildflow/scheduling.py` — typology templates, productivity and CPM
- `buildflow/cashflow.py` — linked and independent monthly curves
- `buildflow/agent.py` — orchestration and trace
- `buildflow/validation.py` — audit findings and reconciliation
- `buildflow/exporter.py` — multi-sheet Excel report
- `buildflow/benchmark.py` — reader ablation evaluation
- `tests/` — end-to-end and structural checks
