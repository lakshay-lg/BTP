from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
import os
from typing import Any


@dataclass
class BOQItem:
    id: str
    description: str
    unit: str
    quantity: float
    rate: float
    amount: float
    source_row: int = 0
    source_sheet: str = ""
    work_package: str = "unknown"
    phase: str = "unclassified"
    track: str = "general"
    confidence: float | None = 0.0
    classifier: str = ""
    evidence: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    amount_missing: bool = False
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectConfig:
    name: str = "Untitled construction project"
    typology: str = "auto"
    start_date: str = field(default_factory=lambda: date.today().isoformat())
    contract_duration_days: int | None = None
    structure_count: int = 1
    workweek_days: int = 6
    classifier_mode: str = "hybrid"
    use_llm_fallback: bool = False
    llm_model: str = field(default_factory=lambda: os.getenv("BUILDFLOW_LLM_MODEL", "openai/gpt-oss-120b"))
    cashflow_mode: str = "compare"
    indirect_cost_pct: float = 0.0
    retention_pct: float = 0.0
    payment_lag_months: int = 1
    crew_multiplier: float = 1.0
    data_provenance: str = "user_upload"

    def __post_init__(self) -> None:
        if self.typology not in {"auto", "building", "stp_tank", "linear_mep", "rwh"}:
            raise ValueError(f"Unsupported typology: {self.typology}")
        if self.contract_duration_days is not None and self.contract_duration_days <= 0:
            raise ValueError("Contract duration must be greater than zero")
        if not 1 <= self.structure_count <= 100:
            raise ValueError("Structure count must be between 1 and 100")
        if not 1 <= self.workweek_days <= 7:
            raise ValueError("Workweek days must be between 1 and 7")
        if self.crew_multiplier <= 0:
            raise ValueError("Crew multiplier must be greater than zero")
        if not 0 <= self.indirect_cost_pct <= 100 or not 0 <= self.retention_pct <= 100:
            raise ValueError("Overhead and retention percentages must be between 0 and 100")
        if self.payment_lag_months < 0:
            raise ValueError("Payment lag cannot be negative")
        if self.classifier_mode not in {"rules", "retrieval", "hybrid"}:
            raise ValueError(f"Unsupported classifier mode: {self.classifier_mode}")
        if self.cashflow_mode not in {"schedule", "phase", "compare"}:
            raise ValueError(f"Unsupported cash-flow mode: {self.cashflow_mode}")


@dataclass
class Activity:
    id: str
    name: str
    work_package: str
    track: str
    quantity: float
    unit: str
    productivity_per_day: float
    duration_days: int
    predecessors: list[str]
    cost: float
    boq_item_ids: list[str] = field(default_factory=list)
    start_day: int = 0
    finish_day: int = 0
    start_date: str = ""
    finish_date: str = ""
    total_float_days: int = 0
    critical: bool = False
    assumption: str = ""


@dataclass
class CashFlowPeriod:
    period: str
    gross_work_value: float
    site_overhead: float
    planned_expenditure: float
    certified_receipt: float
    retention_withheld: float
    net_cash_flow: float
    cumulative_work_value: float
    cumulative_percent: float


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    recommendation: str = ""


@dataclass
class TraceEvent:
    step: int
    tool: str
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisResult:
    project: ProjectConfig
    typology: str
    typology_confidence: float
    items: list[BOQItem]
    activities: list[Activity]
    cashflow: list[CashFlowPeriod]
    independent_cashflow: list[CashFlowPeriod]
    findings: list[Finding]
    trace: list[TraceEvent]
    metrics: dict[str, Any]
    assumptions: list[str]
    job_id: str = ""
    normalization_review: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_from_dict(data: dict[str, Any]) -> ProjectConfig:
    fields = ProjectConfig.__dataclass_fields__
    cleaned = {key: value for key, value in data.items() if key in fields}
    for key in ("contract_duration_days", "structure_count", "workweek_days", "payment_lag_months"):
        if key in cleaned and cleaned[key] not in (None, ""):
            cleaned[key] = int(cleaned[key])
    for key in ("indirect_cost_pct", "retention_pct", "crew_multiplier"):
        if key in cleaned and cleaned[key] not in (None, ""):
            cleaned[key] = float(cleaned[key])
    if "use_llm_fallback" in cleaned:
        value = cleaned["use_llm_fallback"]
        cleaned["use_llm_fallback"] = value is True or str(value).lower() in {"1", "true", "yes", "on"}
    return ProjectConfig(**cleaned)
