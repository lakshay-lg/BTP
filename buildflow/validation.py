from __future__ import annotations

from collections import Counter

from .models import Activity, BOQItem, Finding, ProjectConfig
from .scheduling import topological_order


def validate(
    items: list[BOQItem], activities: list[Activity], typology: str, config: ProjectConfig
) -> list[Finding]:
    findings: list[Finding] = []
    try:
        topological_order(activities)
    except ValueError as exc:
        findings.append(Finding("error", "CPM_INVALID", str(exc), "Correct predecessor links before using the schedule"))
    boq_total = sum(item.amount for item in items)
    scheduled_total = sum(activity.cost for activity in activities)
    if abs(boq_total - scheduled_total) > max(1.0, boq_total * 0.001):
        findings.append(
            Finding(
                "error",
                "COST_RECONCILIATION",
                f"Activity costs ({scheduled_total:,.2f}) do not reconcile to BOQ value ({boq_total:,.2f}).",
                "Review package allocation shares",
            )
        )
    unknown = [item for item in items if item.work_package == "unknown"]
    if unknown:
        findings.append(
            Finding(
                "warning",
                "UNCLASSIFIED_ITEMS",
                f"{len(unknown)} of {len(items)} BOQ rows remain unclassified and were placed in miscellaneous work.",
                "Review the low-confidence rows or extend the taxonomy",
            )
        )
    low_confidence = [item for item in items if item.confidence is not None and item.confidence < 0.68]
    if low_confidence:
        findings.append(
            Finding(
                "warning",
                "LOW_CONFIDENCE",
                f"{len(low_confidence)} BOQ rows have classification confidence below 0.68.",
                "Planner review is required; optionally enable the opt-in LLM fallback",
            )
        )
    unpriced = [item for item in items if item.amount_missing or (item.classifier != 'planner_reviewed' and item.amount <= 0)]
    if unpriced:
        findings.append(
            Finding(
                "warning",
                "UNPRICED_ITEMS",
                f"{len(unpriced)} BOQ rows are unpriced, so the cash-flow total is incomplete.",
                "Enter verified tender rates or amounts; do not use invented market rates for validation",
            )
        )
    package_counts = Counter(item.work_package for item in items)
    if typology == "stp_tank" and not package_counts["dewatering_shoring"]:
        findings.append(
            Finding(
                "high",
                "STP_TEMPORARY_WORKS",
                "No dewatering or shoring item was found for the tank excavation.",
                "Confirm excavation depth, groundwater level, safe side slopes and temporary-works scope",
            )
        )
    if typology == "stp_tank" and not package_counts["testing"]:
        findings.append(
            Finding(
                "high",
                "STP_LEAK_TEST",
                "Hydrostatic/leak testing is absent from the BOQ; the scheduler inserted a zero-cost hold point.",
                "Add test method, acceptance criteria, water source and rectification allowance",
            )
        )
    if typology == "rwh" and config.structure_count > 1:
        findings.append(
            Finding(
                "info",
                "RWH_QUANTITY_SCOPE",
                f"The model is allocating BOQ quantities across {config.structure_count} tanks.",
                "Verify whether the uploaded quantities already cover all tanks before approving the baseline",
            )
        )
    project_duration = max((activity.finish_day for activity in activities), default=0)
    if config.contract_duration_days:
        delta = project_duration - config.contract_duration_days
        if abs(delta) > max(5, config.contract_duration_days * 0.1):
            direction = "longer" if delta > 0 else "shorter"
            findings.append(
                Finding(
                    "warning",
                    "DURATION_VARIANCE",
                    f"The norms-based CPM duration is {abs(delta)} working days {direction} than the entered contract duration.",
                    "Calibrate productivity, crew counts, calendars and constraints against actual site records",
                )
            )
    if config.start_date[5:7] in {"06", "07", "08", "09"} and typology in {"stp_tank", "rwh"}:
        findings.append(
            Finding(
                "warning",
                "MONSOON_EXPOSURE",
                "Substructure work starts during or near the north-Indian monsoon window.",
                "Model rain days, pumping capacity, access and concrete pour protection using project-specific data",
            )
        )
    if not findings:
        findings.append(Finding("info", "CHECKS_PASSED", "No structural or reconciliation errors were detected."))
    return findings
