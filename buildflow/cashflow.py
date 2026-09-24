from __future__ import annotations

import calendar as month_calendar
from collections import defaultdict
from datetime import date, timedelta

from .calendar import parse_date, working_day_at, working_days_between
from .models import Activity, BOQItem, CashFlowPeriod, ProjectConfig


PHASE_WINDOWS = {
    "preliminaries": (0.00, 0.10),
    "earthwork": (0.00, 0.18),
    "dewatering_shoring": (0.00, 0.28),
    "pcc": (0.08, 0.22),
    "rcc": (0.12, 0.60),
    "reinforcement": (0.10, 0.60),
    "formwork": (0.10, 0.62),
    "masonry": (0.38, 0.72),
    "waterproofing": (0.55, 0.82),
    "sewer_pipe": (0.12, 0.68),
    "storm_pipe": (0.10, 0.68),
    "pressure_pipe": (0.18, 0.72),
    "internal_drainage": (0.35, 0.82),
    "manhole": (0.18, 0.74),
    "borewell": (0.05, 0.48),
    "plumbing": (0.45, 0.88),
    "electrical": (0.42, 0.90),
    "mechanical_equipment": (0.62, 0.92),
    "finishes": (0.58, 0.94),
    "backfill": (0.62, 0.88),
    "road_reinstatement": (0.72, 0.94),
    "testing": (0.90, 1.00),
    "unknown": (0.10, 0.92),
}


def _month_key(day: date) -> str:
    return day.strftime("%Y-%m")


def _month_sequence(start: date, finish: date) -> list[str]:
    result: list[str] = []
    current = date(start.year, start.month, 1)
    last = date(finish.year, finish.month, 1)
    while current <= last:
        result.append(_month_key(current))
        current = date(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1)
    return result


def _shift_month(period: str, offset: int) -> str:
    year, month = map(int, period.split("-"))
    index = year * 12 + month - 1 + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _period_end(period: str) -> date:
    year, month = map(int, period.split("-"))
    return date(year, month, month_calendar.monthrange(year, month)[1])


def _build_periods(
    direct: dict[str, float],
    ordered_months: list[str],
    config: ProjectConfig,
) -> list[CashFlowPeriod]:
    if not ordered_months:
        return []
    total_direct = sum(direct.values())
    overhead_total = total_direct * config.indirect_cost_pct / 100.0
    overhead_per_month = overhead_total / len(ordered_months)
    receipt_map: dict[str, float] = defaultdict(float)
    retention_map: dict[str, float] = defaultdict(float)
    for period in ordered_months:
        retention = direct.get(period, 0.0) * config.retention_pct / 100.0
        receipt_period = _shift_month(period, config.payment_lag_months)
        receipt_map[receipt_period] += direct.get(period, 0.0) - retention
        retention_map[period] += retention
    all_months = list(ordered_months)
    for period in sorted(receipt_map):
        if period not in all_months:
            all_months.append(period)
    all_months.sort()
    cumulative = 0.0
    periods: list[CashFlowPeriod] = []
    for period in all_months:
        gross = direct.get(period, 0.0)
        overhead = overhead_per_month if period in ordered_months else 0.0
        expenditure = gross + overhead
        receipt = receipt_map.get(period, 0.0)
        cumulative += gross
        periods.append(
            CashFlowPeriod(
                period=period,
                gross_work_value=round(gross, 2),
                site_overhead=round(overhead, 2),
                planned_expenditure=round(expenditure, 2),
                certified_receipt=round(receipt, 2),
                retention_withheld=round(retention_map.get(period, 0.0), 2),
                net_cash_flow=round(receipt - expenditure, 2),
                cumulative_work_value=round(cumulative, 2),
                cumulative_percent=round(100 * cumulative / total_direct, 2) if total_direct else 0.0,
            )
        )
    return periods


def schedule_cashflow(activities: list[Activity], config: ProjectConfig) -> list[CashFlowPeriod]:
    direct: dict[str, float] = defaultdict(float)
    active_start: date | None = None
    active_finish: date | None = None
    for activity in activities:
        if activity.cost <= 0:
            continue
        start, finish = parse_date(activity.start_date), parse_date(activity.finish_date)
        workdays = working_days_between(start, finish, config.workweek_days)
        if not workdays:
            workdays = [start]
        daily_value = activity.cost / len(workdays)
        for day in workdays:
            direct[_month_key(day)] += daily_value
        active_start = start if active_start is None else min(active_start, start)
        active_finish = finish if active_finish is None else max(active_finish, finish)
    if active_start is None or active_finish is None:
        return []
    months = _month_sequence(active_start, active_finish)
    return _build_periods(direct, months, config)


def _smoothstep(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3.0 - 2.0 * value)


def _phase_cdf(progress: float, window: tuple[float, float]) -> float:
    start, finish = window
    if progress <= start:
        return 0.0
    if progress >= finish:
        return 1.0
    return _smoothstep((progress - start) / max(finish - start, 0.001))


def independent_phase_cashflow(
    items: list[BOQItem], config: ProjectConfig, fallback_duration_days: int
) -> list[CashFlowPeriod]:
    duration_days = config.contract_duration_days or max(1, fallback_duration_days)
    start = parse_date(config.start_date)
    finish = working_day_at(start, duration_days - 1, config.workweek_days)
    months = _month_sequence(start, finish)
    if not months:
        return []
    project_calendar_days = max(1, (finish - start).days + 1)
    direct: dict[str, float] = defaultdict(float)
    for item in items:
        window = PHASE_WINDOWS.get(item.work_package, PHASE_WINDOWS["unknown"])
        previous = 0.0
        allocations: list[tuple[str, float]] = []
        for period in months:
            period_finish = min(_period_end(period), finish)
            progress = min(1.0, max(0.0, ((period_finish - start).days + 1) / project_calendar_days))
            cumulative = _phase_cdf(progress, window)
            allocations.append((period, max(0.0, cumulative - previous)))
            previous = cumulative
        # Floating point and narrow-window correction: preserve the priced BOQ total exactly.
        weight_total = sum(weight for _, weight in allocations)
        if weight_total <= 0:
            direct[months[-1]] += item.amount
        else:
            for period, weight in allocations:
                direct[period] += item.amount * weight / weight_total
    return _build_periods(direct, months, config)


def compare_cashflows(schedule: list[CashFlowPeriod], independent: list[CashFlowPeriod]) -> dict[str, float | str]:
    schedule_by_month = {period.period: period.gross_work_value for period in schedule}
    independent_by_month = {period.period: period.gross_work_value for period in independent}
    months = sorted(set(schedule_by_month) | set(independent_by_month))
    total = max(sum(schedule_by_month.values()), 1.0)
    mae = sum(abs(schedule_by_month.get(month, 0.0) - independent_by_month.get(month, 0.0)) for month in months) / max(len(months), 1)
    schedule_peak = max(schedule_by_month, key=schedule_by_month.get) if schedule_by_month else ""
    independent_peak = max(independent_by_month, key=independent_by_month.get) if independent_by_month else ""
    return {
        "monthly_mae": round(mae, 2),
        "monthly_mae_percent_of_value": round(100 * mae / total, 2),
        "schedule_peak_month": schedule_peak,
        "independent_peak_month": independent_peak,
    }
