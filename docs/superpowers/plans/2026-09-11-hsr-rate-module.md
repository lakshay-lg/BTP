# HSR Rate Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Price BOQ rows from the official Haryana PWD Schedule of Rates (HSR 2021) with per-row provenance, AI-assisted matching and gap pricing, and formula-driven rates in the Excel export.

**Architecture:** An offline builder turns the HSR PDF into committed CSVs. At runtime BuildFlow loads those CSVs, matches each BOQ row to an HSR code (deterministic shortlist and verbatim rule, optional Groq pick, frozen in `rate_matches.csv`), prices rows before scheduling, and writes rates and amounts as live Excel formulas. Rows HSR does not cover are priced from AI-produced component CSVs built with a prompt pack.

**Tech Stack:** Python 3.11+, openpyxl 3.1, standard library (`csv`, `hashlib`, `re`, `urllib`), `pypdf>=6,<7` (builder only, optional `rates` extra), LibreOffice headless (formula test only, skipped when absent), Groq Chat Completions through the existing `buildflow/llm.py` transport.

**Spec:** `docs/superpowers/specs/2026-09-11-hsr-rate-module-design.md`

## Global Constraints

- GST exclusion factor `0.893`; GST applied once, at totals.
- Defaults: `gst_pct` 18.0 (finding `GST_ASSUMED` until confirmed), `escalation` 1.0, `profit_overhead_pct` 15.0, `labour_cess_pct` 1.0, `rate_basis` `validation`.
- Validation total = `tender` + `hsr`. Demo total = `tender` + `hsr` + `hsr-proposed` + `ai-researched`. AI guesses never enter any total.
- Verbatim rule: every HSR content token appears in the BOQ full description; the HSR description has at least 6 content tokens; units compatible; exactly one candidate qualifies.
- Shortlist: top 8 unit-compatible priced items by Jaccard similarity of content tokens.
- Groq matching: batches of at most 5 rows (spec allows up to 10; 5 keeps requests inside the free tier's 8K tokens/minute), strict JSON schema, code must be one of that row's candidates or `none`. Candidate descriptions are truncated to 240 characters.
- HSR self-check: when components are printed, they sum to the through rate within ±1 or 0.5% of it, whichever is larger; rows printing only a through rate are kept.
- Row key = `(source_sheet, source_row)`.
- The application reads rate CSVs with the standard library only; `pypdf` is used only by `tools/build_hsr_table.py`.
- Tests use no network. External AI calls happen only when `use_llm_fallback` is true (DESIGN §18).
- Run every command from the BTP repository root (`~/Code/BTP`). Baseline: `python3 -m pytest -q` → 30 passed.
- Every commit message ends with these two trailer lines:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q
  ```

## Prerequisites and scope

- Prerequisites P1 (unit-compatibility guard in scheduling) and P2 (parent-row BOQ parser) have their own designs. This plan works on the current `BOQItem.description`; the verbatim rule only becomes effective once P2 supplies full descriptions (section + parent + leaf). Tests that exercise it use literal full descriptions.
- Out of scope (spec §13): formula-driven cash flow, a match-review UI, CPWD DSR, EmbeddingGemma retrieval, collecting A&C slips 1–18.

## File structure

| File | Responsibility |
|---|---|
| `buildflow/rate_units.py` (new) | Canonical units and HSR→BOQ unit conversion factors |
| `buildflow/hsr_parse.py` (new) | Pure parsing of HSR layout text: items, basic rates, self-check, CSV rows |
| `tools/build_hsr_table.py` (new) | Download and hash-check PDFs, extract layout text, write `data/rates/` |
| `data/rates/*.csv`, `data/rates/SOURCE.md` (generated, committed) | Items, basic rates, amendments, quarantine, provenance |
| `buildflow/rate_table.py` (new) | Runtime loader: `RateItem`, `BasicRate`, `RateTable`, `load_rate_table()` |
| `buildflow/rate_matching.py` (new) | Content tokens, shortlist, verbatim rule, match flow, `rate_matches.csv` I/O |
| `buildflow/llm.py` (modify) | Shared structured-request helper; `match_rate_items()` |
| `buildflow/pricing.py` (new) | Rate-source precedence, net rates, basis, totals |
| `buildflow/ai_rates.py` (new) | Gap export, tier-B component import and arithmetic, tier-C guess import |
| `buildflow/rate_benchmark.py` (new) | Matcher evaluation against a verified gold CSV |
| `buildflow/models.py` (modify) | New `BOQItem`/`ProjectConfig`/`AnalysisResult` fields, `RateMatch`, `AIComponent` |
| `buildflow/agent.py`, `validation.py`, `cli.py`, `exporter.py` (modify) | Wiring, findings, flags, sheets and formulas |
| `prompts/rate_analysis.md`, `prompts/rate_guess.md` (new) | Prompt pack for tier B and tier C |
| `tests/fixtures/hsr_layout_sample.txt` (new) | Verbatim HSR layout lines used by parser tests |
| `tests/test_rate_units.py`, `test_hsr_parse.py`, `test_rate_table.py`, `test_rate_matching.py`, `test_pricing.py`, `test_rate_agent.py`, `test_ai_rates.py`, `test_export_formulas.py`, `test_prompts.py`, `test_rate_benchmark.py` (new); `tests/test_llm.py` (extend) | Tests |

---

### Task 1: Unit vocabulary

**Files:**
- Create: `buildflow/rate_units.py`
- Test: `tests/test_rate_units.py`

**Interfaces:**
- Produces: `canonical_unit(raw: str) -> str` (one of `m3, m2, m, kg, quintal, tonne, no, litre, day, hour`, or `""` when unknown); `unit_factor(boq_unit: str, hsr_unit: str) -> float | None` (multiplier turning an HSR rate per `hsr_unit` into a rate per `boq_unit`; `None` when incompatible).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rate_units.py
from __future__ import annotations

import pytest

from buildflow.rate_units import canonical_unit, unit_factor


@pytest.mark.parametrize(
    "raw, expected",
    [("Cum", "m3"), ("cum", "m3"), ("Sqm", "m2"), ("sqm.", "m2"), ("Kg", "kg"), ("quintal", "quintal"),
     ("Each.", "no"), ("Nos", "no"), ("No", "no"), ("metre", "m"), ("day", "day"), ("1000 nos.", ""), ("", "")],
)
def test_canonical_unit(raw, expected):
    assert canonical_unit(raw) == expected


@pytest.mark.parametrize(
    "boq, hsr, factor",
    [("Sqm", "sqm", 1.0), ("kg", "quintal", 0.01), ("kg", "tonne", 0.001), ("quintal", "kg", 100.0), ("Nos", "Each.", 1.0)],
)
def test_unit_factor_converts_compatible_units(boq, hsr, factor):
    assert unit_factor(boq, hsr) == pytest.approx(factor)


@pytest.mark.parametrize("boq, hsr", [("Cum", "sqm"), ("kg", "sqm"), ("Sqm", ""), ("1000 nos.", "Each")])
def test_incompatible_units_have_no_factor(boq, hsr):
    assert unit_factor(boq, hsr) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_rate_units.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'buildflow.rate_units'`

- [ ] **Step 3: Write minimal implementation**

```python
# buildflow/rate_units.py
from __future__ import annotations

import re

_ALIASES = {
    "m3": {"cum", "cu m", "cu.m", "m3", "cubic metre", "cubic meter"},
    "m2": {"sqm", "sq m", "sq.m", "m2", "square metre", "square meter"},
    "m": {"m", "metre", "meter", "rm", "rmt", "running metre"},
    "kg": {"kg", "kgs", "kilogram"},
    "quintal": {"quintal", "qtl"},
    "tonne": {"tonne", "ton", "mt", "t"},
    "no": {"no", "nos", "number", "each", "ea", "pcs"},
    "litre": {"litre", "liter", "ltr"},
    "day": {"day", "days"},
    "hour": {"hour", "hours", "hr"},
}
_LOOKUP = {alias: unit for unit, aliases in _ALIASES.items() for alias in aliases}
_KG_PER_UNIT = {"kg": 1.0, "quintal": 100.0, "tonne": 1000.0}


def canonical_unit(raw: str) -> str:
    text = re.sub(r"\s+", " ", str(raw or "").strip().lower()).rstrip(".").strip()
    return _LOOKUP.get(text, "")


def unit_factor(boq_unit: str, hsr_unit: str) -> float | None:
    """Multiplier turning an HSR rate per ``hsr_unit`` into a rate per ``boq_unit``; None if incompatible."""
    boq, hsr = canonical_unit(boq_unit), canonical_unit(hsr_unit)
    if not boq or not hsr:
        return None
    if boq == hsr:
        return 1.0
    if boq in _KG_PER_UNIT and hsr in _KG_PER_UNIT:
        return _KG_PER_UNIT[boq] / _KG_PER_UNIT[hsr]
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_rate_units.py -q`
Expected: PASS (22 passed)

- [ ] **Step 5: Commit**

```bash
git add buildflow/rate_units.py tests/test_rate_units.py
git commit -m "feat(rates): add unit vocabulary and HSR unit conversion" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 2: HSR item parser

**Files:**
- Create: `buildflow/hsr_parse.py`
- Create: `tests/fixtures/hsr_layout_sample.txt`
- Test: `tests/test_hsr_parse.py`

**Interfaces:**
- Consumes: `canonical_unit` from Task 1.
- Produces:
  - `component_columns(line: str) -> tuple[str, ...] | None` — `("labour", "material")` for 3-column headers, `("labour", "machinery", "material")` for 4-column headers.
  - `parse_rate_tail(line: str) -> RateTail | None`; `RateTail(unit: str, numbers: tuple[float, ...], start: int)`.
  - `HsrItem` dataclass (`code, own_text, page, chapter, columns, source, unit_raw, components, through, problems`) with `assigned() -> dict[str, float]`.
  - `self_check(item: HsrItem) -> str | None`.
  - `parse_items(pages: list[tuple[int, str]], source: str = "HSR 2021") -> tuple[list[HsrItem], list[tuple[HsrItem, str]]]` — usable items (including unpriced parents) and quarantined items with reasons.
  - `ITEM_HEADER: list[str]` and `item_rows(items: list[HsrItem]) -> list[dict[str, str]]` (CSV rows with `parent_code` and `full_description`).

Parsing rules this task implements (from the real PDF): layout extraction does **not** align numbers with header labels, so the last number on a rate line is the through rate and the numbers before it are components, assigned to columns only when their count equals the page's component columns. Codes need two or more spaces after them; slip rows carry a serial number (`1.  10.110`); a `CHAPTER N` heading sets the chapter, and a code whose first number differs from the chapter is description text.

- [ ] **Step 1: Create the fixture (verbatim HSR layout lines — keep every space)**

`tests/fixtures/hsr_layout_sample.txt`:

```text
=== hsr-238
Item        Description                                                                                                                Unit        Labour      Material   Through
No.                                                                                                                                                             Rate           Rate          Rate
11.57      Providing & laying average 6mm thick POP Coating  on walls, Ceiling, beams     sqm             47                29              76
             and  lintels  etc.   complete  in  all  respects  as  approved  by  the  Engineer-In-
             Charge.
11.64      Distempering  with  oil  bound  washable  distemper  of  approved  brand  and
             manufacture to give an even shade:
             11.64.1        New work (two or more coats) over and including water thinnable     sqm            37               13              50
                              priming coat with cement primer
11.65      Distempering  with  1st  quality  acrylic  distemper  (ready  mixed)  having  voe
                                                                                      223
=== hsr-155
                                                 CHAPTER 4.0 - EARTH WORK AND ROCK CUTTING
Item        Description                                                                                         Unit              Labour   Machinery   Material   Through
No.                                                                                                                                             Rate          Rate           Rate          Rate
                               4.7.1.1              50m lead                                                 sqm                    59                                                      59
                               4.7.1.2              15 m lead                                                sqm                  42                                                     42
4.8          Earth  work  in  rough   excavation,   banking  excavated  earth  in
             layers  not  exceeding  20cm  in  depth,  breaking  clods,  watering,
             rolling   each   layer   with  ½   tonne   roller   or   wooden   or   steel
             rammers,  and  rolling  every  3rd  and  top-most  layer  with  power
             roller of minimum 8  tonnes and dressing up in embankments for
              roads,  flood  banks,  marginal banks and  guide  banks or filling  up
              ground depressions, lead up to 50 m and lift up to 1.5 m

             4.8.1            All kinds of soil                                                                cum                 158                                                 158
4.9           Banking excavated earth in layers not exceeding 20 cm in  depth,
             breaking clods, watering, rolling each layer with ½ tonne roller, or
              wooden  or  steel  rammers,  and  rolling  every  3rd  and  top-most
             layer with power roller of minimum 8 tonnes and dressing up,  in
             embankments for roads, flood banks, marginal banks, and guide
             banks etc., lead up to 50 m and lift up to 1.5 m:

             4.9.1            All kinds of soil                                                                cum                 115                                                 115
4.10        Deduct for not rolling with power roller of minimum 8 tonnes for cum                   4                                                      4
             banking excavated earth in layers not exceeding 20 cm in depth.

4.11        Deduct for not watering the excavated earth for  banking                 cum                 19                                                   19

                                                                                           141
=== hsr-223
Item        Description                                                                                                                 Unit         Labour      Material   Through
No.                                                                                                                                                                         Rate            Rate          Rate
10.37      Providing and  fixing of Kota  stone slab flooring over 20 mm  (average) thick
             base  laid  over  and jointed  with  grey  cement  slurry  mixed  with  pigment  to
             match  the shade  of  the  slab,  including rubbing and  polishing complete with
             base of cement mortar 1 : 4 ( 1 cement : 4 coarse sand) :
             10.37.1       25 mm thick                                                                                            sqm            301             584           885
10.38      Providing  and  fixing  of  Kota  stone  slabs  20  mm  thick  in  risers  of  steps,     sqm            365              571            936
             skirting, dado and pillars laid on 12 mm  (average) thick cement mortar 1:3 (1
                                                                                      208
=== hsr-30
Unique                                                               Description                                                                        Unit           Rate (INR)
 Code
B0179     Kota stone slab 20 mm to 25 mm thick (semi-polished)                                                           sqm                 280
B0180     Kota stone slab 25mm thick (rough chiselled)                                                                          sqm                 260
                                                                                            16
=== hsr-19
Unique                                                                      Description                                                                           Unit              Rate (INR)
 Code
PM014     Hydraulic Excavator (3D) with driver and fuel                                                                             day                12000
                                                                                                  6
=== hsr-16
                                 CHAPTER 1.0- WAGES AND WORKING CHARGES OF MACHINERY
Unique                                                                         Description                                                                    Unit                             Rate (INR)
 Code
LB008            Beldar                                                                                                                                          day                                 364
                                                                                                       3
=== slip19-1
 No.     Item no.                                                       Labour     Machinery       Material    Through
                                                                         rates         rates         rates       rates
 1.     10.110        Providing   &   Fixing   of   SS   316  Each.       6.16                      56.67        62.83
                      Tactile  Indicator  Warning  (Studs)
                      with  concentric  circle  pattern  on
                      top size (35x25x4.5mm) with stem
                      type  as  per  manufactures  design  /
                                                          (2159)
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_hsr_parse.py
from __future__ import annotations

import re
from pathlib import Path

from buildflow.hsr_parse import component_columns, item_rows, parse_items

FIXTURE = Path(__file__).parent / "fixtures" / "hsr_layout_sample.txt"


def pages() -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    label = ""
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if line.startswith("=== "):
            label = line[4:].strip()
            blocks[label] = []
        else:
            blocks[label].append(line)
    return {key: "\n".join(lines) for key, lines in blocks.items()}


def parse(label: str, source: str = "HSR 2021"):
    usable, quarantined = parse_items([(1, pages()[label])], source)
    return {item.code: item for item in usable}, quarantined


def test_header_lines_announce_component_columns():
    text = pages()
    assert component_columns(text["hsr-238"].splitlines()[0]) == ("labour", "material")
    assert component_columns(text["hsr-155"].splitlines()[1]) == ("labour", "machinery", "material")
    assert component_columns("11.57      Providing & laying") is None


def test_three_column_row_assigns_components_and_keeps_wrapped_text():
    items, quarantined = parse("hsr-238")
    pop = items["11.57"]
    assert (pop.unit_raw, pop.components, pop.through) == ("sqm", (47.0, 29.0), 76.0)
    assert pop.assigned() == {"labour": 47.0, "material": 29.0}
    assert pop.own_text == (
        "Providing & laying average 6mm thick POP Coating on walls, Ceiling, beams and lintels etc. "
        "complete in all respects as approved by the Engineer-In- Charge."
    )
    assert quarantined == []


def test_sub_item_carries_parent_text_in_full_description():
    items, _ = parse("hsr-238")
    rows = {row["code"]: row for row in item_rows(list(items.values()))}
    assert rows["11.64.1"]["full_description"] == (
        "Distempering with oil bound washable distemper of approved brand and manufacture to give an even shade: "
        "New work (two or more coats) over and including water thinnable priming coat with cement primer"
    )
    assert (rows["11.64.1"]["parent_code"], rows["11.64.1"]["unit"], rows["11.64.1"]["through"]) == ("11.64", "m2", "50")
    assert rows["11.64"]["through"] == ""


def test_four_column_row_with_one_component_keeps_through_unassigned():
    items, quarantined = parse("hsr-155")
    soil = items["4.8.1"]
    assert (soil.unit_raw, soil.components, soil.through, soil.chapter) == ("cum", (158.0,), 158.0, "4")
    assert soil.assigned() == {}
    assert items["4.11"].through == 19.0
    assert items["4.10"].own_text.endswith("banking excavated earth in layers not exceeding 20 cm in depth.")
    assert quarantined == []


def test_rate_on_first_line_with_wrapped_continuation():
    items, _ = parse("hsr-223")
    assert (items["10.37.1"].components, items["10.37.1"].through) == ((301.0, 584.0), 885.0)
    assert items["10.38"].through == 936.0
    assert items["10.38"].own_text.startswith(
        "Providing and fixing of Kota stone slabs 20 mm thick in risers of steps, skirting, dado"
    )


def test_serial_numbered_amendment_row():
    items, quarantined = parse("slip19-1", "A&C slip No. 19 (10-07-2024)")
    tactile = items["10.110"]
    assert (tactile.unit_raw, tactile.components, tactile.through) == ("Each.", (6.16, 56.67), 62.83)
    assert tactile.source == "A&C slip No. 19 (10-07-2024)"
    assert tactile.own_text.startswith("Providing & Fixing of SS 316 Tactile Indicator Warning (Studs)")
    assert quarantined == []


def test_bad_arithmetic_is_quarantined_not_kept():
    doctored = pages()["hsr-238"].replace("76", "99", 1)
    usable, quarantined = parse_items([(1, doctored)])
    assert "11.57" not in {item.code for item in usable}
    assert [(item.code, "do not sum" in reason) for item, reason in quarantined] == [("11.57", True)]


def test_rounding_within_half_a_percent_is_accepted():
    doctored = pages()["hsr-223"].replace("885", "887", 1)  # 301 + 584 = 885; 887 is within 0.5%
    usable, quarantined = parse_items([(1, doctored)])
    assert "10.37.1" in {item.code for item in usable}
    assert quarantined == []


def test_through_only_row_is_kept():
    page = re.sub(r"(banking\s+cum\s+19)\s+19", r"\1", pages()["hsr-155"])
    items = {item.code: item for item in parse_items([(1, page)])[0]}
    assert (items["4.11"].components, items["4.11"].through) == ((), 19.0)


def test_code_from_another_chapter_is_description_text():
    page = pages()["hsr-155"].replace(
        "ground depressions, lead up to 50 m and lift up to 1.5 m", "1.5  m lift for every additional depth"
    )
    usable, _ = parse_items([(1, page)])
    assert "1.5" not in {item.code for item in usable}
    parent = next(item for item in usable if item.code == "4.8")
    assert parent.own_text.endswith("1.5 m lift for every additional depth")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_hsr_parse.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'buildflow.hsr_parse'`

- [ ] **Step 4: Write minimal implementation**

```python
# buildflow/hsr_parse.py
"""Pure parsing of HSR 2021 layout-mode text (pypdf ``extraction_mode="layout"``). No I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .rate_units import canonical_unit

COMPONENTS_3 = ("labour", "material")
COMPONENTS_4 = ("labour", "machinery", "material")
_HEADER_WORDS = {"item", "no", "no.", "description", "unit", "labour", "machinery", "material", "through",
                 "rate", "rates", "reference", "morth", "sr", "sr.", "unique", "code", "(inr)"}
_CHAPTER = re.compile(r"^\s*CHAPTER\s+(\d{1,2})(?:\.0)?\b", re.IGNORECASE)
_CODE_LINE = re.compile(r"^\s*(?:\d{1,3}\.\s+)?(?P<code>\d{1,2}\.\d{1,3}(?:\.\d{1,3})*)\s{2,}(?P<rest>\S.*)$")
_UNIT = r"(?:cum|sqm|kg|each|metre|meter|rm|nos?|quintal|tonne|litre|day|hour)\.?"
_TAIL = re.compile(rf"(?P<unit>\b{_UNIT})(?P<nums>(?:\s+\d[\d,]*(?:\.\d+)?)+)\s*$", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

ITEM_HEADER = ["code", "parent_code", "own_text", "full_description", "unit_raw", "unit", "components",
               "labour", "machinery", "material", "through", "chapter", "page", "source"]


def component_columns(line: str) -> tuple[str, ...] | None:
    """Component columns announced by an HSR table header line, or None if the line is not a header."""
    if "Labour" not in line or "Through" not in line:
        return None
    return COMPONENTS_4 if "Machinery" in line else COMPONENTS_3


@dataclass
class RateTail:
    unit: str
    numbers: tuple[float, ...]
    start: int


def parse_rate_tail(line: str) -> RateTail | None:
    match = _TAIL.search(line)
    if not match:
        return None
    numbers = tuple(float(value.replace(",", "")) for value in _NUMBER.findall(match.group("nums")))
    return RateTail(match.group("unit"), numbers, match.start("unit"))


@dataclass
class HsrItem:
    code: str
    own_text: str
    page: int
    chapter: str
    columns: tuple[str, ...]
    source: str
    unit_raw: str = ""
    components: tuple[float, ...] = ()
    through: float | None = None
    problems: list[str] = field(default_factory=list)

    def assigned(self) -> dict[str, float]:
        if self.through is None or len(self.components) != len(self.columns):
            return {}
        return dict(zip(self.columns, self.components))

    def take_tail(self, tail: RateTail) -> None:
        if self.through is not None:
            self.problems.append("more than one rate line")
            return
        self.unit_raw, self.components, self.through = tail.unit, tail.numbers[:-1], tail.numbers[-1]


def _clean(text: str) -> str:
    return " ".join(text.split())


def _is_heading(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", text)) and not re.search(r"[a-z]", text)


def _is_header_words(text: str) -> bool:
    words = text.lower().split()
    return bool(words) and all(word in _HEADER_WORDS for word in words)


def self_check(item: HsrItem) -> str | None:
    """Why an item must be quarantined, or None when it may be used for pricing."""
    if item.problems:
        return "; ".join(item.problems)
    if item.through is not None and item.components:
        tolerance = max(1.0, 0.005 * item.through)
        if abs(sum(item.components) - item.through) > tolerance:
            return f"components {list(item.components)} do not sum to through {item.through}"
    return None


def parse_items(pages: list[tuple[int, str]], source: str = "HSR 2021") -> tuple[list[HsrItem], list[tuple[HsrItem, str]]]:
    """Parse layout-mode pages; returns usable items (including unpriced parents) and quarantined items."""
    items: list[HsrItem] = []
    chapter, columns, current = "", COMPONENTS_3, None
    for page_number, text in pages:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or not re.search(r"[A-Za-z]", stripped):
                continue
            chapter_match = _CHAPTER.match(line)
            if chapter_match:
                chapter = chapter_match.group(1)
                continue
            header = component_columns(line)
            if header:
                columns = header
                continue
            if _is_header_words(stripped):
                continue
            code_match = _CODE_LINE.match(line)
            if code_match and (not chapter or code_match.group("code").split(".")[0] == chapter):
                tail = parse_rate_tail(line)
                start = code_match.start("rest")
                own = line[start:tail.start] if tail and tail.start >= start else line[start:]
                current = HsrItem(code_match.group("code"), _clean(own), page_number, chapter, columns, source)
                if tail:
                    current.take_tail(tail)
                items.append(current)
                continue
            if _is_heading(stripped) or current is None:
                continue
            tail = parse_rate_tail(line)
            if tail:
                current.take_tail(tail)
                line = line[:tail.start]
            current.own_text = _clean(f"{current.own_text} {line}")
    usable = [item for item in items if self_check(item) is None]
    quarantined = [(item, reason) for item in items if (reason := self_check(item))]
    return usable, quarantined


def _fmt(value: float | None) -> str:
    return "" if value is None else f"{value:.10g}"


def item_rows(items: list[HsrItem]) -> list[dict[str, str]]:
    own = {item.code: item.own_text for item in items}
    rows = []
    for item in items:
        parts = item.code.split(".")
        ancestors = [code for code in (".".join(parts[:k]) for k in range(2, len(parts))) if code in own]
        values = item.assigned()
        rows.append({
            "code": item.code,
            "parent_code": ancestors[-1] if ancestors else "",
            "own_text": item.own_text,
            "full_description": " ".join([own[code] for code in ancestors] + [item.own_text]),
            "unit_raw": item.unit_raw,
            "unit": canonical_unit(item.unit_raw),
            "components": " ".join(_fmt(value) for value in item.components),
            "labour": _fmt(values.get("labour")),
            "machinery": _fmt(values.get("machinery")),
            "material": _fmt(values.get("material")),
            "through": _fmt(item.through),
            "chapter": item.chapter,
            "page": str(item.page),
            "source": item.source,
        })
    return rows
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_hsr_parse.py -q`
Expected: PASS (10 passed). Then `python3 -m pytest -q` → all tests pass.

- [ ] **Step 6: Commit**

```bash
git add buildflow/hsr_parse.py tests/fixtures/hsr_layout_sample.txt tests/test_hsr_parse.py
git commit -m "feat(rates): parse HSR layout text into items with self-check" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 3: Basic-rate parser and CSV writers

**Files:**
- Modify: `buildflow/hsr_parse.py` (append)
- Test: `tests/test_hsr_parse.py` (append)

**Interfaces:**
- Consumes: `parse_items`, `item_rows`, `ITEM_HEADER`, `HsrItem` from Task 2; `canonical_unit` from Task 1.
- Produces:
  - `BasicRateRow(code: str, category: str, description: str, unit_raw: str, rate: float, page: int, source: str)` (frozen dataclass); `category` is `material` (B codes), `plant` (PM), `labour` (LB).
  - `parse_basic_rates(pages: list[tuple[int, str]], source: str = "HSR 2021") -> tuple[list[BasicRateRow], list[tuple[str, int, str]]]` — rows, and rejected `(code, page, line)` for code lines that do not parse.
  - `BASIC_HEADER`, `QUARANTINE_HEADER: list[str]`; `basic_rows(rows) -> list[dict[str, str]]`; `quarantine_rows(quarantined) -> list[dict[str, str]]`; `write_csv(path, header, rows) -> None`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_hsr_parse.py`)**

```python
# tests/test_hsr_parse.py (append)
import csv

from buildflow.hsr_parse import (
    BASIC_HEADER, ITEM_HEADER, QUARANTINE_HEADER, basic_rows, parse_basic_rates, quarantine_rows, write_csv,
)


def test_basic_rates_by_category():
    text = pages()
    rows, rejected = parse_basic_rates([(30, text["hsr-30"]), (19, text["hsr-19"]), (16, text["hsr-16"])])
    assert {row.code: (row.category, row.description, row.unit_raw, row.rate, row.page) for row in rows} == {
        "B0179": ("material", "Kota stone slab 20 mm to 25 mm thick (semi-polished)", "sqm", 280.0, 30),
        "B0180": ("material", "Kota stone slab 25mm thick (rough chiselled)", "sqm", 260.0, 30),
        "PM014": ("plant", "Hydraulic Excavator (3D) with driver and fuel", "day", 12000.0, 19),
        "LB008": ("labour", "Beldar", "day", 364.0, 16),
    }
    assert rejected == []


def test_basic_code_line_without_rate_is_rejected():
    rows, rejected = parse_basic_rates([(5, "B0999     Some material without a rate")])
    assert rows == []
    assert rejected == [("B0999", 5, "B0999     Some material without a rate")]


def test_item_and_basic_csv_rows_round_trip(tmp_path):
    usable, _ = parse_items([(238, pages()["hsr-238"])])
    items_path = tmp_path / "items.csv"
    write_csv(items_path, ITEM_HEADER, item_rows(usable))
    with items_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["code"] for row in rows] == ["11.57", "11.64", "11.64.1", "11.65"]
    assert (rows[0]["components"], rows[0]["labour"], rows[0]["material"], rows[0]["page"]) == ("47 29", "47", "29", "238")

    basic, _ = parse_basic_rates([(30, pages()["hsr-30"])])
    basic_path = tmp_path / "basic.csv"
    write_csv(basic_path, BASIC_HEADER, basic_rows(basic))
    with basic_path.open(newline="", encoding="utf-8") as handle:
        first = next(csv.DictReader(handle))
    assert (first["code"], first["unit"], first["rate"]) == ("B0179", "m2", "280")


def test_quarantine_rows_record_reason():
    doctored = pages()["hsr-238"].replace("76", "99", 1)
    _, quarantined = parse_items([(238, doctored)])
    rows = quarantine_rows(quarantined)
    assert [(row["code"], row["through"], "do not sum" in row["reason"]) for row in rows] == [("11.57", "99", True)]
    assert list(rows[0]) == QUARANTINE_HEADER
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_hsr_parse.py -q`
Expected: FAIL with `ImportError: cannot import name 'BASIC_HEADER' from 'buildflow.hsr_parse'`

- [ ] **Step 3: Write minimal implementation (append to `buildflow/hsr_parse.py`)**

```python
# buildflow/hsr_parse.py (append)
import csv
from pathlib import Path

_BASIC_LINE = re.compile(
    r"^\s*(?P<code>(?:B|PM|LB)\d{3,4})\s{2,}(?P<desc>\S.*?\S)\s{2,}"
    r"(?P<unit>[A-Za-z0-9.]+(?: [A-Za-z0-9.]+)?)\s{2,}(?P<rate>\d[\d,]*(?:\.\d+)?)\s*$"
)
_BASIC_CODE = re.compile(r"^\s*(?P<code>(?:B|PM|LB)\d{3,4})\b")

BASIC_HEADER = ["code", "category", "description", "unit_raw", "unit", "rate", "page", "source"]
QUARANTINE_HEADER = ["code", "page", "reason", "own_text", "unit_raw", "components", "through", "source"]


@dataclass(frozen=True)
class BasicRateRow:
    code: str
    category: str
    description: str
    unit_raw: str
    rate: float
    page: int
    source: str


def _category(code: str) -> str:
    if code.startswith("PM"):
        return "plant"
    if code.startswith("LB"):
        return "labour"
    return "material"


def parse_basic_rates(pages: list[tuple[int, str]], source: str = "HSR 2021") -> tuple[list[BasicRateRow], list[tuple[str, int, str]]]:
    rows: list[BasicRateRow] = []
    rejected: list[tuple[str, int, str]] = []
    for page_number, text in pages:
        for line in text.splitlines():
            match = _BASIC_LINE.match(line)
            if match:
                code = match.group("code")
                rows.append(BasicRateRow(code, _category(code), _clean(match.group("desc")), match.group("unit"),
                                         float(match.group("rate").replace(",", "")), page_number, source))
            elif (code_match := _BASIC_CODE.match(line)):
                rejected.append((code_match.group("code"), page_number, line.strip()))
    return rows, rejected


def basic_rows(rows: list[BasicRateRow]) -> list[dict[str, str]]:
    return [
        {"code": row.code, "category": row.category, "description": row.description, "unit_raw": row.unit_raw,
         "unit": canonical_unit(row.unit_raw), "rate": _fmt(row.rate), "page": str(row.page), "source": row.source}
        for row in rows
    ]


def quarantine_rows(quarantined: list[tuple[HsrItem, str]]) -> list[dict[str, str]]:
    return [
        {"code": item.code, "page": str(item.page), "reason": reason, "own_text": item.own_text, "unit_raw": item.unit_raw,
         "components": " ".join(_fmt(value) for value in item.components), "through": _fmt(item.through), "source": item.source}
        for item, reason in quarantined
    ]


def write_csv(path: str | Path, header: list[str], rows: list[dict[str, str]]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_hsr_parse.py -q`
Expected: PASS (14 passed). Then `python3 -m pytest -q` → all tests pass.

- [ ] **Step 5: Commit**

```bash
git add buildflow/hsr_parse.py tests/test_hsr_parse.py
git commit -m "feat(rates): parse HSR basic rates and write rate CSVs" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 4: Table builder and committed rate data

**Files:**
- Create: `tools/build_hsr_table.py`
- Modify: `pyproject.toml` (add the `rates` extra), `.gitignore` (add `.cache/`)
- Generate and commit: `data/rates/hsr_2021_items.csv`, `hsr_2021_basic_rates.csv`, `hsr_2021_amendments.csv`, `hsr_2021_quarantine.csv`, `SOURCE.md`
- Test: `tests/test_build_hsr_table.py`

**Interfaces:**
- Consumes: everything exported by `buildflow/hsr_parse.py` (Tasks 2–3).
- Produces: the committed CSVs in the formats `ITEM_HEADER` / `BASIC_HEADER` / `QUARANTINE_HEADER`; `check_known(items: dict[str, HsrItem], basic: dict[str, BasicRateRow]) -> list[str]` (problems; empty when all known values match).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_hsr_table.py
from __future__ import annotations

import importlib.util
from pathlib import Path

from buildflow.hsr_parse import BasicRateRow, HsrItem

ROOT = Path(__file__).resolve().parent.parent


def load_tool():
    spec = importlib.util.spec_from_file_location("build_hsr_table", ROOT / "tools" / "build_hsr_table.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_known_reports_missing_and_wrong_values():
    tool = load_tool()
    items = {code: HsrItem(code, "x", 1, "", ("labour", "material"), "HSR 2021", "sqm", (value,), value)
             for code, value in tool.KNOWN_ITEMS.items()}
    basic = {code: BasicRateRow(code, "material", "x", "sqm", value, 1, "HSR 2021") for code, value in tool.KNOWN_BASIC.items()}
    assert tool.check_known(items, basic) == []

    items["11.64.1"].through = 51.0
    del basic["PM014"]
    assert tool.check_known(items, basic) == [
        "11.64.1: expected through 50.0, got 51.0",
        "PM014: expected basic rate 12000.0, got None",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_build_hsr_table.py -q`
Expected: FAIL with `FileNotFoundError` (no `tools/build_hsr_table.py`)

- [ ] **Step 3: Write the builder**

```python
# tools/build_hsr_table.py
"""Build data/rates/ from the official HSR 2021 PDF and its A&C slips.

Run offline:  .venv/bin/python tools/build_hsr_table.py [--cache .cache/hsr] [--out data/rates]
Needs the optional `rates` extra (pypdf). The application never imports this script.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buildflow.hsr_parse import (  # noqa: E402
    BASIC_HEADER, ITEM_HEADER, QUARANTINE_HEADER, BasicRateRow, HsrItem, basic_rows, item_rows,
    parse_basic_rates, parse_items, quarantine_rows, write_csv,
)

SOURCES = {
    "base": ("HSR 2021", "https://haryanapwd.gov.in/home/docs/HSR%202021.pdf",
             "ee559c506c73931bed98c7438be1bb2f96c237df6df22cd99593bf15f8759329"),
    "slip_2024_03": ("A&C slip (14-03-2024)",
                     "https://hsamb.org.in/sites/default/files/documents/Engineering/202411/partA/Amendment%20in%20HSR%20-%2014.03.2024%20(Notification).pdf",
                     "d354304f49712abf706728f5ba4505a03ffae017aa11a14cd47b78cb5c405329"),
    "slip_19": ("A&C slip No. 19 (10-07-2024)", "https://works.haryana.gov.in/home/docs/notification01072024.pdf",
                "c385460912e773e458e29aacfe3007d7cd31a711f379d323784fe1e178b11f00"),
}
KNOWN_ITEMS = {"10.37.1": 885.0, "11.64.1": 50.0, "13.29.2": 93.0, "11.8.1": 92.0, "4.8.1": 158.0}
KNOWN_BASIC = {"B0179": 280.0, "PM014": 12000.0, "LB008": 364.0}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path, attempts: int = 10) -> None:
    """Download with HTTP range resume; the HSR server resets long transfers."""
    for _ in range(attempts):
        have = dest.stat().st_size if dest.exists() else 0
        request = urllib.request.Request(url, headers={"User-Agent": "buildflow/0.1", "Range": f"bytes={have}-"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                with dest.open("ab" if response.status == 206 else "wb") as handle:
                    while chunk := response.read(1 << 20):
                        handle.write(chunk)
            return
        except urllib.error.HTTPError as exc:
            if exc.code == 416:  # range not satisfiable: file already complete
                return
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            continue
    raise RuntimeError(f"Download kept failing: {url}")


def fetch(key: str, cache: Path) -> Path:
    _, url, expected = SOURCES[key]
    path = cache / f"{key}.pdf"
    if not path.exists() or sha256(path) != expected:
        download(url, path)
    actual = sha256(path)
    if actual != expected:
        raise SystemExit(f"{key}: SHA-256 {actual} does not match the edition this table was built from ({expected}). "
                         f"Delete {path} and re-run, or review the new edition before updating SOURCES.")
    return path


def layout_pages(path: Path) -> list[tuple[int, str]]:
    from pypdf import PdfReader  # optional dependency: pip install '.[rates]'

    reader = PdfReader(path, strict=False)
    return [(number, page.extract_text(extraction_mode="layout") or "") for number, page in enumerate(reader.pages, 1)]


def check_known(items: dict[str, HsrItem], basic: dict[str, BasicRateRow]) -> list[str]:
    problems = []
    for code, through in KNOWN_ITEMS.items():
        got = items[code].through if code in items else None
        if got != through:
            problems.append(f"{code}: expected through {through}, got {got}")
    for code, rate in KNOWN_BASIC.items():
        got = basic[code].rate if code in basic else None
        if got != rate:
            problems.append(f"{code}: expected basic rate {rate}, got {got}")
    return problems


def write_source_md(out: Path, summary: dict[str, object]) -> None:
    sources = "\n".join(f"| {label} | {url} | `{sha}` |" for label, url, sha in SOURCES.values())
    chapters = ", ".join(f"{chapter}: {count}" for chapter, count in sorted(summary["priced_by_chapter"].items(), key=lambda kv: int(kv[0] or 0)))
    (out / "SOURCE.md").write_text(f"""# Rate data provenance

Extracted from the Haryana PWD (B&R) Schedule of Rates 2021 (Third Edition) and A&C slips by
`tools/build_hsr_table.py` on {summary["built"]}. Publication of the Government of Haryana, PWD (B&R).

| Document | URL | SHA-256 |
|---|---|---|
{sources}

## Contents

- Items: {summary["items"]} ({summary["priced"]} priced; priced by chapter — {chapters})
- Unpriced leaf items (no rate line parsed): {summary["unpriced_leaves"]}
- Basic rates: {summary["basic"]} (codes not parsed: {summary["basic_rejected"]})
- Amendment items: {summary["amendments"]}
- Quarantined (failed the self-check; never used for pricing): {summary["quarantined"]}

## Reading rules

- Item columns are Labour | Material | Through (3-column pages) or Labour | Machinery | Material | Through (4-column
  pages). The last number is the through rate; components are assigned to columns only when their count matches.
- HSR 2021 item rates include 12% GST, labour welfare cess and contractor's profit; A&C slip No. 19 reads them with a
  multiple factor of 0.893 to exclude GST.
- Basic material, labour and plant rates exclude GST, labour cess, contractor's profit, overheads and carriage.

## Known gaps

- A&C slips 1–18 are not yet collected. The two slips applied here change no STP items.
""", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build data/rates from the official HSR 2021 PDF")
    parser.add_argument("--cache", default=".cache/hsr", help="Directory holding downloaded PDFs")
    parser.add_argument("--out", default="data/rates", help="Directory for the generated CSVs")
    args = parser.parse_args(argv)
    cache, out = Path(args.cache), Path(args.out)
    cache.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    base_label = SOURCES["base"][0]
    base_pages = layout_pages(fetch("base", cache))
    items, quarantined = parse_items(base_pages, base_label)
    basic, basic_rejected = parse_basic_rates(base_pages, base_label)
    amendments: list[HsrItem] = []
    for key in ("slip_2024_03", "slip_19"):
        parsed, bad = parse_items(layout_pages(fetch(key, cache)), SOURCES[key][0])
        amendments += [item for item in parsed if item.through is not None]
        quarantined += bad

    problems = check_known({item.code: item for item in items}, {row.code: row for row in basic})
    if problems:
        print("Known-value check failed; nothing written:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1

    write_csv(out / "hsr_2021_items.csv", ITEM_HEADER, item_rows(items))
    write_csv(out / "hsr_2021_basic_rates.csv", BASIC_HEADER, basic_rows(basic))
    write_csv(out / "hsr_2021_amendments.csv", ITEM_HEADER, item_rows(amendments))
    write_csv(out / "hsr_2021_quarantine.csv", QUARANTINE_HEADER, quarantine_rows(quarantined))
    codes = {item.code for item in items}
    parents = {code.rsplit(".", 1)[0] for code in codes}
    summary = {
        "built": date.today().isoformat(),
        "items": len(items),
        "priced": sum(item.through is not None for item in items),
        "priced_by_chapter": dict(Counter(item.chapter for item in items if item.through is not None)),
        "unpriced_leaves": sum(item.through is None and item.code not in parents for item in items),
        "basic": len(basic),
        "basic_rejected": len(basic_rejected),
        "amendments": len(amendments),
        "quarantined": len(quarantined),
    }
    write_source_md(out, summary)
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_build_hsr_table.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Add the optional extra and ignore the download cache**

In `pyproject.toml`, under `[project.optional-dependencies]`, add the line after `dev = [...]`:

```toml
rates = ["pypdf>=6,<7"]
```

Append to `.gitignore`:

```text
.cache/
```

- [ ] **Step 6: Build the table from the real PDFs**

```bash
python3 -m venv .venv --system-site-packages
.venv/bin/pip install 'pypdf>=6,<7'
.venv/bin/python tools/build_hsr_table.py
```

Expected: exit code 0. Reference run (2026-09-11): `items: 9085`, `priced: 6704`, `unpriced_leaves: 786`, `basic: 2229`, `amendments: 18`, `quarantined: 202` — 17 of them in the STP chapters (4–7, 10, 11, 13), most of the rest in the multi-column tables of chapters 19–26. Counts within a few percent are fine; a large rise in `quarantined` means a parsing regression. If it exits 1, the stderr lines name the codes whose known values failed. For each one, print that code's page:

```bash
.venv/bin/python -c "from pypdf import PdfReader; r = PdfReader('.cache/hsr/base.pdf', strict=False); print(next(p.extract_text(extraction_mode='layout') for p in r.pages if '11.64.1' in (p.extract_text() or '')))"
```

copy the relevant lines verbatim into a new `=== ` block in `tests/fixtures/hsr_layout_sample.txt`, add a failing test to `tests/test_hsr_parse.py` asserting the expected through rate, fix `buildflow/hsr_parse.py`, and re-run the builder. Do not hand-edit the generated CSVs.

- [ ] **Step 7: Check the generated data**

Run: `head -3 data/rates/hsr_2021_items.csv && grep -c . data/rates/hsr_2021_quarantine.csv && cat data/rates/SOURCE.md`
Expected: the items header matches `ITEM_HEADER`; `SOURCE.md` lists the three SHA-256 values and the counts printed in Step 6.

- [ ] **Step 8: Commit**

```bash
git add tools/build_hsr_table.py tests/test_build_hsr_table.py pyproject.toml .gitignore data/rates
git commit -m "feat(rates): build HSR 2021 rate tables from the official PDF" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 5: Runtime rate-table loader

**Files:**
- Create: `buildflow/rate_table.py`
- Test: `tests/test_rate_table.py`

**Interfaces:**
- Consumes: the CSVs committed in Task 4 (`ITEM_HEADER`, `BASIC_HEADER` formats); `canonical_unit` (Task 1).
- Produces:
  - `RateItem(code, full_description, unit_raw, through: float | None, labour, machinery, material: float | None, components: tuple[float, ...], page: int, source: str)` (frozen) with properties `unit -> str` and `priced -> bool`.
  - `BasicRate(code, category, description, unit_raw, rate: float, page: int, source: str)` (frozen).
  - `RateTable(items: dict[str, RateItem], basic: dict[str, BasicRate], amendments_applied: list[str])` with `priced_items() -> list[RateItem]`.
  - `DEFAULT_DIR: Path` (repo `data/rates`) and `load_rate_table(directory: str | Path | None = None) -> RateTable` — raises `FileNotFoundError` when the items CSV is missing and `ValueError` when a CSV is malformed. `DEFAULT_DIR` is read at call time so tests can monkeypatch it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rate_table.py
from __future__ import annotations

import pytest

from buildflow.hsr_parse import ITEM_HEADER, item_rows, parse_items, write_csv
from buildflow.rate_table import load_rate_table


@pytest.mark.parametrize(
    "code, components, through, unit",
    [("10.37.1", (301.0, 584.0), 885.0, "m2"), ("11.64.1", (37.0, 13.0), 50.0, "m2"),
     ("13.29.2", (19.0, 74.0), 93.0, "kg"), ("11.8.1", (44.0, 48.0), 92.0, "m2"), ("4.8.1", (158.0,), 158.0, "m3")],
)
def test_known_item_values_from_committed_table(code, components, through, unit):
    item = load_rate_table().items[code]
    assert (item.components, item.through, item.unit) == (components, through, unit)


@pytest.mark.parametrize(
    "code, category, rate, unit_raw",
    [("B0179", "material", 280.0, "sqm"), ("PM014", "plant", 12000.0, "day"), ("LB008", "labour", 364.0, "day")],
)
def test_known_basic_rates_from_committed_table(code, category, rate, unit_raw):
    basic = load_rate_table().basic[code]
    assert (basic.category, basic.rate, basic.unit_raw) == (category, rate, unit_raw)


def test_committed_slip_19_item_is_applied():
    table = load_rate_table()
    assert table.items["10.110"].through == 62.83
    assert "10.110 (A&C slip No. 19 (10-07-2024))" in table.amendments_applied


def _row(code: str, through: str, source: str) -> dict[str, str]:
    row = dict.fromkeys(ITEM_HEADER, "")
    row.update(code=code, own_text="x", full_description="x", unit_raw="sqm", unit="m2", components=through,
               through=through, page="1", source=source)
    return row


def test_amendment_replaces_base_item(tmp_path):
    write_csv(tmp_path / "hsr_2021_items.csv", ITEM_HEADER, [_row("10.110", "50", "HSR 2021")])
    write_csv(tmp_path / "hsr_2021_amendments.csv", ITEM_HEADER, [_row("10.110", "62.83", "A&C slip No. 19 (10-07-2024)")])
    table = load_rate_table(tmp_path)
    assert (table.items["10.110"].through, table.items["10.110"].source) == (62.83, "A&C slip No. 19 (10-07-2024)")
    assert table.basic == {}


def test_parents_are_loaded_but_not_priced(tmp_path):
    from tests.test_hsr_parse import pages  # fixture reader from Task 2

    usable, _ = parse_items([(238, pages()["hsr-238"])])
    write_csv(tmp_path / "hsr_2021_items.csv", ITEM_HEADER, item_rows(usable))
    table = load_rate_table(tmp_path)
    assert table.items["11.64.1"].full_description.startswith("Distempering with oil bound washable distemper")
    assert "11.64" in table.items and "11.64" not in {item.code for item in table.priced_items()}


def test_missing_table_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_rate_table(tmp_path)


def test_malformed_table_raises(tmp_path):
    write_csv(tmp_path / "hsr_2021_items.csv", ITEM_HEADER, [_row("10.110", "fifty", "HSR 2021")])
    with pytest.raises(ValueError):
        load_rate_table(tmp_path)
```


- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_rate_table.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'buildflow.rate_table'`

- [ ] **Step 3: Write minimal implementation**

```python
# buildflow/rate_table.py
"""Runtime access to the committed HSR rate tables (standard library only)."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .rate_units import canonical_unit

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "rates"
ITEMS_FILE = "hsr_2021_items.csv"
BASIC_FILE = "hsr_2021_basic_rates.csv"
AMENDMENTS_FILE = "hsr_2021_amendments.csv"


@dataclass(frozen=True)
class RateItem:
    code: str
    full_description: str
    unit_raw: str
    through: float | None
    labour: float | None
    machinery: float | None
    material: float | None
    components: tuple[float, ...]
    page: int
    source: str

    @property
    def unit(self) -> str:
        return canonical_unit(self.unit_raw)

    @property
    def priced(self) -> bool:
        return self.through is not None


@dataclass(frozen=True)
class BasicRate:
    code: str
    category: str
    description: str
    unit_raw: str
    rate: float
    page: int
    source: str


@dataclass
class RateTable:
    items: dict[str, RateItem]
    basic: dict[str, BasicRate]
    amendments_applied: list[str] = field(default_factory=list)

    def priced_items(self) -> list[RateItem]:
        return [item for item in self.items.values() if item.priced]


def _number(text: str) -> float | None:
    return float(text) if text.strip() else None


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _item(row: dict[str, str]) -> RateItem:
    return RateItem(
        row["code"], row["full_description"], row["unit_raw"], _number(row["through"]), _number(row["labour"]),
        _number(row["machinery"]), _number(row["material"]), tuple(float(value) for value in row["components"].split()),
        int(row["page"] or 0), row["source"],
    )


def load_rate_table(directory: str | Path | None = None) -> RateTable:
    folder = Path(directory) if directory is not None else DEFAULT_DIR
    items_path = folder / ITEMS_FILE
    if not items_path.exists():
        raise FileNotFoundError(f"HSR rate table not found: {items_path}")
    try:
        items = {row["code"]: _item(row) for row in _read(items_path)}
        applied: list[str] = []
        if (folder / AMENDMENTS_FILE).exists():
            for row in _read(folder / AMENDMENTS_FILE):
                items[row["code"]] = _item(row)
                applied.append(f'{row["code"]} ({row["source"]})')
        basic: dict[str, BasicRate] = {}
        if (folder / BASIC_FILE).exists():
            for row in _read(folder / BASIC_FILE):
                basic[row["code"]] = BasicRate(row["code"], row["category"], row["description"], row["unit_raw"],
                                               float(row["rate"]), int(row["page"] or 0), row["source"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Malformed HSR rate table in {folder}: {exc}") from exc
    return RateTable(items, basic, applied)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_rate_table.py -q`
Expected: PASS (13 passed). Then `python3 -m pytest -q` → all tests pass.

- [ ] **Step 5: Commit**

```bash
git add buildflow/rate_table.py tests/test_rate_table.py
git commit -m "feat(rates): load committed HSR rate tables at runtime" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 6: Content tokens, shortlist and verbatim rule

**Files:**
- Create: `buildflow/rate_matching.py`
- Test: `tests/test_rate_matching.py`

**Interfaces:**
- Consumes: `RateTable`, `RateItem` (Task 5); `unit_factor` (Task 1).
- Produces:
  - `content_tokens(text: str) -> frozenset[str]` (lower-cased alphanumeric runs, keeping numbers, sizes and ratios such as `20`, `1:4`, `1.5`; stopwords removed).
  - `Candidate(code: str, score: float, unit_factor: float)` (frozen).
  - `shortlist(description: str, unit: str, table: RateTable, size: int = 8) -> list[Candidate]` — unit-compatible priced items, highest Jaccard first, ties by code.
  - `verbatim_match(description: str, unit: str, table: RateTable) -> Candidate | None`.
  - Constants `SHORTLIST_SIZE = 8`, `VERBATIM_MIN_TOKENS = 6`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rate_matching.py
from __future__ import annotations

from buildflow.rate_matching import content_tokens, shortlist, verbatim_match
from buildflow.rate_table import RateItem, RateTable, load_rate_table

ROW_124 = ("FINISHING > Distempering with oil bound washable distemper of approved brand and manufacture to give an even "
           "shade : Approved make : Asian, Nerolac, Berger, Dulux, Birla Opus > New work (two or more coats) over and "
           "including water thinnable priming coat with cement primer")
ROW_121 = "FINISHING > 18-20 mm cement plaster of mix : > 1:4 (1 cement: 4 coarse sand)"
ROW_102 = ("FLOORING > Kota stone slab flooring over 20 mm (average) thick base laid over and jointed with grey cement "
           "slurry mixed with pigment to match the shade of the slab, including rubbing and polishing complete with base "
           "of cement mortar 1 : 4 (1 cement : 4 coarse sand), along with Kota stone skirting of 1 feet all around : Base "
           "Rate of Kota : Rs: 35 Per Sqft. > 20 mm thick Kota stone (Inside Plant Room only)")


def item(code: str, text: str, unit: str, through: float = 100.0) -> RateItem:
    return RateItem(code, text, unit, through, None, None, None, (through,), 1, "HSR 2021")


def table(*items: RateItem) -> RateTable:
    return RateTable({entry.code: entry for entry in items}, {})


def test_content_tokens_keep_sizes_and_ratios_but_drop_stopwords():
    tokens = content_tokens("Cement plaster 1:4 (1 cement: 4 coarse sand), 20 mm thick of the wall")
    assert {"cement", "plaster", "1:4", "1", "4", "coarse", "sand", "20", "mm", "thick", "wall"} == tokens


def test_shortlist_keeps_only_compatible_units_best_first():
    rates = table(
        item("11.1", "Cement plaster 20 mm thick 1:4", "sqm"),
        item("11.2", "Cement plaster 20 mm thick 1:4", "cum"),
        item("10.1", "Kota stone flooring 25 mm thick", "sqm"),
    )
    assert [candidate.code for candidate in shortlist("cement plaster 20 mm 1:4", "Sqm", rates)] == ["11.1", "10.1"]


def test_shortlist_carries_the_unit_factor():
    rates = table(item("13.1", "Steel work in gratings frames", "quintal"))
    (candidate,) = shortlist("steel gratings", "Kg", rates)
    assert candidate.unit_factor == 0.01


def test_verbatim_rule_on_real_rows():
    rates = load_rate_table()
    assert verbatim_match(ROW_124, "Sqm", rates).code == "11.64.1"
    assert verbatim_match(ROW_121, "Sqm", rates).code == "11.10.1"
    assert verbatim_match(ROW_102, "Sqm", rates) is None


def test_verbatim_needs_a_unique_candidate():
    text = "Cement plaster 20 mm thick 1:4 coarse sand"
    rates = table(item("11.1", text, "sqm"), item("11.2", text, "sqm"))
    assert verbatim_match(f"FINISHING > {text}", "Sqm", rates) is None


def test_verbatim_ignores_short_generic_items():
    rates = table(item("4.8.1", "All kinds of soil", "cum"))
    assert verbatim_match("EXCAVATION > Earth work in excavation > All kinds of soil", "Cum", rates) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_rate_matching.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'buildflow.rate_matching'`

- [ ] **Step 3: Write minimal implementation**

```python
# buildflow/rate_matching.py
"""Match BOQ rows to HSR items: deterministic shortlist, verbatim rule, optional model pick, frozen matches."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterator

from .rate_table import RateItem, RateTable
from .rate_units import unit_factor

STOPWORDS = frozenset(
    "a all an and any approved are as at be by etc for from in including is its make of on or per such that the "
    "thereof this to up upto with".split()
)
SHORTLIST_SIZE = 8
VERBATIM_MIN_TOKENS = 6
_TOKEN = re.compile(r"[a-z0-9]+(?:[.:/][0-9]+)*")


@lru_cache(maxsize=None)
def content_tokens(text: str) -> frozenset[str]:
    return frozenset(token for token in _TOKEN.findall(text.lower()) if token not in STOPWORDS)


@dataclass(frozen=True)
class Candidate:
    code: str
    score: float
    unit_factor: float


def _compatible(unit: str, table: RateTable) -> Iterator[tuple[RateItem, float]]:
    for entry in table.priced_items():
        factor = unit_factor(unit, entry.unit_raw)
        if factor is not None:
            yield entry, factor


def shortlist(description: str, unit: str, table: RateTable, size: int = SHORTLIST_SIZE) -> list[Candidate]:
    query = content_tokens(description)
    scored = []
    for entry, factor in _compatible(unit, table):
        tokens = content_tokens(entry.full_description)
        union = query | tokens
        scored.append(Candidate(entry.code, len(query & tokens) / len(union) if union else 0.0, factor))
    scored.sort(key=lambda candidate: (-candidate.score, candidate.code))
    return scored[:size]


def verbatim_match(description: str, unit: str, table: RateTable) -> Candidate | None:
    """The unique HSR item whose every content token appears in the BOQ description, if there is exactly one."""
    query = content_tokens(description)
    hits = [
        Candidate(entry.code, 1.0, factor)
        for entry, factor in _compatible(unit, table)
        if len(tokens := content_tokens(entry.full_description)) >= VERBATIM_MIN_TOKENS and tokens <= query
    ]
    return hits[0] if len(hits) == 1 else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_rate_matching.py -q`
Expected: PASS (6 passed). Then `python3 -m pytest -q` → all tests pass.

- [ ] **Step 5: Commit**

```bash
git add buildflow/rate_matching.py tests/test_rate_matching.py
git commit -m "feat(rates): add content tokens, shortlist and verbatim matching" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 7: Match flow and frozen matches

**Files:**
- Modify: `buildflow/models.py` (add `RateMatch` after `BOQItem`)
- Modify: `buildflow/rate_matching.py` (append)
- Test: `tests/test_rate_matching.py` (append)

**Interfaces:**
- Consumes: `content_tokens`, `shortlist`, `verbatim_match`, `Candidate` (Task 6); `RateTable` (Task 5); `unit_factor` (Task 1); `BOQItem` (existing).
- Produces:
  - `RateMatch` dataclass in `buildflow/models.py`: `source_sheet: str, source_row: int, description_sha1: str, boq_description: str, unit: str, hsr_code: str = "", multiplier: float = 1.0, status: str = "none", method: str = "", spec_differences: str = "", model: str = "", date: str = "", reason: str = ""`.
  - `MATCH_HEADER: list[str]`, `STATUSES: tuple[str, ...]`, `Picker = Callable[[list[dict]], list[dict]]`.
  - `row_key(item: BOQItem) -> tuple[str, int]`; `key_text(key: tuple[str, int]) -> str` (`"03 Boq:121"`); `description_sha1(text: str) -> str`.
  - `MatchReport(frozen: int, verbatim: int, groq: int, unmatched: int, rejected_picks: list[str])`.
  - `match_items(items: list[BOQItem], table: RateTable, frozen: dict[tuple[str, int], RateMatch], picker: Picker | None = None, model: str = "", today: str | None = None) -> tuple[dict[tuple[str, int], RateMatch], MatchReport]`. A picker request is `{"row_key", "description", "unit", "candidates": [{"code", "description" (≤240 chars), "unit"}]}`; a pick is `{"row_key", "hsr_code", "spec_differences", "confidence", "reason"}`.
  - `write_rate_matches(path, matches: list[RateMatch]) -> None`; `read_rate_matches(path, items: list[BOQItem], table: RateTable) -> tuple[dict[tuple[str, int], RateMatch], list[str], list[str]]` (frozen, rejected lines, stale lines).

- [ ] **Step 1: Write the failing tests (append to `tests/test_rate_matching.py`)**

```python
# tests/test_rate_matching.py (append)
import csv

from buildflow.models import BOQItem, RateMatch
from buildflow.rate_matching import (
    MATCH_HEADER, description_sha1, match_items, read_rate_matches, row_key, write_rate_matches,
)

PLASTER = "Cement plaster 20 mm thick 1:4 coarse sand finished smooth"
KOTA = "Kota stone slab flooring 25 mm thick polished"


def boq(row: int, description: str, unit: str = "Sqm") -> BOQItem:
    return BOQItem(f"BOQ-{row:03d}", description, unit, 10.0, 0.0, 0.0, source_row=row, source_sheet="03 Boq")


def never_called(requests):
    raise AssertionError("the model must not be asked")


def rates() -> RateTable:
    return table(item("11.10.1", PLASTER, "sqm", 144.0), item("10.37.1", KOTA, "sqm", 885.0),
                 item("4.8.1", "Earth work in excavation all kinds of soil", "cum", 158.0))


def test_verbatim_row_needs_no_model():
    matches, report = match_items([boq(121, f"FINISHING > {PLASTER}")], rates(), {}, never_called, today="2026-09-11")
    match = matches[("03 Boq", 121)]
    assert (match.hsr_code, match.status, match.method, report.verbatim) == ("11.10.1", "verbatim", "verbatim", 1)


def test_frozen_match_is_reused_without_asking_the_model():
    row = boq(102, "20 mm thick Kota stone")
    frozen = {row_key(row): RateMatch("03 Boq", 102, description_sha1(row.description), row.description, "Sqm",
                                      "10.37.1", 1.0, "spec-approved", "manual")}
    matches, report = match_items([row], rates(), frozen, never_called)
    assert matches[("03 Boq", 102)] is frozen[("03 Boq", 102)]
    assert report.frozen == 1


def test_model_pick_becomes_proposed_or_spec_mismatch_and_sends_no_numbers():
    seen = []

    def picker(requests):
        seen.extend(requests)
        return [
            {"row_key": "03 Boq:10", "hsr_code": "11.10.1", "spec_differences": "", "confidence": 0.9, "reason": "plaster"},
            {"row_key": "03 Boq:11", "hsr_code": "10.37.1", "spec_differences": "BOQ 20 mm, HSR 25 mm",
             "confidence": 0.8, "reason": "kota"},
        ]

    rows = [boq(10, "plastering walls"), boq(11, "kota flooring 20 mm")]
    matches, report = match_items(rows, rates(), {}, picker, model="openai/gpt-oss-120b", today="2026-09-11")
    assert (matches[("03 Boq", 10)].status, matches[("03 Boq", 11)].status) == ("ai-proposed", "spec-mismatch")
    assert matches[("03 Boq", 11)].spec_differences == "BOQ 20 mm, HSR 25 mm"
    assert report.groq == 2
    assert {candidate["code"] for candidate in seen[0]["candidates"]} == {"11.10.1", "10.37.1"}
    assert set(seen[0]) == {"row_key", "description", "unit", "candidates"}


def test_pick_outside_the_candidates_is_rejected():
    def picker(requests):
        return [{"row_key": "03 Boq:10", "hsr_code": "4.8.1", "spec_differences": "", "confidence": 0.9, "reason": "x"}]

    matches, report = match_items([boq(10, "plastering walls")], rates(), {}, picker)
    assert matches[("03 Boq", 10)].status == "none"
    assert report.rejected_picks == ["03 Boq:10: '4.8.1' is not one of the row's candidates"]


def test_without_a_picker_unmatched_rows_stay_none():
    matches, report = match_items([boq(10, "plastering walls")], rates(), {}, None)
    assert (matches[("03 Boq", 10)].status, report.unmatched) == ("none", 1)


def test_rate_matches_round_trip(tmp_path):
    rows = [boq(121, f"FINISHING > {PLASTER}")]
    matches, _ = match_items(rows, rates(), {}, None, today="2026-09-11")
    path = tmp_path / "rate_matches.csv"
    write_rate_matches(path, list(matches.values()))
    assert read_rate_matches(path, rows, rates()) == (matches, [], [])


def test_edited_lines_are_checked(tmp_path):
    rows = [boq(121, "plaster"), boq(84, "waterproofing"), boq(16, "extra lift", "Cum")]

    def line(row, **changes):
        values = {"source_sheet": "03 Boq", "source_row": str(row.source_row), "description_sha1": description_sha1(row.description),
                  "boq_description": row.description, "unit": row.unit, "hsr_code": "11.10.1", "multiplier": "1",
                  "status": "ai-confirmed", "method": "manual", "spec_differences": "", "model": "", "date": "", "reason": ""}
        values.update(changes)
        return values

    path = tmp_path / "rate_matches.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCH_HEADER)
        writer.writeheader()
        writer.writerows([
            line(rows[0], description_sha1="0" * 40),
            line(rows[1], hsr_code="99.99"),
            line(rows[2]),
            line(rows[0], source_row="999"),
            line(rows[1], multiplier="0"),
        ])
    frozen, rejected, stale = read_rate_matches(path, rows, rates())
    assert frozen == {}
    assert stale == ["line 2: 03 Boq:121 changed since it was matched"]
    assert rejected == [
        "line 3: unknown HSR code '99.99'",
        "line 4: Cum is incompatible with 11.10.1 (sqm)",
        "line 5: 03 Boq:999 is not a row of this BOQ",
        "line 6: multiplier must be positive",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_rate_matching.py -q`
Expected: FAIL with `ImportError: cannot import name 'RateMatch' from 'buildflow.models'`

- [ ] **Step 3: Add `RateMatch` to `buildflow/models.py` (directly after the `BOQItem` class)**

```python
@dataclass
class RateMatch:
    """One BOQ row's HSR match, as frozen in rate_matches.csv."""

    source_sheet: str
    source_row: int
    description_sha1: str
    boq_description: str
    unit: str
    hsr_code: str = ""
    multiplier: float = 1.0
    status: str = "none"
    method: str = ""
    spec_differences: str = ""
    model: str = ""
    date: str = ""
    reason: str = ""
```

- [ ] **Step 4: Append the match flow to `buildflow/rate_matching.py`**

```python
# buildflow/rate_matching.py (append)
import csv
import hashlib
from dataclasses import field
from datetime import date
from pathlib import Path
from typing import Callable

from .models import BOQItem, RateMatch

MATCH_HEADER = ["source_sheet", "source_row", "description_sha1", "boq_description", "unit", "hsr_code", "multiplier",
                "status", "method", "spec_differences", "model", "date", "reason"]
STATUSES = ("verbatim", "ai-proposed", "spec-mismatch", "ai-confirmed", "spec-approved", "none")
CANDIDATE_TEXT_LIMIT = 240
Picker = Callable[[list[dict]], list[dict]]


def row_key(item: BOQItem) -> tuple[str, int]:
    return (item.source_sheet, item.source_row)


def key_text(key: tuple[str, int]) -> str:
    return f"{key[0]}:{key[1]}"


def description_sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


@dataclass
class MatchReport:
    frozen: int = 0
    verbatim: int = 0
    groq: int = 0
    unmatched: int = 0
    rejected_picks: list[str] = field(default_factory=list)


def _new_match(item: BOQItem, **fields) -> RateMatch:
    return RateMatch(item.source_sheet, item.source_row, description_sha1(item.description), item.description, item.unit, **fields)


def match_items(
    items: list[BOQItem],
    table: RateTable,
    frozen: dict[tuple[str, int], RateMatch],
    picker: Picker | None = None,
    model: str = "",
    today: str | None = None,
) -> tuple[dict[tuple[str, int], RateMatch], MatchReport]:
    today = today or date.today().isoformat()
    matches: dict[tuple[str, int], RateMatch] = {}
    report = MatchReport()
    pending: list[BOQItem] = []
    for entry in items:
        key = row_key(entry)
        if key in frozen:
            matches[key] = frozen[key]
            report.frozen += 1
            continue
        hit = verbatim_match(entry.description, entry.unit, table)
        if hit:
            matches[key] = _new_match(entry, hsr_code=hit.code, status="verbatim", method="verbatim", date=today,
                                      reason="every HSR content token appears in the BOQ text")
            report.verbatim += 1
        else:
            pending.append(entry)

    requests: list[dict] = []
    allowed: dict[str, set[str]] = {}
    if picker:
        for entry in pending:
            candidates = shortlist(entry.description, entry.unit, table)
            if not candidates:
                continue
            text = key_text(row_key(entry))
            allowed[text] = {candidate.code for candidate in candidates}
            requests.append({
                "row_key": text,
                "description": entry.description,
                "unit": entry.unit,
                "candidates": [
                    {"code": candidate.code,
                     "description": table.items[candidate.code].full_description[:CANDIDATE_TEXT_LIMIT],
                     "unit": table.items[candidate.code].unit_raw}
                    for candidate in candidates
                ],
            })
    picks = {pick.get("row_key"): pick for pick in (picker(requests) if picker and requests else [])}

    for entry in pending:
        key = row_key(entry)
        text = key_text(key)
        pick = picks.get(text)
        code = str(pick.get("hsr_code", "")) if pick else ""
        if pick and code != "none" and code not in allowed.get(text, set()):
            report.rejected_picks.append(f"{text}: {code!r} is not one of the row's candidates")
            pick, code = None, ""
        if pick and code != "none":
            spec = str(pick.get("spec_differences", "")).strip()
            matches[key] = _new_match(entry, hsr_code=code, status="spec-mismatch" if spec else "ai-proposed", method="groq",
                                      spec_differences=spec, model=model, date=today, reason=str(pick.get("reason", ""))[:180])
            report.groq += 1
        else:
            matches[key] = _new_match(entry, status="none", method="groq" if pick else "", model=model if pick else "",
                                      date=today, reason=str(pick.get("reason", ""))[:180] if pick else "no verbatim match or model pick")
            report.unmatched += 1
    return matches, report


def write_rate_matches(path: str | Path, matches: list[RateMatch]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCH_HEADER)
        writer.writeheader()
        for match in matches:
            writer.writerow({
                "source_sheet": match.source_sheet, "source_row": match.source_row, "description_sha1": match.description_sha1,
                "boq_description": match.boq_description, "unit": match.unit, "hsr_code": match.hsr_code,
                "multiplier": f"{match.multiplier:g}", "status": match.status, "method": match.method,
                "spec_differences": match.spec_differences, "model": match.model, "date": match.date, "reason": match.reason,
            })


def read_rate_matches(
    path: str | Path, items: list[BOQItem], table: RateTable
) -> tuple[dict[tuple[str, int], RateMatch], list[str], list[str]]:
    by_key = {row_key(entry): entry for entry in items}
    frozen: dict[tuple[str, int], RateMatch] = {}
    rejected: list[str] = []
    stale: list[str] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            try:
                key = (row["source_sheet"], int(row["source_row"]))
            except (KeyError, TypeError, ValueError):
                rejected.append(f"line {line_number}: unreadable row key")
                continue
            entry = by_key.get(key)
            if entry is None:
                rejected.append(f"line {line_number}: {key_text(key)} is not a row of this BOQ")
                continue
            if row.get("description_sha1") != description_sha1(entry.description):
                stale.append(f"line {line_number}: {key_text(key)} changed since it was matched")
                continue
            status, code = (row.get("status") or "").strip(), (row.get("hsr_code") or "").strip()
            if status not in STATUSES:
                rejected.append(f"line {line_number}: unknown status {status!r}")
                continue
            if status != "none":
                if code not in table.items or not table.items[code].priced:
                    rejected.append(f"line {line_number}: unknown HSR code {code!r}")
                    continue
                if unit_factor(entry.unit, table.items[code].unit_raw) is None:
                    rejected.append(f"line {line_number}: {entry.unit} is incompatible with {code} ({table.items[code].unit_raw})")
                    continue
            try:
                multiplier = float(row.get("multiplier") or 1)
            except ValueError:
                multiplier = 0.0
            if multiplier <= 0:
                rejected.append(f"line {line_number}: multiplier must be positive")
                continue
            frozen[key] = RateMatch(key[0], key[1], row["description_sha1"], row.get("boq_description", ""),
                                    row.get("unit", ""), code if status != "none" else "", multiplier, status,
                                    row.get("method", ""), row.get("spec_differences", ""), row.get("model", ""),
                                    row.get("date", ""), row.get("reason", ""))
    return frozen, rejected, stale
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_rate_matching.py -q`
Expected: PASS (13 passed). Then `python3 -m pytest -q` → all tests pass.

- [ ] **Step 6: Commit**

```bash
git add buildflow/models.py buildflow/rate_matching.py tests/test_rate_matching.py
git commit -m "feat(rates): match BOQ rows to HSR items and freeze matches in CSV" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 8: Groq matching request

**Files:**
- Modify: `buildflow/llm.py` (extract `_post_structured`; add `MATCH_BATCH_SIZE`, `MATCH_INSTRUCTIONS`, `_match_payload`, `match_rate_items`)
- Test: `tests/test_llm.py` (append)

**Interfaces:**
- Consumes: existing `GROQ`, `Transport`, `_post_json`, `MAX_ATTEMPTS`, `MAX_RETRY_WAIT_SECONDS`, `LLMClassificationError` in `buildflow/llm.py`; the request/pick shapes defined in Task 7.
- Produces: `match_rate_items(requests: list[dict], model: str, transport: Transport | None = None, sleep: Callable[[float], object] = time.sleep) -> list[dict]` — raw picks, all-or-nothing across batches (any failed batch raises `LLMClassificationError`). Candidate validation stays in `rate_matching.match_items` (Task 7). `MATCH_BATCH_SIZE = 5`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_llm.py`)**

```python
# tests/test_llm.py (append)
def structured(content: dict) -> dict:
    body = completion([])
    body["choices"][0]["message"]["content"] = json.dumps(content)
    return body


def match_request(row: int, codes: tuple[str, ...] = ("11.10.1", "10.37.1")) -> dict:
    return {"row_key": f"03 Boq:{row}", "description": "plastering walls", "unit": "Sqm",
            "candidates": [{"code": code, "description": f"item {code}", "unit": "sqm"} for code in codes]}


def test_match_request_sends_rows_and_candidates_only():
    pick = {"row_key": "03 Boq:1", "hsr_code": "11.10.1", "spec_differences": "", "confidence": 0.9, "reason": "r"}
    fake = FakeGroq((200, {}, structured({"matches": [pick]})))
    picks = llm.match_rate_items([match_request(1)], "openai/gpt-oss-120b", transport=fake)
    ((url, headers, payload),) = fake.requests
    schema = payload["response_format"]["json_schema"]
    assert (url, schema["strict"], headers.get("User-Agent") is not None) == (GROQ_URL, True, True)
    assert schema["schema"]["properties"]["matches"]["items"]["properties"]["hsr_code"]["enum"] == ["10.37.1", "11.10.1", "none"]
    assert json.loads(payload["messages"][-1]["content"]) == {"rows": [match_request(1)]}
    assert picks == [pick]


def test_match_requests_are_batched():
    fake = FakeGroq(*[(200, {}, structured({"matches": []}))] * 3)
    llm.match_rate_items([match_request(n) for n in range(1, 2 * llm.MATCH_BATCH_SIZE + 2)], "m", transport=fake)
    sizes = [len(json.loads(payload["messages"][-1]["content"])["rows"]) for _, _, payload in fake.requests]
    assert sizes == [llm.MATCH_BATCH_SIZE, llm.MATCH_BATCH_SIZE, 1]


def test_match_without_key_raises(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(LLMClassificationError, match="GROQ_API_KEY"):
        llm.match_rate_items([match_request(1)], "m", transport=FakeGroq())


def test_unreadable_match_reply_raises():
    with pytest.raises(LLMClassificationError):
        llm.match_rate_items([match_request(1)], "m", transport=FakeGroq((200, {}, structured({"classifications": []}))))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_llm.py -q`
Expected: the 4 new tests FAIL with `AttributeError: module 'buildflow.llm' has no attribute 'match_rate_items'` (or `MATCH_BATCH_SIZE`); the existing 17 pass.

- [ ] **Step 3: Refactor — extract the shared request helper**

In `buildflow/llm.py`, replace the whole `_request_classifications` function (from `def _request_classifications(` through its final `raise LLMClassificationError(f"Groq returned an unreadable classification: {exc}") from exc`) with:

```python
def _post_structured(post: Transport, api_key: str, payload: dict, sleep: Callable[[float], object]) -> dict:
    """POST a strict-schema request with bounded 429 retries; return the parsed JSON object from the reply."""
    url = f"{GROQ.base_url}/chat/completions"
    # An explicit User-Agent is required: Groq's edge rejects urllib's default signature (HTTP 403, error 1010).
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "buildflow/0.1"}
    attempt = 1
    status, response_headers, raw = post(url, headers, payload)
    while status == 429 and attempt < MAX_ATTEMPTS:
        try:
            wait = float(response_headers.get("retry-after", 1))
        except ValueError:  # HTTP-date form; fail fast rather than parse dates for a fallback path
            break
        if wait > MAX_RETRY_WAIT_SECONDS:
            break
        sleep(wait)
        attempt += 1
        status, response_headers, raw = post(url, headers, payload)
    if status != 200:
        raise LLMClassificationError(f"Groq request failed: HTTP {status} {raw}")
    try:
        content = json.loads(raw["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMClassificationError(f"Groq returned an unreadable response: {exc}") from exc
    if not isinstance(content, dict):
        raise LLMClassificationError("Groq returned an unreadable response: not a JSON object")
    return content


def _request_classifications(
    post: Transport, api_key: str, model: str, batch: list[BOQItem], packages: list[str], sleep: Callable[[float], object]
) -> list:
    entries = _post_structured(post, api_key, _request_payload(model, batch, packages), sleep).get("classifications")
    if not isinstance(entries, list):
        raise LLMClassificationError("Groq returned an unreadable classification: no classifications list")
    return entries
```

Run: `python3 -m pytest tests/test_llm.py -q`
Expected: the existing 17 tests still pass (the refactor keeps behaviour); the 4 new tests still fail.

- [ ] **Step 4: Add the matching request (append to `buildflow/llm.py`)**

```python
# buildflow/llm.py (append)
MATCH_BATCH_SIZE = 5  # rows per request; each row carries up to 8 candidate texts, and the free tier allows 8K tokens/minute
MATCH_INSTRUCTIONS = (
    "You match Indian construction BOQ line items to items of the Haryana PWD Schedule of Rates (HSR). For each row, "
    "choose the single candidate code whose work, material, size and grade fit the row, or 'none' if no candidate fits. "
    "Describe any size, grade or specification difference between the row and the chosen item in spec_differences, or "
    "return an empty string when there is none. Never estimate rates, quantities or amounts."
)


def _match_payload(model: str, requests: list[dict], codes: list[str]) -> dict:
    schema = {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "row_key": {"type": "string"},
                        "hsr_code": {"type": "string", "enum": codes},
                        "spec_differences": {"type": "string"},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["row_key", "hsr_code", "spec_differences", "confidence", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["matches"],
        "additionalProperties": False,
    }
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": MATCH_INSTRUCTIONS},
            {"role": "user", "content": json.dumps({"rows": requests})},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "hsr_match", "strict": True, "schema": schema}},
    }


def match_rate_items(
    requests: list[dict],
    model: str,
    transport: Transport | None = None,
    sleep: Callable[[float], object] = time.sleep,
) -> list[dict]:
    """Ask Groq to pick one candidate HSR code, or 'none', per row. Sends descriptions, units and candidate texts only."""
    api_key = os.getenv(GROQ.key_env, "").strip()
    if not api_key:
        raise LLMClassificationError(f"{GROQ.key_env} is not set")
    post = transport or _post_json
    picks: list[dict] = []
    for start in range(0, len(requests), MATCH_BATCH_SIZE):
        batch = requests[start : start + MATCH_BATCH_SIZE]
        codes = sorted({candidate["code"] for request in batch for candidate in request["candidates"]}) + ["none"]
        entries = _post_structured(post, api_key, _match_payload(model, batch, codes), sleep).get("matches")
        if not isinstance(entries, list):
            raise LLMClassificationError("Groq returned an unreadable match: no matches list")
        picks.extend(entry for entry in entries if isinstance(entry, dict))
    return picks
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_llm.py -q`
Expected: PASS (21 passed). Then `python3 -m pytest -q` → all tests pass.

- [ ] **Step 6: Commit**

```bash
git add buildflow/llm.py tests/test_llm.py
git commit -m "feat(rates): ask Groq to pick HSR candidates under a strict schema" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 9: Pricing, rate sources and settings

**Files:**
- Modify: `buildflow/models.py` (`BOQItem`, `ProjectConfig`, `config_from_dict`, `AnalysisResult`)
- Create: `buildflow/pricing.py`
- Test: `tests/test_pricing.py`

**Interfaces:**
- Consumes: `RateMatch` (Task 7), `RateTable`/`RateItem` (Task 5), `unit_factor` (Task 1).
- Produces:
  - `BOQItem` fields: `rate_source: str = "unpriced"`, `hsr_code: str = ""`, `match_status: str = ""`, `unit_factor: float = 1.0`, `multiplier: float = 1.0`, `net_rate: float = 0.0`, `priced_amount: float = 0.0`, `ai_guess_rate: float | None = None`. After pricing, `rate`/`amount` hold the basis-applied values that scheduling and cash flow use.
  - `ProjectConfig` fields: `rate_basis: str = "validation"`, `gst_pct: float = 18.0`, `gst_confirmed: bool = False`, `escalation: float = 1.0`, `profit_overhead_pct: float = 15.0`, `labour_cess_pct: float = 1.0`, `rate_matches_path: str = ""`, `ai_components_path: str = ""`, `ai_guesses_path: str = ""`.
  - `AnalysisResult` fields: `rate_matches: list[RateMatch]`, `rate_totals: dict[str, float]`, `rate_rows: list[dict]`, `rate_components: list` (all default empty).
  - `pricing.GST_EXCL_FACTOR = 0.893`, `RATE_SOURCES`, `BASIS_SOURCES: dict[str, tuple[str, ...]]`.
  - `hsr_net_rate(through: float, factor: float, multiplier: float, escalation: float) -> float`.
  - `price_items(items, matches: dict[tuple[str, int], RateMatch], table: RateTable | None, config: ProjectConfig, ai_rates: dict[tuple[str, int], float] | None = None, ai_guesses: dict[tuple[str, int], float] | None = None) -> None`.
  - `totals(items, gst_pct: float) -> dict[str, float]` with keys `source:<name>` for each rate source, `validation`, `demo`, `validation_incl_gst`, `demo_incl_gst`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pricing.py
from __future__ import annotations

import pytest

from buildflow.models import BOQItem, ProjectConfig, RateMatch, config_from_dict
from buildflow.pricing import price_items, totals
from buildflow.rate_table import RateItem, RateTable

UNPRICED = "Unpriced item; excluded from monetary totals"


def boq(row: int, unit: str = "Sqm", quantity: float = 10.0, rate: float = 0.0, amount: float = 0.0) -> BOQItem:
    flags = [] if rate or amount else [UNPRICED]
    return BOQItem(f"BOQ-{row:03d}", f"row {row}", unit, quantity, rate, amount, source_row=row, source_sheet="03 Boq", flags=flags)


def match(row: int, code: str, status: str = "verbatim", multiplier: float = 1.0) -> RateMatch:
    return RateMatch("03 Boq", row, "sha", f"row {row}", "Sqm", code, multiplier, status, "verbatim")


def rates() -> RateTable:
    return RateTable({
        "11.64.1": RateItem("11.64.1", "Distemper", "sqm", 50.0, 37.0, None, 13.0, (37.0, 13.0), 239, "HSR 2021"),
        "5.1": RateItem("5.1", "Steel", "quintal", 1000.0, None, None, None, (1000.0,), 1, "HSR 2021"),
        "4.31.1": RateItem("4.31.1", "Extra lift", "cum", 100.0, None, None, None, (100.0,), 159, "HSR 2021"),
    }, {})


def price(items: list[BOQItem], matches: list[RateMatch], ai_rates=None, ai_guesses=None, **settings) -> list[BOQItem]:
    keyed = {(m.source_sheet, m.source_row): m for m in matches}
    price_items(items, keyed, rates(), ProjectConfig(**settings), ai_rates, ai_guesses)
    return items


def test_verbatim_distemper_is_priced_net_of_gst():
    (row,) = price([boq(124)], [match(124, "11.64.1")])  # 50 × 0.893 = 44.65 per sqm
    assert (row.rate_source, round(row.net_rate, 4), round(row.amount, 2), row.flags) == ("hsr", 44.65, 446.5, [])


def test_quintal_rate_is_converted_to_kg():
    (row,) = price([boq(67, "Kg", 200.0)], [match(67, "5.1")])  # 1000 × 0.01 × 0.893 = 8.93 per kg
    assert (row.unit_factor, round(row.net_rate, 4), round(row.amount, 2)) == (0.01, 8.93, 1786.0)


def test_multiplier_scales_a_lift_band():
    (row,) = price([boq(18, "Cum", 1.0)], [match(18, "4.31.1", "spec-approved", 2.0)])
    assert round(row.net_rate, 4) == 178.6


def test_escalation_scales_hsr_rates():
    (row,) = price([boq(124)], [match(124, "11.64.1")], escalation=1.1)
    assert round(row.net_rate, 4) == 49.115


def test_proposed_match_counts_only_in_the_demo_basis():
    (validation,) = price([boq(10)], [match(10, "11.64.1", "ai-proposed")])
    assert (validation.rate_source, validation.amount, round(validation.priced_amount, 2)) == ("hsr-proposed", 0.0, 446.5)
    assert validation.flags == ["Priced as hsr-proposed; outside the validation basis, so excluded from totals"]
    (demo,) = price([boq(10)], [match(10, "11.64.1", "ai-proposed")], rate_basis="demo")
    assert round(demo.amount, 2) == 446.5


def test_tender_rate_wins_over_a_match():
    (row,) = price([boq(5, rate=120.0, amount=1200.0)], [match(5, "11.64.1")])
    assert (row.rate_source, row.net_rate, row.amount) == ("tender", 120.0, 1200.0)


@pytest.mark.parametrize("status", ["spec-mismatch", "none"])
def test_unapproved_or_missing_matches_stay_unpriced(status):
    (row,) = price([boq(20)], [match(20, "11.64.1" if status != "none" else "", status)])
    assert (row.rate_source, row.amount, row.flags) == ("unpriced", 0.0, [UNPRICED])


def test_ai_researched_rate_and_guess():
    (row,) = price([boq(11)], [], ai_rates={("03 Boq", 11): 631.25}, ai_guesses={("03 Boq", 11): 700.0})
    assert (row.rate_source, row.net_rate, row.ai_guess_rate, row.amount) == ("ai-researched", 631.25, 700.0, 0.0)


def test_totals_by_source_and_basis():
    items = price(
        [boq(124), boq(10), boq(5, rate=120.0, amount=1200.0), boq(7)],
        [match(124, "11.64.1"), match(10, "11.64.1", "ai-proposed")],
    )
    result = totals(items, 18.0)
    assert (result["source:tender"], result["source:hsr"], result["source:hsr-proposed"], result["source:unpriced"]) == (1200.0, 446.5, 446.5, 0.0)
    assert (result["validation"], result["demo"], result["validation_incl_gst"]) == (1646.5, 2093.0, 1942.87)


@pytest.mark.parametrize("settings", [{"rate_basis": "market"}, {"gst_pct": 120}, {"escalation": 0}, {"labour_cess_pct": -1}])
def test_invalid_rate_settings_are_rejected(settings):
    with pytest.raises(ValueError):
        ProjectConfig(**settings)


def test_rate_settings_from_form_values():
    config = config_from_dict({"gst_pct": "12", "gst_confirmed": "true", "escalation": "1.05", "rate_basis": "demo"})
    assert (config.gst_pct, config.gst_confirmed, config.escalation, config.rate_basis) == (12.0, True, 1.05, "demo")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_pricing.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'buildflow.pricing'`

- [ ] **Step 3: Extend `buildflow/models.py`**

Add these fields at the end of `BOQItem` (after `flags`):

```python
    rate_source: str = "unpriced"
    hsr_code: str = ""
    match_status: str = ""
    unit_factor: float = 1.0
    multiplier: float = 1.0
    net_rate: float = 0.0
    priced_amount: float = 0.0
    ai_guess_rate: float | None = None
```

Add these fields at the end of `ProjectConfig` (after `data_provenance`):

```python
    rate_basis: str = "validation"
    gst_pct: float = 18.0
    gst_confirmed: bool = False
    escalation: float = 1.0
    profit_overhead_pct: float = 15.0
    labour_cess_pct: float = 1.0
    rate_matches_path: str = ""
    ai_components_path: str = ""
    ai_guesses_path: str = ""
```

Append to the end of `ProjectConfig.__post_init__`:

```python
        if self.rate_basis not in {"validation", "demo"}:
            raise ValueError(f"Unsupported rate basis: {self.rate_basis}")
        if not all(0 <= value <= 100 for value in (self.gst_pct, self.profit_overhead_pct, self.labour_cess_pct)):
            raise ValueError("GST, profit/overhead and labour cess percentages must be between 0 and 100")
        if self.escalation <= 0:
            raise ValueError("Escalation factor must be greater than zero")
```

In `config_from_dict`, extend the float keys and the boolean handling:

```python
    for key in ("indirect_cost_pct", "retention_pct", "crew_multiplier", "gst_pct", "escalation", "profit_overhead_pct", "labour_cess_pct"):
        if key in cleaned and cleaned[key] not in (None, ""):
            cleaned[key] = float(cleaned[key])
    for key in ("use_llm_fallback", "gst_confirmed"):
        if key in cleaned:
            value = cleaned[key]
            cleaned[key] = value is True or str(value).lower() in {"1", "true", "yes", "on"}
```

(replacing the existing float loop and the existing `use_llm_fallback` block). Add these fields at the end of `AnalysisResult` (after `job_id`):

```python
    rate_matches: list[RateMatch] = field(default_factory=list)
    rate_totals: dict[str, float] = field(default_factory=dict)
    rate_rows: list[dict[str, Any]] = field(default_factory=list)
    rate_components: list[Any] = field(default_factory=list)
```

- [ ] **Step 4: Create `buildflow/pricing.py`**

```python
# buildflow/pricing.py
"""Rate-source precedence, net rates, rate basis and totals (spec §7)."""
from __future__ import annotations

from .models import BOQItem, ProjectConfig, RateMatch
from .rate_table import RateTable
from .rate_units import unit_factor

GST_EXCL_FACTOR = 0.893  # HSR 2021 rates include 12% GST; A&C slip No. 19 reads them × 0.893 to exclude it
RATE_SOURCES = ("tender", "hsr", "hsr-proposed", "ai-researched", "unpriced")
BASIS_SOURCES = {"validation": ("tender", "hsr"), "demo": ("tender", "hsr", "hsr-proposed", "ai-researched")}
_STATUS_SOURCE = {"verbatim": "hsr", "ai-confirmed": "hsr", "spec-approved": "hsr", "ai-proposed": "hsr-proposed"}
_UNPRICED_FLAG = "Unpriced item; excluded from monetary totals"


def hsr_net_rate(through: float, factor: float, multiplier: float, escalation: float) -> float:
    return through * factor * multiplier * GST_EXCL_FACTOR * escalation


def price_items(
    items: list[BOQItem],
    matches: dict[tuple[str, int], RateMatch],
    table: RateTable | None,
    config: ProjectConfig,
    ai_rates: dict[tuple[str, int], float] | None = None,
    ai_guesses: dict[tuple[str, int], float] | None = None,
) -> None:
    basis = BASIS_SOURCES[config.rate_basis]
    ai_rates, ai_guesses = ai_rates or {}, ai_guesses or {}
    for item in items:
        key = (item.source_sheet, item.source_row)
        match = matches.get(key)
        if match:
            item.match_status, item.hsr_code, item.multiplier = match.status, match.hsr_code, match.multiplier
        if item.rate > 0 or item.amount > 0:
            item.rate_source = "tender"
            item.net_rate = item.rate if item.rate > 0 else item.amount / item.quantity
            item.priced_amount = item.amount if item.amount > 0 else item.quantity * item.rate
        else:
            source = _STATUS_SOURCE.get(match.status) if match and match.hsr_code and table else None
            hsr = table.items.get(match.hsr_code) if source else None
            factor = unit_factor(item.unit, hsr.unit_raw) if hsr and hsr.priced else None
            if factor is not None:
                item.rate_source, item.unit_factor = source, factor
                item.net_rate = hsr_net_rate(hsr.through, factor, match.multiplier, config.escalation)
            elif key in ai_rates:
                item.rate_source, item.net_rate = "ai-researched", ai_rates[key]
            else:
                item.rate_source, item.net_rate = "unpriced", 0.0
            item.priced_amount = item.quantity * item.net_rate
        item.ai_guess_rate = ai_guesses.get(key)
        in_basis = item.rate_source in basis
        item.rate = item.net_rate if in_basis else 0.0
        item.amount = item.priced_amount if in_basis else 0.0
        item.flags = [flag for flag in item.flags if flag != _UNPRICED_FLAG]
        if item.rate_source == "unpriced":
            item.flags.append(_UNPRICED_FLAG)
        elif not in_basis:
            item.flags.append(f"Priced as {item.rate_source}; outside the {config.rate_basis} basis, so excluded from totals")


def totals(items: list[BOQItem], gst_pct: float) -> dict[str, float]:
    by_source = dict.fromkeys(RATE_SOURCES, 0.0)
    for item in items:
        by_source[item.rate_source] += item.priced_amount
    validation = sum(by_source[source] for source in BASIS_SOURCES["validation"])
    demo = sum(by_source[source] for source in BASIS_SOURCES["demo"])
    factor = 1 + gst_pct / 100
    result = {f"source:{source}": round(value, 2) for source, value in by_source.items()}
    result.update(validation=round(validation, 2), demo=round(demo, 2),
                  validation_incl_gst=round(validation * factor, 2), demo_incl_gst=round(demo * factor, 2))
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_pricing.py -q`
Expected: PASS (15 passed). Then `python3 -m pytest -q` → all tests pass (existing tests are unaffected: no code calls `price_items` yet).

- [ ] **Step 6: Commit**

```bash
git add buildflow/models.py buildflow/pricing.py tests/test_pricing.py
git commit -m "feat(rates): price rows by rate source with GST-exclusive HSR rates" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 10: Agent wiring, rate findings and CLI

**Files:**
- Modify: `buildflow/validation.py` (add `RATE_FINDING_CODES`, `rate_findings`; basis wording in `UNPRICED_ITEMS`)
- Modify: `buildflow/agent.py` (match and price before scheduling; keep rate findings on replan)
- Modify: `buildflow/cli.py` (rate flags; write frozen matches)
- Modify: `README.md`, `docs/DESIGN.md` (§17)
- Test: `tests/test_rate_agent.py`

**Interfaces:**
- Consumes: `load_rate_table` (Task 5); `match_items`, `read_rate_matches`, `write_rate_matches` (Task 7); `match_rate_items` (Task 8); `price_items`, `totals` (Task 9).
- Produces:
  - `rate_findings(items, config, totals, *, table_error="", match_error="", rejected=None, stale=None, import_rejected=None) -> list[Finding]` and `RATE_FINDING_CODES: frozenset[str]` in `validation.py`.
  - Trace steps `match_rates` and `price_boq`; `AnalysisResult.rate_matches`, `rate_totals`, `rate_rows` (one dict per HSR code used: `code, description, unit, labour, machinery, material, through, page, source`), and `metrics["rate_totals"]`.
  - CLI flags `--rate-basis`, `--gst-pct`, `--escalation`, `--rate-matches`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rate_agent.py
from __future__ import annotations

import csv
import sys

import pytest

import buildflow.rate_table as rate_table_module
from buildflow import llm
from buildflow.agent import ProjectAgent
from buildflow.cli import main as cli_main
from buildflow.models import BOQItem, ProjectConfig
from buildflow.validation import rate_findings

ROW_124 = ("FINISHING > Distempering with oil bound washable distemper of approved brand and manufacture to give an even "
           "shade : Approved make : Asian, Nerolac, Berger, Dulux, Birla Opus > New work (two or more coats) over and "
           "including water thinnable priming coat with cement primer")
UNPRICED = "Unpriced item; excluded from monetary totals"


def items() -> list[BOQItem]:
    return [
        BOQItem("BOQ-124", ROW_124, "Sqm", 10.0, 0.0, 0.0, source_row=124, source_sheet="03 Boq", flags=[UNPRICED]),
        BOQItem("BOQ-005", "Tender-priced earthwork in excavation", "Cum", 100.0, 250.0, 25000.0, source_row=5, source_sheet="03 Boq"),
        BOQItem("BOQ-084", "Crystalline waterproofing to tank walls", "Sqm", 50.0, 0.0, 0.0, source_row=84, source_sheet="03 Boq", flags=[UNPRICED]),
    ]


def config(**overrides) -> ProjectConfig:
    return ProjectConfig(name="rate test", typology="stp_tank", start_date="2026-04-25", contract_duration_days=150, **overrides)


def row(result, number: int) -> BOQItem:
    return next(item for item in result.items if item.source_row == number)


def test_verbatim_row_is_priced_from_hsr_before_scheduling(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = ProjectAgent().run(items(), config())
    assert (row(result, 124).rate_source, row(result, 124).hsr_code, round(row(result, 124).amount, 2)) == ("hsr", "11.64.1", 446.5)
    assert (row(result, 5).rate_source, row(result, 84).rate_source) == ("tender", "unpriced")
    assert result.rate_totals["validation"] == 25446.5
    assert {event.tool: event.status for event in result.trace}["price_boq"] == "completed"
    assert result.rate_rows[0]["code"] == "11.64.1"
    codes = {finding.code for finding in result.findings}
    assert {"GST_ASSUMED", "RATE_GAPS", "RATE_BASIS"} <= codes
    assert "COST_RECONCILIATION" not in codes
    assert sum(activity.cost for activity in result.activities) == pytest.approx(25446.5, abs=1)


def test_ai_failure_during_matching_keeps_verbatim_matches(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(llm, "_post_json", lambda url, headers, payload: (500, {}, {"error": "upstream down"}))
    result = ProjectAgent().run(items(), config(use_llm_fallback=True))
    assert "RATE_MATCH_FAILED" in {finding.code for finding in result.findings}
    assert row(result, 124).rate_source == "hsr"


def test_missing_rate_table_is_a_finding_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(rate_table_module, "DEFAULT_DIR", tmp_path)
    result = ProjectAgent().run(items(), config())
    assert "RATE_TABLE_UNAVAILABLE" in {finding.code for finding in result.findings}
    assert (row(result, 124).rate_source, row(result, 5).rate_source) == ("unpriced", "tender")


def test_rate_findings_survive_replanning():
    result = ProjectAgent().run(items(), config())
    replanned = ProjectAgent().replan(result, [{"id": "MOB", "duration_days": 4}])
    assert "GST_ASSUMED" in {finding.code for finding in replanned.findings}


def test_rate_findings_report_pending_reviews_and_guess_deviation():
    proposed = BOQItem("A", "x", "Sqm", 1.0, 0.0, 0.0)
    proposed.rate_source, proposed.net_rate, proposed.priced_amount = "hsr-proposed", 100.0, 100.0
    guessed = BOQItem("B", "y", "Sqm", 1.0, 0.0, 0.0)
    guessed.rate_source, guessed.net_rate, guessed.priced_amount, guessed.ai_guess_rate = "hsr", 100.0, 100.0, 140.0
    findings = rate_findings([proposed, guessed], ProjectConfig(gst_confirmed=True), {"source:hsr": 100.0, "source:hsr-proposed": 100.0})
    assert [finding.code for finding in findings] == ["RATE_REVIEW_PENDING", "RATE_BASIS", "AI_GUESS_DEVIATION"]


def test_cli_freezes_matches_to_csv(tmp_path, monkeypatch):
    boq = tmp_path / "boq.csv"
    boq.write_text(f'Item,Description,Unit,Quantity,Rate,Amount\n1,"{ROW_124}",Sqm,10,,\n', encoding="utf-8")
    matches = tmp_path / "rate_matches.csv"
    monkeypatch.setattr(sys, "argv", ["buildflow", str(boq), "--typology", "stp_tank", "--rate-matches", str(matches),
                                      "--output", str(tmp_path / "out.xlsx")])
    cli_main()
    with matches.open(encoding="utf-8") as handle:
        assert [(r["source_row"], r["hsr_code"], r["status"]) for r in csv.DictReader(handle)] == [("2", "11.64.1", "verbatim")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_rate_agent.py -q`
Expected: FAIL with `ImportError: cannot import name 'rate_findings' from 'buildflow.validation'`

- [ ] **Step 3: Add rate findings to `buildflow/validation.py`**

Change the existing `UNPRICED_ITEMS` message line to:

```python
                f"{len(unpriced)} BOQ rows have no rate in the {config.rate_basis} basis, so the cash-flow total is incomplete.",
```

Append at the end of the module:

```python
RATE_FINDING_CODES = frozenset({
    "RATE_TABLE_UNAVAILABLE", "RATE_MATCH_FAILED", "RATE_MATCHES_REJECTED", "RATE_MATCHES_STALE", "AI_IMPORT_REJECTED",
    "RATE_REVIEW_PENDING", "RATE_GAPS", "RATE_BASIS", "GST_ASSUMED", "AI_GUESS_DEVIATION",
})


def rate_findings(
    items: list[BOQItem],
    config: ProjectConfig,
    totals: dict[str, float],
    *,
    table_error: str = "",
    match_error: str = "",
    rejected: list[str] | None = None,
    stale: list[str] | None = None,
    import_rejected: list[str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    if table_error:
        findings.append(Finding("warning", "RATE_TABLE_UNAVAILABLE", table_error, "Run tools/build_hsr_table.py or restore data/rates; rows keep tender rates only"))
    if match_error:
        findings.append(Finding("warning", "RATE_MATCH_FAILED", match_error, "Only verbatim and frozen matches were applied; retry when the AI provider is available"))
    if rejected:
        findings.append(Finding("warning", "RATE_MATCHES_REJECTED", f"{len(rejected)} match line(s) rejected: " + "; ".join(rejected[:5]), "Fix the listed lines; rejected lines are ignored"))
    if stale:
        findings.append(Finding("info", "RATE_MATCHES_STALE", f"{len(stale)} frozen match(es) ignored because the BOQ text changed: " + "; ".join(stale[:5]), "Review and re-freeze these rows"))
    if import_rejected:
        findings.append(Finding("warning", "AI_IMPORT_REJECTED", f"{len(import_rejected)} AI rate line(s) rejected: " + "; ".join(import_rejected[:5]), "Correct the listed lines in the AI CSV"))
    proposed = [item for item in items if item.rate_source == "hsr-proposed"]
    if proposed:
        findings.append(Finding("warning", "RATE_REVIEW_PENDING", f"{len(proposed)} row(s) are priced from AI-proposed HSR matches awaiting review.", "Set status to ai-confirmed or none in rate_matches.csv"))
    gaps = [item for item in items if item.rate_source == "unpriced"]
    if gaps:
        findings.append(Finding("info", "RATE_GAPS", f"{len(gaps)} row(s) have no tender, HSR or AI-researched rate.", "Export rate_gaps.csv and price them with prompts/rate_analysis.md"))
    priced = {key[len("source:"):]: value for key, value in totals.items() if key.startswith("source:") and value > 0}
    if priced:
        whole = sum(priced.values())
        shares = ", ".join(f"{source} {100 * value / whole:.0f}%" for source, value in priced.items())
        findings.append(Finding("info", "RATE_BASIS", f"Priced value by source: {shares}; CPM cost and cash flow use the {config.rate_basis} basis.", "Validation results use tender and HSR rates only"))
    if not config.gst_confirmed:
        findings.append(Finding("info", "GST_ASSUMED", f"GST is assumed at {config.gst_pct:g}% on totals.", "Confirm the tender's GST rate and pass --gst-pct"))
    deviating = [item for item in items if item.ai_guess_rate is not None and item.net_rate > 0 and abs(item.ai_guess_rate / item.net_rate - 1) > 0.30]
    if deviating:
        findings.append(Finding("info", "AI_GUESS_DEVIATION", f"{len(deviating)} AI guess(es) differ from the priced rate by more than 30%: " + ", ".join(item.id for item in deviating[:10]), "Treat AI guesses as comparison only"))
    return findings
```

- [ ] **Step 4: Wire matching and pricing into `buildflow/agent.py`**

Replace the import block at the top with:

```python
from __future__ import annotations

import copy
import uuid
from collections import Counter
from pathlib import Path

from .cashflow import compare_cashflows, independent_phase_cashflow, schedule_cashflow
from .llm import GROQ, LLMClassificationError, classify_ambiguous_items, match_rate_items
from .models import AnalysisResult, BOQItem, Finding, ProjectConfig, TraceEvent
from .pricing import price_items, totals
from .rate_matching import MatchReport, match_items, read_rate_matches
from .rate_table import load_rate_table
from .scheduling import apply_duration_overrides, build_activities
from .taxonomy import classify_items, infer_typology
from .validation import RATE_FINDING_CODES, rate_findings, validate
```

In `ProjectAgent.run`, insert this block immediately before `activities = build_activities(items, typology, config)`:

```python
        rate_table, table_error = None, ""
        try:
            rate_table = load_rate_table()
        except (FileNotFoundError, ValueError) as exc:
            table_error = str(exc)
        matches, rejected, stale, match_error = {}, [], [], ""
        if rate_table:
            frozen = {}
            if config.rate_matches_path and Path(config.rate_matches_path).exists():
                frozen, rejected, stale = read_rate_matches(config.rate_matches_path, items, rate_table)
            picker = (lambda requests: match_rate_items(requests, config.llm_model)) if config.use_llm_fallback else None
            try:
                matches, report = match_items(items, rate_table, frozen, picker, config.llm_model if picker else "")
            except LLMClassificationError as exc:
                match_error, picker = str(exc), None
                matches, report = match_items(items, rate_table, frozen, None)
            rejected += report.rejected_picks
            self._trace(
                trace, "match_rates", "completed",
                f"Matched BOQ rows to HSR 2021: {report.verbatim} verbatim, {report.frozen} frozen, {report.groq} AI-picked, {report.unmatched} unmatched",
                {"verbatim": report.verbatim, "frozen": report.frozen, "groq": report.groq, "unmatched": report.unmatched,
                 "provider": GROQ.name if picker else "", "model": config.llm_model if picker else ""},
            )
        else:
            self._trace(trace, "match_rates", "skipped", "HSR rate table unavailable; rows keep tender rates only", {"reason": table_error})
        price_items(items, matches, rate_table, config)
        rate_totals = totals(items, config.gst_pct)
        self._trace(trace, "price_boq", "completed", f"Priced BOQ rows on the {config.rate_basis} basis", rate_totals)
        used_codes = sorted({item.hsr_code for item in items if item.rate_source in {"hsr", "hsr-proposed"}})
        rate_rows = [
            {"code": code, "description": entry.full_description, "unit": entry.unit_raw, "labour": entry.labour,
             "machinery": entry.machinery, "material": entry.material, "through": entry.through, "page": entry.page,
             "source": entry.source}
            for code in used_codes
            for entry in [rate_table.items[code]]
        ] if rate_table else []
```

Directly after the existing line `findings = validate(items, activities, typology, config)`, add:

```python
        findings.extend(rate_findings(items, config, rate_totals, table_error=table_error, match_error=match_error,
                                      rejected=rejected, stale=stale))
```

Add `"rate_totals": rate_totals,` to the `metrics` dict; add this entry to the `assumptions.extend([...])` list:

```python
                f"Rates: HSR 2021 through rates × 0.893 (GST excluded) × escalation {config.escalation:g}; GST {config.gst_pct:g}% applied once on totals; tender rates are taken as ex-GST.",
```

and pass `rate_matches=list(matches.values()), rate_totals=rate_totals, rate_rows=rate_rows` to the `AnalysisResult(...)` constructor.

In `ProjectAgent.replan`, replace `updated.findings = validate(updated.items, updated.activities, updated.typology, updated.project)` with:

```python
        updated.findings = validate(updated.items, updated.activities, updated.typology, updated.project) + [
            finding for finding in result.findings if finding.code in RATE_FINDING_CODES
        ]
```

(`MatchReport` is imported for type readers; no other use is required.)

- [ ] **Step 5: Add the CLI flags to `buildflow/cli.py`**

Add after the `--llm-model` argument:

```python
    parser.add_argument("--rate-basis", choices=["validation", "demo"], default="validation",
                        help="Rate sources feeding CPM cost and cash flow (validation: tender + HSR)")
    parser.add_argument("--gst-pct", type=float, help="Tender GST rate applied once on totals (marks GST as confirmed)")
    parser.add_argument("--escalation", type=float, default=1.0, help="Multiplier from HSR 2021 prices to the project date")
    parser.add_argument("--rate-matches", help="CSV of frozen HSR matches: read before matching and rewritten afterwards")
```

Add these keyword arguments to the `ProjectConfig(...)` call:

```python
        rate_basis=args.rate_basis,
        escalation=args.escalation,
        rate_matches_path=args.rate_matches or "",
        **({"gst_pct": args.gst_pct, "gst_confirmed": True} if args.gst_pct is not None else {}),
```

After `result = ProjectAgent().run(...)`, add:

```python
    if args.rate_matches:
        write_rate_matches(args.rate_matches, result.rate_matches)
```

and import it at the top: `from .rate_matching import write_rate_matches`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_rate_agent.py -q`
Expected: PASS (6 passed). Then `python3 -m pytest -q` → all tests pass (sample BOQs are fully tender-priced, so their amounts are unchanged).

- [ ] **Step 7: Document the settings**

In `README.md`, insert this section immediately before `## Expected BOQ columns`:

````markdown
## Rates from HSR 2021

Unpriced BOQ rows are priced from the official Haryana PWD Schedule of Rates 2021, extracted into `data/rates/`
(provenance in `data/rates/SOURCE.md`). Rows that copy HSR wording are matched automatically; with the AI fallback
enabled, Groq picks among up to 8 HSR candidates for the rest. HSR rates include 12% GST, so they are multiplied by
0.893, and the tender's GST is added once on totals.

```bash
python3 -m buildflow.cli BOQ.xlsx --typology stp_tank --rate-matches rate_matches.csv --gst-pct 18 --escalation 1.0
```

Review AI matches by editing `rate_matches.csv` (`status` → `ai-confirmed`, `spec-approved` or `none`; `multiplier`
for extra-lift depth bands) and re-run. Validation totals use tender and HSR rates only; `--rate-basis demo` also counts
AI-proposed and AI-researched rates.

````

In `docs/DESIGN.md` §17, insert after the `crew_multiplier` row:

```markdown
| `rate_basis` | `validation` | Rate sources feeding CPM cost and cash flow (`validation` or `demo`) |
| `gst_pct` / `gst_confirmed` | `18.0` / `false` | GST applied once on totals; `GST_ASSUMED` until confirmed |
| `escalation` | `1.0` | Multiplier from HSR 2021 prices to the project date |
| `profit_overhead_pct` / `labour_cess_pct` | `15.0` / `1.0` | Tier-B rate-analysis markups |
```

- [ ] **Step 8: Commit**

```bash
git add buildflow/validation.py buildflow/agent.py buildflow/cli.py tests/test_rate_agent.py README.md docs/DESIGN.md
git commit -m "feat(rates): match and price BOQ rows before scheduling" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 11: Formula-driven rate sheets in the Excel export

**Files:**
- Modify: `buildflow/exporter.py`
- Test: `tests/test_export_formulas.py`

**Interfaces:**
- Consumes: `AnalysisResult.rate_rows`, `rate_totals`, `rate_matches`, item rate fields (Tasks 9–10); `BASIS_SOURCES`, `GST_EXCL_FACTOR`, `RATE_SOURCES` (Task 9); `MATCH_HEADER` (Task 7).
- Produces: sheets `Rate Assumptions` (named cells `GST_EXCL_FACTOR`, `GST_PCT`, `ESCALATION`, `PROFIT_OVERHEAD_PCT`, `LABOUR_CESS_PCT` in `B5:B9`), `Rates` (HSR items used; Through in column G), `Rate Totals`, `Rate Matches`; `Priced BOQ` columns A–U where E (rate) and F (amount) are formulas and M–U are `HSR code, Match status, Rate source, Unit factor, Multiplier, In basis, Basis amount, AI guess, Deviation`. Helper `_priced_boq(workbook, result, rate_row: dict[str, int], analysis_row: dict[str, int])` — Task 12 passes `analysis_row` for AI-researched rows.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_export_formulas.py
from __future__ import annotations

import shutil
import subprocess
from io import BytesIO

import pytest
from openpyxl import load_workbook

from buildflow.agent import ProjectAgent
from buildflow.exporter import export_xlsx
from buildflow.models import BOQItem, ProjectConfig, RateMatch
from buildflow.pricing import RATE_SOURCES
from buildflow.rate_matching import description_sha1, write_rate_matches

ROW_124 = ("FINISHING > Distempering with oil bound washable distemper of approved brand and manufacture to give an even "
           "shade : Approved make : Asian, Nerolac, Berger, Dulux, Birla Opus > New work (two or more coats) over and "
           "including water thinnable priming coat with cement primer")
PLASTER_ROW = "Plastering to plant room walls, 18-20 mm"
UNPRICED = "Unpriced item; excluded from monetary totals"


def items() -> list[BOQItem]:
    return [
        BOQItem("BOQ-124", ROW_124, "Sqm", 10.0, 0.0, 0.0, source_row=124, source_sheet="03 Boq", flags=[UNPRICED]),
        BOQItem("BOQ-005", "Tender-priced earthwork in excavation", "Cum", 100.0, 250.0, 25000.0, source_row=5, source_sheet="03 Boq"),
        BOQItem("BOQ-121", PLASTER_ROW, "Sqm", 20.0, 0.0, 0.0, source_row=121, source_sheet="03 Boq", flags=[UNPRICED]),
        BOQItem("BOQ-084", "Crystalline waterproofing to tank walls", "Sqm", 50.0, 0.0, 0.0, source_row=84, source_sheet="03 Boq", flags=[UNPRICED]),
    ]


def analysed(tmp_path):
    matches = tmp_path / "rate_matches.csv"
    write_rate_matches(matches, [RateMatch("03 Boq", 121, description_sha1(PLASTER_ROW), PLASTER_ROW, "Sqm", "11.10.1", 1.0,
                                           "ai-proposed", "groq", "", "openai/gpt-oss-120b", "2026-09-11", "plaster")])
    config = ProjectConfig(name="formula test", typology="stp_tank", start_date="2026-04-25", contract_duration_days=150,
                           rate_matches_path=str(matches))
    return ProjectAgent().run(items(), config)


def test_named_cells_and_formulas_are_written(tmp_path):
    result = analysed(tmp_path)
    workbook = load_workbook(BytesIO(export_xlsx(result)))
    assert {"GST_EXCL_FACTOR", "GST_PCT", "ESCALATION", "PROFIT_OVERHEAD_PCT", "LABOUR_CESS_PCT"} <= set(workbook.defined_names)
    assert {"Rates", "Rate Totals", "Rate Matches", "Rate Assumptions"} <= set(workbook.sheetnames)
    sheet = workbook["Priced BOQ"]
    assert sheet["E5"].value.startswith("=Rates!$G$") and sheet["F5"].value == "=D5*E5"
    assert (sheet["E6"].value, sheet["O6"].value, sheet["R7"].value) == (250.0, "tender", "No")


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
def test_formulas_recalculate_to_python_values(tmp_path):
    result = analysed(tmp_path)
    source = tmp_path / "in.xlsx"
    source.write_bytes(export_xlsx(result))
    profile = (tmp_path / "lo-profile").as_uri()
    subprocess.run([shutil.which("soffice"), f"-env:UserInstallation={profile}", "--headless", "--calc", "--convert-to",
                    "xlsx", "--outdir", str(tmp_path / "out"), str(source)], check=True, capture_output=True, timeout=180)
    workbook = load_workbook(tmp_path / "out" / "in.xlsx", data_only=True)
    sheet = workbook["Priced BOQ"]
    for offset, item in enumerate(result.items):
        row = 5 + offset
        assert sheet.cell(row, 5).value == pytest.approx(item.net_rate)
        assert sheet.cell(row, 6).value == pytest.approx(item.priced_amount)
        assert sheet.cell(row, 19).value == pytest.approx(item.amount)
    totals_sheet = workbook["Rate Totals"]
    got = {totals_sheet.cell(row, 1).value: totals_sheet.cell(row, 2).value for row in range(5, totals_sheet.max_row + 1)}
    for source_name in RATE_SOURCES:
        assert got[source_name] == pytest.approx(result.rate_totals[f"source:{source_name}"], abs=0.01)
    for key in ("validation", "demo", "validation_incl_gst", "demo_incl_gst"):
        assert got[key] == pytest.approx(result.rate_totals[key], abs=0.01)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_export_formulas.py -q`
Expected: FAIL with `KeyError` or assertion errors (no `Rate Assumptions` sheet / named cells yet)

- [ ] **Step 3: Add the rate-sheet helpers to `buildflow/exporter.py`**

Add these imports at the top (after the existing openpyxl imports):

```python
from openpyxl.workbook.defined_name import DefinedName

from .pricing import BASIS_SOURCES, GST_EXCL_FACTOR, RATE_SOURCES
from .rate_matching import MATCH_HEADER
```

Add these definitions directly above `def export_xlsx`:

```python
PRICED_HEADERS = ["ID", "Description", "Unit", "Quantity", "Rate", "Amount", "Work package", "Track", "Confidence",
                  "Classifier", "Evidence", "Flags", "HSR code", "Match status", "Rate source", "Unit factor",
                  "Multiplier", "In basis", "Basis amount", "AI guess", "Deviation"]


def _rate_assumptions(workbook, result: AnalysisResult) -> None:
    ws = workbook.create_sheet("Rate Assumptions")
    _title(ws, "Rate assumptions", "Edit a value here and every rate, amount and total recalculates")
    project = result.project
    rows = [
        ["GST_EXCL_FACTOR", GST_EXCL_FACTOR, "HSR 2021 rates include 12% GST; A&C slip No. 19 reads them × 0.893"],
        ["GST_PCT", project.gst_pct, "Tender GST applied once on totals" + ("" if project.gst_confirmed else " (assumed)")],
        ["ESCALATION", project.escalation, "Multiplier from HSR 2021 prices to the project date"],
        ["PROFIT_OVERHEAD_PCT", project.profit_overhead_pct, "Tier-B markup (CPWD convention; HSR publishes none)"],
        ["LABOUR_CESS_PCT", project.labour_cess_pct, "Tier-B labour welfare cess"],
    ]
    _table(ws, 4, ["Name", "Value", "Basis"], rows)
    for offset, row in enumerate(rows):
        workbook.defined_names[row[0]] = DefinedName(row[0], attr_text=f"'Rate Assumptions'!$B${5 + offset}")


def _priced_boq(workbook, result: AnalysisResult, rate_row: dict[str, int], analysis_row: dict[str, int]) -> None:
    ws = workbook.create_sheet("Priced BOQ")
    _title(ws, "Priced BOQ and classification", "Rates and amounts are live formulas; every AI/retrieval decision is reviewable")
    basis = BASIS_SOURCES[result.project.rate_basis]
    rows = []
    for offset, item in enumerate(result.items):
        r = 5 + offset
        key = f"{item.source_sheet}:{item.source_row}"
        if item.rate_source in {"hsr", "hsr-proposed"} and item.hsr_code in rate_row:
            rate = f"=Rates!$G${rate_row[item.hsr_code]}*P{r}*Q{r}*GST_EXCL_FACTOR*ESCALATION"
        elif item.rate_source == "ai-researched" and key in analysis_row:
            rate = f"='Rate Analysis'!$C${analysis_row[key]}"
        else:
            rate = item.net_rate
        rows.append([
            item.id, item.description, item.unit, item.quantity, rate, f"=D{r}*E{r}", item.work_package, item.track,
            item.confidence, item.classifier, "; ".join(item.evidence), "; ".join(item.flags), item.hsr_code,
            item.match_status, item.rate_source, item.unit_factor, item.multiplier,
            "Yes" if item.rate_source in basis else "No", f'=IF(R{r}="Yes",F{r},0)',
            "" if item.ai_guess_rate is None else item.ai_guess_rate,
            f'=IF(AND(ISNUMBER(T{r}),E{r}<>0),T{r}/E{r}-1,"")',
        ])
    _table(ws, 4, PRICED_HEADERS, rows)
    _currency_cells(ws, [5, 6, 19, 20], 5, 4 + len(rows))
    for r in range(5, 5 + len(rows)):
        ws.cell(r, 9).number_format = "0%"
        ws.cell(r, 21).number_format = "0%"


def _rates_sheet(workbook, result: AnalysisResult) -> None:
    ws = workbook.create_sheet("Rates")
    _title(ws, "HSR 2021 rates used", "Through rates as printed (GST-inclusive); net rate = through × unit factor × multiplier × GST_EXCL_FACTOR × ESCALATION")
    rows = [[row["code"], row["description"], row["unit"], row["labour"], row["machinery"], row["material"], row["through"],
             row["page"], row["source"]] for row in result.rate_rows]
    _table(ws, 4, ["Code", "Description", "Unit", "Labour", "Machinery", "Material", "Through", "Page", "Source"], rows)
    _currency_cells(ws, [4, 5, 6, 7], 5, 4 + len(rows))


def _totals_sheet(workbook, result: AnalysisResult) -> None:
    ws = workbook.create_sheet("Rate Totals")
    _title(ws, "Totals by rate source", "Validation = tender + HSR; demo adds AI-proposed and AI-researched; AI guesses are never counted")
    last = 4 + len(result.items)
    amounts, sources = f"'Priced BOQ'!$F$5:$F${last}", f"'Priced BOQ'!$O$5:$O${last}"
    where = {name: 5 + offset for offset, name in enumerate(RATE_SOURCES)}
    rows = [[name, f"=SUMIFS({amounts},{sources},A{where[name]})"] for name in RATE_SOURCES]
    validation_row = 5 + len(RATE_SOURCES)
    rows += [
        ["validation", "=SUM(" + ",".join(f"B{where[name]}" for name in BASIS_SOURCES["validation"]) + ")"],
        ["demo", "=SUM(" + ",".join(f"B{where[name]}" for name in BASIS_SOURCES["demo"]) + ")"],
        ["validation_incl_gst", f"=B{validation_row}*(1+GST_PCT/100)"],
        ["demo_incl_gst", f"=B{validation_row + 1}*(1+GST_PCT/100)"],
    ]
    _table(ws, 4, ["Rate source / total", "Amount"], rows)
    _currency_cells(ws, [2], 5, 4 + len(rows))


def _matches_sheet(workbook, result: AnalysisResult) -> None:
    ws = workbook.create_sheet("Rate Matches")
    _title(ws, "HSR matches", "Mirror of rate_matches.csv — edit the CSV and re-run to change a match")
    _table(ws, 4, MATCH_HEADER, [[getattr(match, name) for name in MATCH_HEADER] for match in result.rate_matches])
```

- [ ] **Step 4: Use the helpers in `export_xlsx`**

Replace the existing Priced BOQ block — from `boq = workbook.create_sheet("Priced BOQ")` through the `for row in range(5, 5 + len(boq_rows)):` loop that sets `boq.cell(row, 9).number_format = "0%"` — with:

```python
    rate_row = {row["code"]: 5 + offset for offset, row in enumerate(result.rate_rows)}
    _priced_boq(workbook, result, rate_row, {})
    _rates_sheet(workbook, result)
    _totals_sheet(workbook, result)
    _matches_sheet(workbook, result)
```

and insert `_rate_assumptions(workbook, result)` immediately before `trace = workbook.create_sheet("Agent Trace")`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_export_formulas.py -q`
Expected: PASS (2 passed; the LibreOffice test is skipped only where `soffice` is missing). Then `python3 -m pytest -q` → all tests pass.

- [ ] **Step 6: Commit**

```bash
git add buildflow/exporter.py tests/test_export_formulas.py
git commit -m "feat(rates): export rates and amounts as live Excel formulas" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 12: AI pricing for gaps (tier B components, tier C guesses)

**Files:**
- Modify: `buildflow/models.py` (add `AIComponent` after `RateMatch`)
- Create: `buildflow/ai_rates.py`
- Modify: `buildflow/agent.py`, `buildflow/cli.py`, `buildflow/exporter.py`
- Test: `tests/test_ai_rates.py`

**Interfaces:**
- Consumes: `row_key`, `shortlist` (Tasks 6–7); `RateTable` (Task 5); `canonical_unit` (Task 1); `price_items` (Task 9, which already accepts `ai_rates`/`ai_guesses`); `rate_findings(import_rejected=...)` (Task 10); `_priced_boq(..., analysis_row)` (Task 11).
- Produces:
  - `AIComponent(source_sheet, source_row: int, component_type, description, qty_per_unit: float, unit, unit_price: float, price_source, source_date, citation_type, citation_ref, notes: str = "", escalate: bool = False)` in `models.py`.
  - In `ai_rates.py`: `GAP_HEADER`, `COMPONENT_HEADER`, `GUESS_HEADER`, `COMPONENT_TYPES`, `CITATION_TYPES`; `write_gaps(path, items, table, matches) -> int`; `read_components(path, items, table) -> tuple[list[AIComponent], list[str]]`; `component_rates(components, escalation, profit_overhead_pct, labour_cess_pct) -> dict[tuple[str, int], float]`; `read_guesses(path, items) -> tuple[dict[tuple[str, int], float], list[str]]`.
  - Exporter sheet `Rate Analysis`: component lines from row 5 (columns `Row, Type, Description, Qty per unit, Unit, Unit price, Price source, Escalate, Effective price, Line amount, Citation type, Citation`), then a summary table whose column C holds each row's rate formula. `_analysis_row_numbers(result) -> dict[str, int]` gives the summary row per `"sheet:row"` key.
  - CLI flags `--ai-components`, `--ai-guesses`, `--rate-gaps`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ai_rates.py
from __future__ import annotations

import csv
import shutil
import subprocess
from io import BytesIO

import pytest
from openpyxl import load_workbook

from buildflow.agent import ProjectAgent
from buildflow.ai_rates import COMPONENT_HEADER, GUESS_HEADER, component_rates, read_components, read_guesses, write_gaps
from buildflow.exporter import export_xlsx
from buildflow.models import AIComponent, BOQItem, ProjectConfig
from buildflow.rate_table import load_rate_table


def boq(row: int, unit: str = "Sqm") -> BOQItem:
    return BOQItem(f"BOQ-{row:03d}", "Crystalline waterproofing to tank walls", unit, 50.0, 0.0, 0.0,
                   source_row=row, source_sheet="03 Boq", flags=["Unpriced item; excluded from monetary totals"])


def write(path, header: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def component(**changes) -> dict:
    row = {"source_sheet": "03 Boq", "source_row": "84", "component_type": "material", "description": "Integral crystalline slurry",
           "qty_per_unit": "0.8", "unit": "kg", "unit_price": "238", "price_source": "B0139", "source_date": "",
           "citation_type": "datasheet", "citation_ref": "Manufacturer coverage 0.8 kg/sqm", "notes": ""}
    row.update(changes)
    return row


LABOUR = component(component_type="labour", description="Beldar", qty_per_unit="0.05", unit="day", unit_price="364",
                   price_source="LB008", citation_type="dar", citation_ref="DAR 2023 analogous item")


def test_hsr_priced_components_import_and_escalate(tmp_path):
    path = tmp_path / "components.csv"
    write(path, COMPONENT_HEADER, [component(), LABOUR])
    components, rejected = read_components(path, [boq(84)], load_rate_table())
    assert rejected == []
    assert [item.escalate for item in components] == [True, True]
    # (0.8 × 238 + 0.05 × 364) = 208.6; × 1.1 escalation = 229.46; × 1.15 = 263.879; × 1.01 cess = 266.51779
    assert component_rates(components, 1.1, 15.0, 1.0)[("03 Boq", 84)] == pytest.approx(266.51779)


def test_tier_b_marks_up_everything_except_carriage():
    lines = [AIComponent("03 Boq", 84, "material", "m", 2.0, "kg", 100.0, "https://example.com/m", "2026-09-11", "url", "x"),
             AIComponent("03 Boq", 84, "labour", "l", 0.5, "day", 600.0, "https://example.com/l", "2026-09-11", "url", "x"),
             AIComponent("03 Boq", 84, "carriage", "c", 1.0, "trip", 50.0, "https://example.com/c", "2026-09-11", "url", "x")]
    # (200 + 300) × 1.15 + 50 = 625; × 1.01 = 631.25 — URL prices are not escalated
    assert component_rates(lines, 1.1, 15.0, 1.0)[("03 Boq", 84)] == pytest.approx(631.25)


@pytest.mark.parametrize(
    "changes, reason",
    [({"source_row": "999"}, "unknown row key"),
     ({"component_type": "overhead"}, "component_type must be one of"),
     ({"unit_price": "0"}, "must be positive numbers"),
     ({"price_source": "B9999"}, "price_source must be an HSR basic code or a URL"),
     ({"price_source": "https://example.com/price", "source_date": ""}, "price_source must be an HSR basic code or a URL"),
     ({"citation_type": "blog"}, "citation_type must be one of"),
     ({"citation_ref": ""}, "citation_type must be one of")],
)
def test_invalid_component_lines_are_rejected(tmp_path, changes, reason):
    path = tmp_path / "components.csv"
    write(path, COMPONENT_HEADER, [component(**changes)])
    components, rejected = read_components(path, [boq(84)], load_rate_table())
    assert components == []
    assert len(rejected) == 1 and rejected[0].startswith("line 2: ") and reason in rejected[0]


def test_guesses_must_use_the_boq_unit(tmp_path):
    path = tmp_path / "guesses.csv"
    base = {"source_sheet": "03 Boq", "source_row": "84", "rate_ex_gst": "650", "model": "claude", "date": "2026-09-11", "notes": ""}
    write(path, GUESS_HEADER, [{**base, "unit": "Sqm"}, {**base, "unit": "Cum"}])
    guesses, rejected = read_guesses(path, [boq(84)])
    assert guesses == {("03 Boq", 84): 650.0}
    assert rejected == ["line 3: unit 'Cum' differs from the BOQ row's 'Sqm'"]


def test_gap_export_lists_unpriced_rows_with_nearest_items(tmp_path):
    gap, priced = boq(84), boq(85)
    gap.rate_source, priced.rate_source = "unpriced", "hsr"
    path = tmp_path / "gaps.csv"
    assert write_gaps(path, [gap, priced], load_rate_table(), {}) == 1
    with path.open(encoding="utf-8") as handle:
        (line,) = list(csv.DictReader(handle))
    assert (line["source_row"], line["unit"]) == ("84", "Sqm")
    assert line["nearest_hsr"].count(" | ") == 2


def analysed(tmp_path):
    components, guesses = tmp_path / "components.csv", tmp_path / "guesses.csv"
    write(components, COMPONENT_HEADER, [component(), LABOUR])
    write(guesses, GUESS_HEADER, [{"source_sheet": "03 Boq", "source_row": "84", "unit": "Sqm", "rate_ex_gst": "650",
                                   "model": "claude", "date": "2026-09-11", "notes": ""}])
    config = ProjectConfig(name="ai test", typology="stp_tank", start_date="2026-04-25", contract_duration_days=150,
                           rate_basis="demo", ai_components_path=str(components), ai_guesses_path=str(guesses))
    return ProjectAgent().run([boq(84)], config)


def test_agent_prices_ai_researched_rows_in_the_demo_basis(tmp_path):
    result = analysed(tmp_path)
    (row,) = result.items
    # 208.6 × 1.15 × 1.01 = 242.2889 per sqm at escalation 1.0
    assert (row.rate_source, row.ai_guess_rate) == ("ai-researched", 650.0)
    assert row.net_rate == pytest.approx(242.2889) and row.amount == pytest.approx(12114.445)
    assert "AI_GUESS_DEVIATION" in {finding.code for finding in result.findings}
    sheet = load_workbook(BytesIO(export_xlsx(result)))["Priced BOQ"]
    assert sheet["E5"].value.startswith("='Rate Analysis'!$C$")


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
def test_rate_analysis_formulas_recalculate_to_python_values(tmp_path):
    result = analysed(tmp_path)
    source = tmp_path / "in.xlsx"
    source.write_bytes(export_xlsx(result))
    profile = (tmp_path / "lo-profile").as_uri()
    subprocess.run([shutil.which("soffice"), f"-env:UserInstallation={profile}", "--headless", "--calc", "--convert-to",
                    "xlsx", "--outdir", str(tmp_path / "out"), str(source)], check=True, capture_output=True, timeout=180)
    sheet = load_workbook(tmp_path / "out" / "in.xlsx", data_only=True)["Priced BOQ"]
    assert sheet["E5"].value == pytest.approx(result.items[0].net_rate)
    assert sheet["F5"].value == pytest.approx(result.items[0].priced_amount)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_ai_rates.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'buildflow.ai_rates'`

- [ ] **Step 3: Add `AIComponent` to `buildflow/models.py` (directly after `RateMatch`)**

```python
@dataclass
class AIComponent:
    """One tier-B rate-analysis line imported from ai_rate_components.csv."""

    source_sheet: str
    source_row: int
    component_type: str
    description: str
    qty_per_unit: float
    unit: str
    unit_price: float
    price_source: str
    source_date: str
    citation_type: str
    citation_ref: str
    notes: str = ""
    escalate: bool = False
```

- [ ] **Step 4: Create `buildflow/ai_rates.py`**

```python
# buildflow/ai_rates.py
"""Tier-B and tier-C AI pricing files (spec §8): gap export, validated imports and rate-analysis arithmetic."""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path

from .models import AIComponent, BOQItem, RateMatch
from .rate_matching import row_key, shortlist
from .rate_table import RateTable
from .rate_units import canonical_unit

GAP_HEADER = ["source_sheet", "source_row", "full_description", "unit", "spec_differences", "nearest_hsr"]
COMPONENT_HEADER = ["source_sheet", "source_row", "component_type", "description", "qty_per_unit", "unit", "unit_price",
                    "price_source", "source_date", "citation_type", "citation_ref", "notes"]
GUESS_HEADER = ["source_sheet", "source_row", "unit", "rate_ex_gst", "model", "date", "notes"]
COMPONENT_TYPES = ("material", "labour", "plant", "carriage")
CITATION_TYPES = ("hsr", "dar", "is", "astm", "aci", "datasheet", "url")


def _rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _key(row: dict[str, str]) -> tuple[str, int] | None:
    try:
        return (row["source_sheet"].strip(), int(row["source_row"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _positive(text: str | None) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _iso_date(text: str | None) -> bool:
    try:
        date.fromisoformat((text or "").strip())
    except ValueError:
        return False
    return True


def _same_unit(left: str, right: str) -> bool:
    a, b = canonical_unit(left), canonical_unit(right)
    return a == b if a and b else left.strip().lower() == right.strip().lower()


def write_gaps(path: str | Path, items: list[BOQItem], table: RateTable, matches: dict[tuple[str, int], RateMatch]) -> int:
    rows = []
    for entry in items:
        if entry.rate_source != "unpriced":
            continue
        match = matches.get(row_key(entry))
        nearest = " | ".join(f"{candidate.code}: {table.items[candidate.code].full_description[:80]}"
                             for candidate in shortlist(entry.description, entry.unit, table, 3))
        rows.append({"source_sheet": entry.source_sheet, "source_row": entry.source_row, "full_description": entry.description,
                     "unit": entry.unit, "spec_differences": match.spec_differences if match else "", "nearest_hsr": nearest})
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=GAP_HEADER)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def read_components(path: str | Path, items: list[BOQItem], table: RateTable) -> tuple[list[AIComponent], list[str]]:
    keys = {row_key(entry) for entry in items}
    components: list[AIComponent] = []
    rejected: list[str] = []
    for line_number, row in enumerate(_rows(path), start=2):
        problems = []
        key = _key(row)
        if key is None or key not in keys:
            problems.append("unknown row key")
        kind = (row.get("component_type") or "").strip()
        if kind not in COMPONENT_TYPES:
            problems.append(f"component_type must be one of {', '.join(COMPONENT_TYPES)}")
        quantity, price = _positive(row.get("qty_per_unit")), _positive(row.get("unit_price"))
        if quantity is None or price is None:
            problems.append("qty_per_unit and unit_price must be positive numbers")
        source, when = (row.get("price_source") or "").strip(), (row.get("source_date") or "").strip()
        escalate = source in table.basic
        if not escalate and not (source.startswith(("http://", "https://")) and _iso_date(when)):
            problems.append("price_source must be an HSR basic code or a URL with an ISO source_date")
        citation, reference = (row.get("citation_type") or "").strip(), (row.get("citation_ref") or "").strip()
        if citation not in CITATION_TYPES or not reference:
            problems.append(f"citation_type must be one of {', '.join(CITATION_TYPES)}, with a citation_ref")
        if problems:
            rejected.append(f"line {line_number}: " + "; ".join(problems))
            continue
        components.append(AIComponent(key[0], key[1], kind, (row.get("description") or "").strip(), quantity,
                                      (row.get("unit") or "").strip(), price, source, when, citation, reference,
                                      (row.get("notes") or "").strip(), escalate))
    return components, rejected


def component_rates(
    components: list[AIComponent], escalation: float, profit_overhead_pct: float, labour_cess_pct: float
) -> dict[tuple[str, int], float]:
    """Rate ex-GST = (Σ marked-up lines × (1 + P&O) + Σ carriage) × (1 + cess); HSR basic prices are escalated."""
    marked: dict[tuple[str, int], float] = defaultdict(float)
    carriage: dict[tuple[str, int], float] = defaultdict(float)
    for line in components:
        amount = line.qty_per_unit * line.unit_price * (escalation if line.escalate else 1.0)
        (carriage if line.component_type == "carriage" else marked)[(line.source_sheet, line.source_row)] += amount
    return {
        key: (marked[key] * (1 + profit_overhead_pct / 100) + carriage[key]) * (1 + labour_cess_pct / 100)
        for key in set(marked) | set(carriage)
    }


def read_guesses(path: str | Path, items: list[BOQItem]) -> tuple[dict[tuple[str, int], float], list[str]]:
    by_key = {row_key(entry): entry for entry in items}
    guesses: dict[tuple[str, int], float] = {}
    rejected: list[str] = []
    for line_number, row in enumerate(_rows(path), start=2):
        problems = []
        key = _key(row)
        entry = by_key.get(key) if key else None
        unit = (row.get("unit") or "").strip()
        if entry is None:
            problems.append("unknown row key")
        elif not _same_unit(unit, entry.unit):
            problems.append(f"unit {unit!r} differs from the BOQ row's {entry.unit!r}")
        rate = _positive(row.get("rate_ex_gst"))
        if rate is None:
            problems.append("rate_ex_gst must be a positive number")
        if not (row.get("model") or "").strip() or not _iso_date(row.get("date")):
            problems.append("model and an ISO date are required")
        if problems:
            rejected.append(f"line {line_number}: " + "; ".join(problems))
            continue
        guesses[key] = rate
    return guesses, rejected
```

- [ ] **Step 5: Wire the imports into `buildflow/agent.py`**

Add `from .ai_rates import component_rates, read_components, read_guesses` to the imports. Replace the line `price_items(items, matches, rate_table, config)` (added in Task 10) with:

```python
        ai_rates, ai_guesses, import_rejected, components = {}, {}, [], []
        if config.ai_components_path and rate_table:
            components, bad = read_components(config.ai_components_path, items, rate_table)
            ai_rates = component_rates(components, config.escalation, config.profit_overhead_pct, config.labour_cess_pct)
            import_rejected += bad
        if config.ai_guesses_path:
            ai_guesses, bad = read_guesses(config.ai_guesses_path, items)
            import_rejected += bad
        price_items(items, matches, rate_table, config, ai_rates, ai_guesses)
```

Add `import_rejected=import_rejected` to the `rate_findings(...)` call and `rate_components=components` to the `AnalysisResult(...)` call.

- [ ] **Step 6: Add the Rate Analysis sheet to `buildflow/exporter.py`**

Add these definitions above `def export_xlsx`:

```python
ANALYSIS_HEADERS = ["Row", "Type", "Description", "Qty per unit", "Unit", "Unit price", "Price source", "Escalate",
                    "Effective price", "Line amount", "Citation type", "Citation"]


def _analysis_row_numbers(result: AnalysisResult) -> dict[str, int]:
    """Summary row (holding the rate formula in column C) for each 'sheet:row' key on the Rate Analysis sheet."""
    keys: list[str] = []
    for line in result.rate_components:
        key = f"{line.source_sheet}:{line.source_row}"
        if key not in keys:
            keys.append(key)
    first = 4 + len(result.rate_components) + 3
    return {key: first + offset for offset, key in enumerate(keys)}


def _analysis_sheet(workbook, result: AnalysisResult) -> None:
    ws = workbook.create_sheet("Rate Analysis")
    _title(ws, "Tier-B rate analysis (AI-researched)", "Every price is cited; HSR basic prices are escalated, web prices are taken at their source date")
    last = 4 + len(result.rate_components)
    rows = []
    for offset, line in enumerate(result.rate_components):
        r = 5 + offset
        rows.append([f"{line.source_sheet}:{line.source_row}", line.component_type, line.description, line.qty_per_unit,
                     line.unit, line.unit_price, line.price_source, 1 if line.escalate else 0,
                     f"=F{r}*IF(H{r}=1,ESCALATION,1)", f"=D{r}*I{r}", line.citation_type, line.citation_ref])
    _table(ws, 4, ANALYSIS_HEADERS, rows)
    amounts, keys, kinds = f"$J$5:$J${last}", f"$A$5:$A${last}", f"$B$5:$B${last}"
    summary = [
        [key, "rate (ex-GST)",
         f'=(SUMIFS({amounts},{keys},A{row},{kinds},"<>carriage")*(1+PROFIT_OVERHEAD_PCT/100)'
         f'+SUMIFS({amounts},{keys},A{row},{kinds},"carriage"))*(1+LABOUR_CESS_PCT/100)']
        for key, row in _analysis_row_numbers(result).items()
    ]
    _table(ws, last + 2, ["Row", "Label", "Rate (ex-GST)"], summary)
```

In `export_xlsx`, change `_priced_boq(workbook, result, rate_row, {})` to `_priced_boq(workbook, result, rate_row, _analysis_row_numbers(result))`, and add `_analysis_sheet(workbook, result)` after `_matches_sheet(workbook, result)`.

- [ ] **Step 7: Add the CLI flags to `buildflow/cli.py`**

Add after the `--rate-matches` argument:

```python
    parser.add_argument("--ai-components", help="Tier-B ai_rate_components.csv produced with prompts/rate_analysis.md")
    parser.add_argument("--ai-guesses", help="Tier-C ai_rate_guesses.csv (comparison only, never in totals)")
    parser.add_argument("--rate-gaps", help="Write rows without any rate to this CSV for tier-B pricing")
```

Add to the `ProjectConfig(...)` call: `ai_components_path=args.ai_components or "", ai_guesses_path=args.ai_guesses or "",`. After the `--rate-matches` write, add:

```python
    if args.rate_gaps:
        matches = {(match.source_sheet, match.source_row): match for match in result.rate_matches}
        count = write_gaps(args.rate_gaps, result.items, load_rate_table(), matches)
        print(f"Wrote {count} gap row(s) to {args.rate_gaps}")
```

with imports `from .ai_rates import write_gaps` and `from .rate_table import load_rate_table`.

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_ai_rates.py -q`
Expected: PASS (13 passed). Then `python3 -m pytest -q` → all tests pass.

- [ ] **Step 9: Commit**

```bash
git add buildflow/models.py buildflow/ai_rates.py buildflow/agent.py buildflow/cli.py buildflow/exporter.py tests/test_ai_rates.py
git commit -m "feat(rates): import cited AI rate analyses and guesses for gap rows" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 13: Prompt pack for AI pricing

**Files:**
- Create: `prompts/rate_analysis.md`, `prompts/rate_guess.md`
- Modify: `README.md` (add "AI pricing for gaps")
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: `COMPONENT_HEADER`, `GUESS_HEADER`, `read_components`, `read_guesses` (Task 12); committed basic rates B0139 and LB008 (Task 4).
- Produces: two prompt files, each ending in exactly one fenced `csv` example that the importers accept.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prompts.py
from __future__ import annotations

import re
from pathlib import Path

from buildflow.ai_rates import COMPONENT_HEADER, GUESS_HEADER, read_components, read_guesses
from buildflow.models import BOQItem
from buildflow.rate_table import load_rate_table

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
FENCE = "`" * 3  # built at runtime so this file contains no literal code fence


def example_csv(name: str) -> str:
    (block,) = re.findall(rf"{FENCE}csv\n(.*?){FENCE}", (PROMPTS / name).read_text(encoding="utf-8"), re.S)
    return block


def gap_row() -> BOQItem:
    return BOQItem("BOQ-084", "Crystalline waterproofing to tank walls", "Sqm", 50.0, 0.0, 0.0, source_row=84, source_sheet="03 Boq")


def test_rate_analysis_example_imports_cleanly(tmp_path):
    text = example_csv("rate_analysis.md")
    assert text.splitlines()[0] == ",".join(COMPONENT_HEADER)
    path = tmp_path / "components.csv"
    path.write_text(text, encoding="utf-8")
    components, rejected = read_components(path, [gap_row()], load_rate_table())
    assert (len(components), rejected) == (2, [])


def test_rate_guess_example_imports_cleanly(tmp_path):
    text = example_csv("rate_guess.md")
    assert text.splitlines()[0] == ",".join(GUESS_HEADER)
    path = tmp_path / "guesses.csv"
    path.write_text(text, encoding="utf-8")
    assert read_guesses(path, [gap_row()]) == ({("03 Boq", 84): 650.0}, [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_prompts.py -q`
Expected: FAIL with `FileNotFoundError` (no `prompts/rate_analysis.md`)

- [ ] **Step 3: Create `prompts/rate_analysis.md`**

````markdown
# Tier-B rate analysis prompt (BuildFlow)

Use this file as the system prompt or project instructions in Claude, ChatGPT or Gemini, or paste it into a Claude
Code session. Attach `rate_gaps.csv` (written by `buildflow --rate-gaps`) and `data/rates/hsr_2021_basic_rates.csv`.

## Role

You are a quantity surveyor preparing rate analyses for an Indian public-works tender at Karnal, Haryana. For each
BOQ row in `rate_gaps.csv`, list the materials, labour, plant and carriage needed for one unit of that row.

## Rules

1. Output only CSV with exactly this header and one line per component — no prose before or after:
   `source_sheet,source_row,component_type,description,qty_per_unit,unit,unit_price,price_source,source_date,citation_type,citation_ref,notes`
2. Use only rows listed in `rate_gaps.csv`, copying `source_sheet` and `source_row` exactly.
3. `component_type` is one of `material`, `labour`, `plant`, `carriage`.
4. `qty_per_unit` is the component quantity for one BOQ unit; `unit` is the component's own unit (for example kg of
   slurry per sqm of wall).
5. `unit_price` excludes GST. Prefer HSR basic rates: set `price_source` to the code (for example `B0139`) and
   `unit_price` to its rate from `hsr_2021_basic_rates.csv`, leaving `source_date` empty. Otherwise `price_source` is
   the URL you read and `source_date` is that day as `YYYY-MM-DD`.
6. `citation_type` is one of `hsr`, `dar`, `is`, `astm`, `aci`, `datasheet`, `url`, and `citation_ref` names the exact
   source (for example `IS 456:2000 cl. 26.4.2` or `ACI 350-20`). Prefer HSR/DAR, then IS codes, then ASTM/ACI, then
   manufacturer datasheets, then URLs. Use American standards only when no Indian reference covers the point, convert
   imperial values to metric, and state the conversion in `notes`.
7. Do not add profit, overheads, labour cess or GST — BuildFlow applies them.
8. If you cannot source a price or a quantity, write `unknown` in that field instead of guessing. BuildFlow rejects and
   reports that line.
9. Price for Karnal, Haryana, at today's date.

## Example

```csv
source_sheet,source_row,component_type,description,qty_per_unit,unit,unit_price,price_source,source_date,citation_type,citation_ref,notes
03 Boq,84,material,Integral crystalline slurry,0.8,kg,238,B0139,,datasheet,Manufacturer coverage 0.8 kg per sqm for two coats,Two coats included in the quantity
03 Boq,84,labour,Beldar,0.05,day,364,LB008,,dar,DAR 2023 analogous waterproofing item,
```
````

- [ ] **Step 4: Create `prompts/rate_guess.md`**

````markdown
# Tier-C rate guess prompt (BuildFlow) — comparison only

Use this file as instructions in Claude, ChatGPT or Gemini. Attach `rate_gaps.csv`. These numbers appear only as a
comparison column in BuildFlow and never enter any total.

## Rules

1. Output only CSV with exactly this header, one line per row of `rate_gaps.csv`:
   `source_sheet,source_row,unit,rate_ex_gst,model,date,notes`
2. Copy `source_sheet`, `source_row` and `unit` exactly from `rate_gaps.csv`.
3. `rate_ex_gst` is your best estimate per BOQ unit, excluding GST, for Karnal, Haryana, at today's date.
4. `model` is your model name; `date` is today as `YYYY-MM-DD`; `notes` says what the rate includes.

## Example

```csv
source_sheet,source_row,unit,rate_ex_gst,model,date,notes
03 Boq,84,Sqm,650,claude-opus-5,2026-09-11,Two coats of crystalline slurry including surface preparation
```
````

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_prompts.py -q`
Expected: PASS (2 passed). Then `python3 -m pytest -q` → all tests pass.

- [ ] **Step 6: Document the workflow in `README.md`**

Insert this section immediately before `## Expected BOQ columns`:

````markdown
## AI pricing for gaps

Rows with no tender rate, HSR rate or approved match can be priced from cited rate analyses:

```bash
python3 -m buildflow.cli BOQ.xlsx --typology stp_tank --rate-matches rate_matches.csv --rate-gaps rate_gaps.csv
```

Give `rate_gaps.csv` and `data/rates/hsr_2021_basic_rates.csv` to Claude, ChatGPT or Gemini with
`prompts/rate_analysis.md` as the instructions, save the CSV it returns as `ai_rate_components.csv`, and re-run with
`--ai-components ai_rate_components.csv --rate-basis demo`. BuildFlow checks every line (known row, positive numbers, an
HSR basic code or a dated URL for each price, a citation for each quantity) and computes the rate itself: materials,
labour and plant × (1 + 15% profit and overheads), plus carriage, × (1 + 1% labour cess). `prompts/rate_guess.md`
produces `ai_rate_guesses.csv` for `--ai-guesses`, a comparison column that never enters any total. Only gap-row
descriptions and units leave the machine; check each chat app's data settings before pasting tender text.

````

- [ ] **Step 7: Commit**

```bash
git add prompts tests/test_prompts.py README.md
git commit -m "docs(rates): add prompt pack for cited AI rate analyses and guesses" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```

### Task 14: Matcher evaluation against a verified gold set

**Files:**
- Create: `buildflow/rate_benchmark.py`
- Create (human-verified): `data/benchmark/stp_hsr_gold.csv`
- Test: `tests/test_rate_benchmark.py`

**Interfaces:**
- Consumes: `match_items`, `row_key`, `shortlist` (Tasks 6–7); `match_rate_items` (Task 8); `load_rate_table` (Task 5); `read_boq` (existing).
- Produces: `GOLD_HEADER = ["source_sheet", "source_row", "description", "unit", "expected_hsr_code", "expected_multiplier", "verified_by", "notes"]`; `evaluate(gold: list[dict], items: list[BOQItem], table: RateTable, picker=None) -> dict` (keys `verified_rows`, `unverified_rows`, `by_method` → `{method: {rows, correct, accuracy}}`, `overall_accuracy`, `auto_priced_share`); `draft(boq_path, table, out) -> int`; CLI `python3 -m buildflow.rate_benchmark BOQ [--gold PATH] [--draft OUT] [--groq] [--output JSON]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rate_benchmark.py
from __future__ import annotations

import csv

from buildflow.models import BOQItem
from buildflow.rate_benchmark import GOLD_HEADER, draft, evaluate
from buildflow.rate_table import RateItem, RateTable

PLASTER = "Cement plaster 20 mm thick 1:4 coarse sand finished smooth"


def rates() -> RateTable:
    return RateTable({"11.10.1": RateItem("11.10.1", PLASTER, "sqm", 144.0, None, None, None, (144.0,), 235, "HSR 2021")}, {})


def boq(row: int, text: str) -> BOQItem:
    return BOQItem(f"B{row}", text, "Sqm", 1.0, 0.0, 0.0, source_row=row, source_sheet="03 Boq")


def gold(row: int, code: str, verified: str = "LS") -> dict[str, str]:
    return {"source_sheet": "03 Boq", "source_row": str(row), "description": "", "unit": "Sqm", "expected_hsr_code": code,
            "expected_multiplier": "1", "verified_by": verified, "notes": ""}


def test_accuracy_per_method_counts_only_verified_rows():
    items = [boq(1, f"FINISHING > {PLASTER}"), boq(2, "kota flooring"), boq(3, "painting")]
    report = evaluate([gold(1, "11.10.1"), gold(2, "none"), gold(3, "11.10.1", verified="")], items, rates())
    assert (report["verified_rows"], report["unverified_rows"]) == (2, 1)
    assert report["by_method"] == {
        "none": {"rows": 1, "correct": 1, "accuracy": 1.0},
        "verbatim": {"rows": 1, "correct": 1, "accuracy": 1.0},
    }
    assert (report["overall_accuracy"], report["auto_priced_share"]) == (1.0, 0.5)


def test_draft_proposes_top_candidates_for_verification(tmp_path):
    boq_csv = tmp_path / "boq.csv"
    boq_csv.write_text("Item,Description,Unit,Quantity,Rate,Amount\n1,Cement plaster 20 mm 1:4,Sqm,10,,\n", encoding="utf-8")
    out = tmp_path / "gold.csv"
    assert draft(boq_csv, rates(), out) == 1
    with out.open(encoding="utf-8") as handle:
        (row,) = list(csv.DictReader(handle))
    assert list(row) == GOLD_HEADER
    assert (row["source_row"], row["expected_hsr_code"], row["verified_by"]) == ("2", "11.10.1", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_rate_benchmark.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'buildflow.rate_benchmark'`

- [ ] **Step 3: Write the implementation**

```python
# buildflow/rate_benchmark.py
"""Evaluate the HSR matcher against a user-verified gold CSV (spec §11)."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from .ingestion import read_boq
from .llm import match_rate_items
from .models import BOQItem, ProjectConfig
from .rate_matching import Picker, match_items, row_key, shortlist
from .rate_table import RateTable, load_rate_table

GOLD_HEADER = ["source_sheet", "source_row", "description", "unit", "expected_hsr_code", "expected_multiplier", "verified_by", "notes"]


def evaluate(gold: list[dict[str, str]], items: list[BOQItem], table: RateTable, picker: Picker | None = None) -> dict:
    verified = {(row["source_sheet"], int(row["source_row"])): row for row in gold if (row.get("verified_by") or "").strip()}
    rows = [entry for entry in items if row_key(entry) in verified]
    matches, _ = match_items(rows, table, {}, picker)
    counts: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    for entry in rows:
        match = matches[row_key(entry)]
        method = match.method or "none"
        expected = (verified[row_key(entry)]["expected_hsr_code"] or "").strip() or "none"
        counts[method] += 1
        correct[method] += (match.hsr_code or "none") == expected
    total = sum(counts.values())
    return {
        "verified_rows": total,
        "unverified_rows": len(gold) - len(verified),
        "by_method": {method: {"rows": rows_, "correct": correct[method], "accuracy": round(correct[method] / rows_, 4)}
                      for method, rows_ in sorted(counts.items())},
        "overall_accuracy": round(sum(correct.values()) / total, 4) if total else 0.0,
        "auto_priced_share": round(counts["verbatim"] / total, 4) if total else 0.0,
    }


def draft(boq_path: str | Path, table: RateTable, out: str | Path) -> int:
    """Write a gold-set draft: the top shortlist candidate per row, left unverified for a planner to check."""
    rows = []
    for entry in read_boq(boq_path):
        top = shortlist(entry.description, entry.unit, table, 1)
        rows.append({"source_sheet": entry.source_sheet, "source_row": entry.source_row, "description": entry.description,
                     "unit": entry.unit, "expected_hsr_code": top[0].code if top else "none", "expected_multiplier": "1",
                     "verified_by": "", "notes": "top shortlist candidate - verify against the HSR page"})
    with Path(out).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=GOLD_HEADER)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate BOQ → HSR matching against a verified gold CSV")
    parser.add_argument("boq", help="BOQ file the gold set was built from")
    parser.add_argument("--gold", default="data/benchmark/stp_hsr_gold.csv")
    parser.add_argument("--draft", help="Write a draft gold CSV to this path instead of evaluating")
    parser.add_argument("--groq", action="store_true", help="Let Groq pick among candidates (needs GROQ_API_KEY)")
    parser.add_argument("--output", help="Optional path for the JSON report")
    args = parser.parse_args()
    table = load_rate_table()
    if args.draft:
        print(f"Wrote {draft(args.boq, table, args.draft)} draft row(s) to {args.draft}")
        return
    with Path(args.gold).open(newline="", encoding="utf-8-sig") as handle:
        gold = list(csv.DictReader(handle))
    model = ProjectConfig().llm_model
    picker = (lambda requests: match_rate_items(requests, model)) if args.groq else None
    report = json.dumps(evaluate(gold, read_boq(args.boq), table, picker), indent=2)
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_rate_benchmark.py -q`
Expected: PASS (2 passed). Then `python3 -m pytest -q` → all tests pass.

- [ ] **Step 5: Draft the STP gold set (human verification required)**

```bash
mkdir -p data/benchmark
python3 -m buildflow.rate_benchmark ~/Downloads/"BOQ STP (1).xlsx" --draft data/benchmark/stp_hsr_gold.csv
```

Expected: `Wrote 38 draft row(s) to data/benchmark/stp_hsr_gold.csv`. Then a planner (the user) opens each row, checks the HSR page for the proposed code, corrects `expected_hsr_code` (or writes `none`), sets `expected_multiplier` for extra-lift depth bands, and puts their initials in `verified_by`. Only rows with `verified_by` count. The file contains tender descriptions: commit it only while the repository stays private.

- [ ] **Step 6: Run the evaluation**

Run: `python3 -m buildflow.rate_benchmark ~/Downloads/"BOQ STP (1).xlsx" --output rate_benchmark.json`
Expected: a JSON report with `verified_rows` equal to the number of rows the planner initialled. Once prerequisite P2 (parent-row parser) lands, re-run it: `auto_priced_share` should rise because full descriptions reach the verbatim rule.

- [ ] **Step 7: Commit**

```bash
git add buildflow/rate_benchmark.py tests/test_rate_benchmark.py data/benchmark/stp_hsr_gold.csv
git commit -m "feat(rates): evaluate HSR matching against a verified gold set" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XT7QLoihCMV1FCYixq5P8q"
```
