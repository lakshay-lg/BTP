# HSR rate module — design

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Status | Draft for review |
| Scope | Rate sourcing, BOQ-row → HSR item matching, pricing with provenance, formula-driven rates in the Excel export, AI-assisted pricing for rows HSR does not cover |
| Related | [System design](../../DESIGN.md) §10–§13, §18; README "Optional AI reader" |

## 1. Problem

- Tender BOQs carry quantities only. The 550 KLD STP BOQ (38 quantity rows) has no rate column, so every cash-flow sheet is zero and `UNPRICED_ITEMS` fires for all rows.
- Rates must serve **both** dissertation validation (verifiable, citable, reproducible) **and** a demo (every row priced, clearly labelled).
- Only the unpriced BOQ is available: no Notice Inviting Tender estimate and no awarded priced BOQ.

## 2. Decisions settled during brainstorming

1. **Approach A.** The official Haryana PWD Schedule of Rates (HSR 2021) is the rate source. AI chooses which HSR item a BOQ row corresponds to; it never produces a tier-A number.
2. **Provenance on every rate.** Validation totals use only verifiable sources; demo totals add proposed and AI-researched rates; AI guesses are comparison-only and never enter any total.
3. **Review through CSV.** Matches are reviewed by editing `rate_matches.csv` (mirrored in the workbook). A UI review screen is deferred.
4. **Formulas now for rates and amounts.** A formula-driven cash flow is a separate follow-up spec.
5. **Citations for AI-researched quantities** may reference HSR/DAR items, IS codes, American standards (ASTM, ACI), manufacturer datasheets or URLs.
6. **Tools.** Groq's free tier is the automated matcher. Claude, GPT and Gemini Pro chats (no API) and Claude Code sessions produce tier-B/C files through a prompt pack.

## 3. Sources and reading rules (verified 2026-09-11)

| Document | URL | Size | SHA-256 |
|---|---|---|---|
| HSR 2021 (Third Edition) | https://haryanapwd.gov.in/home/docs/HSR%202021.pdf | 46,591,509 B, 740 pp | `ee559c506c73931bed98c7438be1bb2f96c237df6df22cd99593bf15f8759329` |
| A&C slip, 14-Mar-2024 (road/bituminous items only) | https://hsamb.org.in/sites/default/files/documents/Engineering/202411/partA/Amendment%20in%20HSR%20-%2014.03.2024%20(Notification).pdf | 533,349 B | `d354304f49712abf706728f5ba4505a03ffae017aa11a14cd47b78cb5c405329` |
| A&C slip No. 19, 10-Jul-2024 (items 10.110–10.114, tactile indicators) | https://works.haryana.gov.in/home/docs/notification01072024.pdf | 605,014 B | `c385460912e773e458e29aacfe3007d7cd31a711f379d323784fe1e178b11f00` |

Reading rules:

- An item line holds a code, a description that may span lines (and continue after the numbers), a unit, then either **Labour | Material | Through** (3 columns) or **Labour | Machinery | Material | Through** (4 columns, road chapter and A&C slips). Through = sum of components, ±1 for rounding (e.g. 10.37.1 Kota stone 25 mm: 301 + 584 = 885 per sqm).
- Item (through) rates are "inclusive of GST and all other taxes, Labour Welfare Cess and contractor's profit". Slip 19: HSR 2021 rates include 12% GST and "will be read with a multiple factor of 0.893 to exclude the impact of GST".
- Basic material, labour and plant rates exclude GST, labour cess, contractor's profit, overheads and carriage. Schedule B carriage rates include profit and overheads and exclude GST.
- HSR publishes no profit/overhead percentage.
- The STP BOQ copies HSR wording verbatim (e.g. 4.31.1 extra lift, 11.64.1 distemper).
- **Not used:** CPWD DSR 2023. cpwd.gov.in rejects scripted downloads ("Request Rejected"), and the available mirror is a scanned PDF with no extractable text.

## 4. Dependencies and build order

Prerequisites, each with its own design (already queued):

- **P1 — unit-compatibility guard** in scheduling (no silent 1 unit/day fallback).
- **P2 — parent-row BOQ parser** producing full descriptions (section + parent + leaf), stable row keys `(source_sheet, source_row)`, OPTION/alternate flags and footer metadata. The matcher consumes P2's full descriptions.

Phases of this module: **1** HSR table → **2** matcher → **3** pricing and Excel → **4** AI tiers and prompt pack → **5** evaluation.

## 5. Component 1 — HSR rate table

Converts the PDF once into versioned, tested data. No PDF parsing happens during an analysis.

**Files**

- `tools/build_hsr_table.py [--pdf PATH] [--out data/rates]` — downloads with resume when no path is given, extracts text with `pypdf`, writes the CSVs. Exits non-zero if the arithmetic self-check or the known-value checks fail. `pypdf` is an optional `rates` extra in `pyproject.toml`; the application reads CSV with the standard library only.
- `data/rates/hsr_2021_items.csv` — `code, parent_code, own_text, full_description, unit_raw, unit, components, labour, machinery, material, through, chapter, page, source`. `components` holds the printed component numbers in order; `labour`/`machinery`/`material` are filled only when the number of components equals the page's component columns (2 on 3-column pages, 3 on 4-column pages), because layout extraction does not align numbers with header labels.
- `data/rates/hsr_2021_basic_rates.csv` — `code, category (material|plant|labour|carriage), description, unit_raw, unit, rate, page, source`
- `data/rates/hsr_2021_amendments.csv` — same columns as items; replaces base items by code; `source` = `A&C slip N (date)`. Slip 19 and the March 2024 slip are entered.
- `data/rates/hsr_2021_quarantine.csv` — rows failing the arithmetic self-check, for manual review. Never used for pricing.
- `data/rates/SOURCE.md` — URLs, SHA-256, download date, slips applied, reading rules, known gaps.

The extracted CSVs are committed, cited as a Haryana PWD (B&R) publication. The PDF is not committed.

**Parsing rules**

- Codes nest (4.31 → 4.31.1 → 6.23.1.1); `full_description` = ancestor texts + own text.
- Page headers, footers, page numbers and section titles are dropped.
- The column heading in force on each page decides 3- vs 4-column interpretation.
- The last number on a rate line is the through rate; the numbers before it are components.
- Through rates are stored exactly as printed (GST-inclusive); conversion happens in pricing.

## 6. Component 2 — matcher (BOQ row → HSR code)

**Input.** Quantity rows with `full_description`, `unit` and row key. Only descriptions and units are used; quantities and rates never go to a model.

**Unit compatibility.** Canonical units: `m3` (cum), `m2` (sqm), `m` (metre, rm), `kg`, `quintal` (100 kg), `tonne` (1000 kg), `no` (nos, no., each), `litre`, `day`. The mass family converts; all other units must match exactly. `unit_factor` converts an HSR per-unit rate to the BOQ unit (HSR per quintal, BOQ per kg → × 1/100).

**Content tokens.** Lower-cased alphanumeric runs, keeping numbers, sizes, grades and mix ratios (`20`, `m30`, `1:4`, `1.5`), with stopwords removed. The shortlist and the verbatim shortcut use this one definition.

**Step 1 — shortlist (local, deterministic).** Among unit-compatible priced items, rank by Jaccard similarity of content tokens. Keep the top 8.

**Verbatim shortcut.** A row is matched without any model call when every content token of an HSR item's full description appears in the BOQ row's full description (containment = 1.0), the HSR description has at least 6 content tokens, the unit is compatible, and exactly one candidate qualifies.

Containment is used instead of an edit-distance ratio because BOQ rows add words HSR lacks (section headings, approved makes, band labels). On real pairs a `SequenceMatcher` ratio scored the genuine match row 124 → 11.64.1 at 0.86, while containment scored it 1.00. Containment also rejects a differing size through its number token: row 121 (18–20 mm plaster) is not 11.8.1 (12 mm), and row 102 (Kota 20 mm) is not 10.37.1 (25 mm). A trial over the 38 STP rows, using a rough HSR parse, gave 4 unique verbatim matches — 13.29.2, 13.42.1, 11.10.1 and 11.64.1, all correct — and no false matches. The shortcut is high-precision and low-recall by design; most rows go through step 2 and review.

**Step 2 — Groq pick (rows not verbatim and not frozen).** Batches of up to 10 rows go to `llm.match_rate_items()`, reusing the existing transport, batching, 429 retry and all-or-nothing behaviour. Strict JSON schema: `matches[]` of `row_key`, `hsr_code` (enum: union of the batch's candidate codes + `none`), `spec_differences` (string, empty when none), `confidence` (number), `reason` (string). Post-check: the code must be one of that row's candidates or `none`; otherwise the answer is rejected and the row stays unmatched.

**Statuses**

| Status | Set by | Meaning | Priced from HSR? |
|---|---|---|---|
| `verbatim` | matcher | Verbatim shortcut | Yes — `hsr` |
| `ai-proposed` | Groq | Pick with empty `spec_differences` | Yes — `hsr-proposed` |
| `spec-mismatch` | Groq | Closest item differs in grade/thickness | No — goes to the gap list |
| `ai-confirmed` | user | Reviewed `ai-proposed` | Yes — `hsr` |
| `spec-approved` | user | Accepted `spec-mismatch` | Yes — `hsr` |
| `none` | matcher/Groq/user | No acceptable item | No — gap list |

**Frozen matches — `rate_matches.csv`.** Columns: `source_sheet, source_row, description_sha1, boq_description, unit, hsr_code, multiplier, status, method (verbatim|groq|manual), spec_differences, model, date, reason`. Loaded before matching; a row with a frozen entry is never re-sent. A description-hash mismatch means the BOQ changed: the entry is ignored and `RATE_MATCHES_STALE` is raised. The user reviews by editing `hsr_code`, `multiplier` and/or `status` (user-settable statuses: `ai-confirmed`, `spec-approved`, `none`). `multiplier` defaults to 1 and covers rows priced as a multiple of one HSR item — e.g. BOQ extra-lift depth bands (rows 16–20) priced from the per-lift item 4.31.1, with the number of lifts per band following the tender's measurement convention. An unknown code or incompatible unit rejects that line (`RATE_MATCHES_REJECTED`).

**Files.** `buildflow/rate_table.py` (loading and amendments), `buildflow/rate_matching.py` (units, shortlist, verbatim, statuses, frozen matches), `llm.match_rate_items()`.

## 7. Component 3 — pricing and Excel output

**Rate source per row** (`BOQItem.rate_source`), in precedence order:

1. `tender` — rate or amount present in the BOQ file. Assumed ex-GST (recorded as an assumption).
2. `hsr` — status `verbatim`, `ai-confirmed` or `spec-approved`.
3. `hsr-proposed` — status `ai-proposed`.
4. `ai-researched` — tier-B components imported (§8).
5. `unpriced`.

An AI guess (§8) is a separate comparison field, never a rate source.

**Arithmetic**

```
HSR net rate  = through × unit_factor × multiplier × GST_EXCL_FACTOR (0.893) × ESCALATION
amount        = quantity × net rate
total incl GST = total ex-GST × (1 + GST_PCT / 100)       — GST applied once, at totals
```

**Totals.** By rate source; **validation total** = `tender` + `hsr`; **demo total** = `tender` + `hsr` + `hsr-proposed` + `ai-researched`. `rate_basis` (`validation` default, or `demo`) selects which rows carry amounts into CPM cost allocation and cash flow; rows outside the basis carry zero and are counted in findings.

**Configuration** (`ProjectConfig`): `rate_basis`, `gst_pct` (18.0), `escalation` (1.0), `profit_overhead_pct` (15.0), `labour_cess_pct` (1.0). CLI: `--rate-basis`, `--gst-pct`, `--escalation`, `--rate-matches`, `--ai-components`, `--ai-guesses`.

**Agent trace.** New steps `match_rates` (counts by method and status, provider, model) and `price_boq` (totals by rate source, basis).

**Findings.** `UNPRICED_ITEMS` (rows without a rate in the chosen basis), `RATE_BASIS` (share of value by source), `RATE_REVIEW_PENDING` (`ai-proposed` count), `RATE_GAPS` (rows needing tier B), `GST_ASSUMED` (default GST in use), `RATE_TABLE_UNAVAILABLE`, `RATE_MATCH_FAILED`, `RATE_MATCHES_STALE`, `RATE_MATCHES_REJECTED`, `AI_IMPORT_REJECTED`, `AI_GUESS_DEVIATION`.

**Excel**

- **Rate Assumptions** — named cells `GST_EXCL_FACTOR`, `GST_PCT`, `ESCALATION`, `PROFIT_OVERHEAD_PCT`, `LABOUR_CESS_PCT`.
- **Rates** — one row per matched HSR code: code, full description, unit, labour, material, through, page, unit factor, net-rate formula.
- **Rate Analysis** — tier-B component lines with formulas (§8).
- **Priced BOQ** — rate as a formula referencing Rates / Rate Analysis cells, amount = quantity × rate, HSR code, match status, rate source, unit factor, multiplier, in-basis flag, basis amount, AI guess, deviation %.
- **Rate Matches** — the `rate_matches.csv` content, for review alongside the workbook.
- **Rate Totals** — `SUMIFS` by rate source, validation and demo totals, ex- and incl-GST.
- Cash-flow sheets remain Python-computed values; their titles state the basis and "re-run after editing rates".

Python computes the same numbers for the web UI, metrics and CPM cost allocation; parity is tested through LibreOffice (§10).

**Files.** `buildflow/pricing.py`, `exporter.py`, `models.py`, `cli.py`, `agent.py`.

## 8. Component 4 — AI pricing for gaps

**Gap export — `rate_gaps.csv`:** `source_sheet, source_row, full_description, unit, spec_differences, nearest_hsr` (top 3 as `code: description`).

**Tier B, `ai-researched` — `ai_rate_components.csv`:** `source_sheet, source_row, component_type (material|labour|plant|carriage), description, qty_per_unit, unit, unit_price, price_source (HSR basic code or URL), source_date (required for URLs), citation_type (hsr|dar|is|astm|aci|datasheet|url), citation_ref, notes`.

```
price_eff   = unit_price × (ESCALATION if price_source is an HSR basic code else 1)
marked      = Σ(material|labour|plant: qty × price_eff) × (1 + PROFIT_OVERHEAD_PCT/100)
            + Σ(carriage: qty × price_eff)                 — Schedule B already includes profit/overheads
rate_ex_gst = marked × (1 + LABOUR_CESS_PCT/100)
```

Citation preference: HSR/DAR, then IS, then ASTM/ACI, then datasheets, then URLs. American standards are used only when no Indian reference covers the point; imperial values are converted to the BOQ's metric unit with the conversion shown in `notes`.

**Tier C, `ai-guess` — `ai_rate_guesses.csv`:** `source_sheet, source_row, unit, rate_ex_gst, model, date, notes`. Shown as a comparison column with deviation % against the row's tier-A/B rate; `AI_GUESS_DEVIATION` when |deviation| > 30%. Never counted in any total.

**Import validation.** A line is rejected when its row key is unknown, its unit differs from the BOQ row (guesses only — a component's `unit` is its own, e.g. kg of cement per sqm of plaster), its `price_source` is neither a known HSR basic code nor a URL with `source_date`, its `citation_type` is not in the list or `citation_ref` is empty, or any number is non-positive. Each rejected line and reason is listed in `AI_IMPORT_REJECTED`; the rest imports.

**Prompt pack.** `prompts/rate_analysis.md` (tier B) and `prompts/rate_guess.md` (tier C): role, inputs (gap CSV and the HSR basic-rates CSV), rules (exact CSV header, a source for every price, `unknown` instead of guessing, Karnal/Haryana basis, state the date, no rows beyond the input), and output examples. Usable as Claude/GPT/Gemini Pro project instructions or in a Claude Code session. Only gap-row descriptions and units leave the machine; each app's data settings must be checked before use.

**Files.** `buildflow/ai_rates.py`, `prompts/`.

## 9. Error handling

| Situation | Behaviour |
|---|---|
| HSR table missing or corrupt | Pricing skipped (`RATE_TABLE_UNAVAILABLE`); analysis continues unpriced |
| Groq unavailable while matching | Verbatim and frozen matches still apply; remaining rows stay unmatched (`RATE_MATCH_FAILED`) |
| `rate_matches.csv` names an unknown code or incompatible unit | Line rejected (`RATE_MATCHES_REJECTED`) |
| BOQ row text changed since a frozen match | Entry ignored (`RATE_MATCHES_STALE`) |
| Unknown unit conversion | Treated as no match, never guessed |
| Invalid line in an AI import file | Line rejected (`AI_IMPORT_REJECTED`); rest imports |
| Table build fails a self-check or known value | Build script exits non-zero; nothing is written over the committed CSVs |

## 10. Testing

All tests fail first, use no network, and rely on the committed CSVs, small text fixtures and the fake Groq transport.

- `tests/test_rate_table.py` — known values from the committed CSV: 10.37.1 = 301/584/885 per sqm; 11.64.1 = 37/13/50 per sqm; 13.29.2 = 19/74/93 per kg; 11.8.1 = 44/48/92 per sqm; basic B0179 = 280 per sqm; PM014 = 12000 per day. Builder run on `tests/fixtures/hsr_pages.txt` covering nesting, multi-line descriptions, 3- vs 4-column pages and page noise; a doctored row fails the self-check.
- `tests/test_rate_matching.py` — cum vs sqm rejected; kg ↔ quintal factor 0.01; BOQ row 124 → 11.64.1 verbatim with no request; row 102 (Kota 20 mm) not verbatim-matched to 10.37.1 (25 mm); Groq pick limited to the row's candidates and an out-of-list code rejected; a frozen match sends no request; a stale hash is ignored.
- `tests/test_pricing.py` — 10 sqm of distemper at 50 × 0.893 = 44.65 per sqm → 446.50; kg/quintal conversion; multiplier 2 on a lift-band row doubles its rate; GST once at totals; validation vs demo totals by rate source; AI guess excluded from both; tender precedence.
- `tests/test_ai_rates.py` — each import rejection rule; tier-B arithmetic with fixture values (material 2 × 100, labour 0.5 × 600, carriage 1 × 50 → (200 + 300) × 1.15 + 50 = 625 → × 1.01 = 631.25); carriage not marked up.
- `tests/test_export_formulas.py` — LibreOffice (25.2 available) recalculates the exported workbook headless; every rate, amount and total must equal Python's value. Skipped when `soffice` is absent.
- End to end — the STP BOQ with committed CSVs and the fake transport produces a workbook with a rate source per row and both totals.

## 11. Evaluation

`data/benchmark/stp_hsr_gold.csv` (HSR codes for the 38 STP rows, drafted by Claude and verified by the user) feeds `python3 -m buildflow.rate_benchmark`, which reports rows matched per method (verbatim, Groq, none), top-1 accuracy per method, and the share of the validation total covered.

## 12. Known gaps and risks

- A&C slips 1–18 are not yet collected; slip 19 and the March 2024 slip touch no STP items.
- HSR 2021 is a 2021 price basis. Escalation is a single labelled multiplier (default 1.0); the dissertation must state the basis.
- HSR has no Fe-550D reinforcement item, so that row becomes `spec-mismatch` or tier B.
- Extra-lift depth bands map to one per-lift HSR item; until the reviewer sets `multiplier` from the tender's measurement convention, these rows stay `spec-mismatch`.
- Profit and overheads at 15% is CPWD's convention (HSR publishes none); labour cess 1% is a default to confirm.
- GST defaults to 18% until confirmed against the tender; sources disagree on the post-September-2025 rate for government works.
- Tender rates are assumed ex-GST.
- Groq output varies between runs; frozen matches make reruns reproducible, but first runs still vary.
- Gap rows sent to consumer chat apps fall under those apps' data settings.
- The HSR server resets long downloads; the builder resumes them.

## 13. Out of scope

- Formula-driven cash flow (next spec).
- A UI screen for match review.
- CPWD DSR support (needs OCR of scanned PDFs).
- EmbeddingGemma retrieval (can replace the shortlist ranking later without interface changes).
- Collecting A&C slips 1–18 (tracked task).
- Prerequisites P1 and P2 (own designs).
