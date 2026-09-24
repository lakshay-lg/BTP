from __future__ import annotations

from datetime import date, datetime, timedelta


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def is_working_day(day: date, workweek_days: int = 6) -> bool:
    if workweek_days >= 7:
        return True
    if workweek_days == 6:
        return day.weekday() != 6
    return day.weekday() < max(1, workweek_days)


def next_working_day(day: date, workweek_days: int = 6) -> date:
    while not is_working_day(day, workweek_days):
        day += timedelta(days=1)
    return day


def working_day_at(start: date, offset: int, workweek_days: int = 6) -> date:
    current = next_working_day(start, workweek_days)
    remaining = max(0, offset)
    while remaining:
        current += timedelta(days=1)
        if is_working_day(current, workweek_days):
            remaining -= 1
    return current


def working_days_between(start: date, finish: date, workweek_days: int = 6) -> list[date]:
    result: list[date] = []
    current = start
    while current <= finish:
        if is_working_day(current, workweek_days):
            result.append(current)
        current += timedelta(days=1)
    return result
