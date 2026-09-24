from __future__ import annotations

import copy
import uuid
from collections import Counter

from .cashflow import compare_cashflows, independent_phase_cashflow, schedule_cashflow
from .llm import GROQ, LLMClassificationError, classify_ambiguous_items
from .models import AnalysisResult, BOQItem, Finding, ProjectConfig, TraceEvent
from .scheduling import apply_duration_overrides, build_activities
from .taxonomy import classify_items, infer_typology
from .validation import validate


class ProjectAgent:
    """A tool-orchestrating workflow; engineering calculations remain deterministic."""

    def run(self, source_items: list[BOQItem], config: ProjectConfig, *, reviewed: bool = False) -> AnalysisResult:
        items = copy.deepcopy(source_items)
        trace: list[TraceEvent] = []
        assumptions: list[str] = []

        self._trace(trace, "inspect_boq", "completed", f"Accepted {len(items)} priced/quantity rows", {"unpriced": sum(item.amount <= 0 for item in items)})

        inferred, inferred_confidence, typology_scores = infer_typology(items, config.name)
        if config.typology == "auto":
            typology, typology_confidence = inferred, inferred_confidence
            decision = f"Inferred {typology} at {typology_confidence:.0%} confidence"
        else:
            typology, typology_confidence = config.typology, 1.0
            decision = f"Used planner-selected typology {typology}"
        self._trace(trace, "select_typology", "completed", decision, {"scores": typology_scores})

        mode = config.classifier_mode if config.classifier_mode in {"rules", "retrieval", "hybrid"} else "hybrid"
        if reviewed:
            if not all(item.classifier == 'planner_reviewed' for item in items):
                raise ValueError('Reviewed input requires planner-reviewed classifications')
        else:
            classify_items(items, mode)
        self._trace(
            trace,
            "classify_boq",
            "completed",
            "Retained planner-reviewed classifications from verified chat import" if reviewed else f"Classified BOQ rows with {mode} baseline",
            {"low_confidence": sum(item.confidence is not None and item.confidence < 0.68 for item in items), "packages": dict(Counter(item.work_package for item in items))},
        )

        llm_finding: Finding | None = None
        if config.use_llm_fallback and not reviewed:
            try:
                before = sum(item.confidence < 0.68 for item in items)
                classify_ambiguous_items(items, config.llm_model)
                after = sum(item.confidence < 0.68 for item in items)
                self._trace(trace, "llm_reader", "completed", f"AI reader reviewed {before} ambiguous rows; {after} still require review", {"provider": GROQ.name, "model": config.llm_model})
            except LLMClassificationError as exc:
                self._trace(trace, "llm_reader", "skipped", "AI reader unavailable; retained offline hybrid classifications", {"reason": str(exc)})
                llm_finding = Finding("warning", "LLM_FALLBACK_SKIPPED", str(exc), f"Set {GROQ.key_env} or leave AI fallback disabled")
        else:
            self._trace(trace, "llm_reader", "skipped", "External AI reading was not enabled; no BOQ text left this machine")

        activities = build_activities(items, typology, config)
        duration = max((activity.finish_day for activity in activities), default=0)
        self._trace(
            trace,
            "build_cpm",
            "completed",
            f"Built and solved an acyclic {len(activities)}-activity CPM network",
            {"duration_working_days": duration, "critical_path": [activity.id for activity in activities if activity.critical]},
        )

        schedule_curve = schedule_cashflow(activities, config)
        independent_curve = [] if reviewed and config.cashflow_mode == 'schedule' else independent_phase_cashflow(items, config, duration)
        comparison = compare_cashflows(schedule_curve, independent_curve)
        self._trace(
            trace,
            "time_phase_cost",
            "completed",
            "Schedule-only reviewed import; independent forecast not generated" if reviewed and config.cashflow_mode == 'schedule' else "Generated schedule-linked and independent phase-pattern cash curves",
            comparison,
        )

        findings = validate(items, activities, typology, config)
        if llm_finding:
            findings.append(llm_finding)
        self._trace(
            trace,
            "audit_result",
            "completed",
            f"Raised {sum(f.severity in {'error', 'high', 'warning'} for f in findings)} review items",
            {"codes": [finding.code for finding in findings]},
        )

        boq_total = sum(item.amount for item in items)
        scheduled_total = sum(activity.cost for activity in activities)
        confidence_values = [item.confidence for item in items if item.confidence is not None]
        finish_date = max((activity.finish_date for activity in activities), default=config.start_date)
        metrics = {
            "boq_total": round(boq_total, 2),
            "scheduled_total": round(scheduled_total, 2),
            "cost_reconciliation_delta": round(scheduled_total - boq_total, 2),
            "item_count": len(items),
            "classified_percent": round(100 * sum(item.work_package != "unknown" for item in items) / max(len(items), 1), 1),
            "mean_classification_confidence": round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else None,
            "activity_count": len(activities),
            "duration_working_days": duration,
            "finish_date": finish_date,
            "critical_path": [activity.id for activity in activities if activity.critical],
            "cashflow_comparison": comparison,
        }
        assumptions.extend(
            [
                "Durations use editable productivity defaults and one logical crew per work front unless the crew multiplier is changed.",
                f"Calendar uses a {config.workweek_days}-day work week and does not yet include project holidays or weather-loss days.",
                "Finish-to-start links are generated from a typology template; a planner must review site access, resources and contractual constraints.",
                "The independent cash curve uses contract duration and phase windows only; it does not consume the CPM dates.",
                "BOQ values are time-phased as planned work value. They are not a substitute for contractor cost ledgers or certified IPC records.",
            ]
        )
        if config.data_provenance == "sanitised_demo":
            assumptions.insert(0, "Built-in rows are sanitised demonstrations, not verbatim tender evidence or validation ground truth.")
        if config.indirect_cost_pct:
            assumptions.append(f"Site overhead is modelled as {config.indirect_cost_pct:.2f}% of BOQ value, spread evenly over active months.")
        if config.retention_pct:
            assumptions.append(f"Retention is {config.retention_pct:.2f}% and receipts lag planned work by {config.payment_lag_months} month(s).")

        if config.cashflow_mode == "phase":
            primary, secondary = independent_curve, []
        elif config.cashflow_mode == "schedule":
            primary, secondary = schedule_curve, []
        else:
            primary, secondary = schedule_curve, independent_curve
        result = AnalysisResult(
            project=config,
            typology=typology,
            typology_confidence=typology_confidence,
            items=items,
            activities=activities,
            cashflow=primary,
            independent_cashflow=secondary,
            findings=findings,
            trace=trace,
            metrics=metrics,
            assumptions=assumptions,
            job_id=uuid.uuid4().hex[:12],
        )
        self._mark_unavailable_prices(result)
        return result

    def replan(self, result: AnalysisResult, overrides: list[dict]) -> AnalysisResult:
        updated = copy.deepcopy(result)
        updated.activities = apply_duration_overrides(
            updated.activities,
            overrides,
            updated.project.start_date,
            updated.project.workweek_days,
        )
        duration = max((activity.finish_day for activity in updated.activities), default=0)
        schedule_curve = schedule_cashflow(updated.activities, updated.project)
        independent_curve = [] if updated.normalization_review and updated.project.cashflow_mode == 'schedule' else independent_phase_cashflow(updated.items, updated.project, duration)
        updated.metrics["duration_working_days"] = duration
        updated.metrics["finish_date"] = max((a.finish_date for a in updated.activities), default=updated.project.start_date)
        updated.metrics["critical_path"] = [activity.id for activity in updated.activities if activity.critical]
        updated.metrics["cashflow_comparison"] = compare_cashflows(schedule_curve, independent_curve)
        if updated.project.cashflow_mode == "phase":
            updated.cashflow, updated.independent_cashflow = independent_curve, []
        elif updated.project.cashflow_mode == "schedule":
            updated.cashflow, updated.independent_cashflow = schedule_curve, []
        else:
            updated.cashflow, updated.independent_cashflow = schedule_curve, independent_curve
        updated.findings = validate(updated.items, updated.activities, updated.typology, updated.project)
        updated.findings.extend(copy.deepcopy(f) for f in result.findings if f.code == 'PLANNER_INPUT_OVERRIDES')
        self._trace(updated.trace, "planner_replan", "completed", f"Applied {len(overrides)} planner override(s) and recalculated CPM/cash flow")
        self._mark_unavailable_prices(updated)
        if updated.normalization_review and updated.project.cashflow_mode == 'schedule':
            updated.cashflow=[]
            updated.independent_cashflow=[]
            updated.metrics['cashflow_comparison']={}
        return updated

    @staticmethod
    def _mark_unavailable_prices(result: AnalysisResult) -> None:
        if any(item.amount_missing for item in result.items):
            result.cashflow = []
            result.independent_cashflow = []
            result.metrics.update(boq_total=None, scheduled_total=None,
                                  cost_reconciliation_delta=None, cashflow_comparison={}, prices_complete=False)

    @staticmethod
    def _trace(trace: list[TraceEvent], tool: str, status: str, summary: str, details: dict | None = None) -> None:
        trace.append(TraceEvent(len(trace) + 1, tool, status, summary, details or {}))
