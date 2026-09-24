from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date

from .calendar import parse_date, working_day_at
from .models import Activity, BOQItem, ProjectConfig


@dataclass(frozen=True)
class StageSpec:
    id: str
    name: str
    allocations: dict[str, float] = field(default_factory=dict)
    predecessors: tuple[str, ...] = ()
    track: str = "general"
    accepted_tracks: tuple[str, ...] = ()
    minimum_days: int = 1
    wait_days: int = 0
    assumption: str = ""


PRODUCTIVITY = {
    "preliminaries": {"default": 1.0},
    "earthwork": {"m3": 120.0, "default": 100.0},
    "dewatering_shoring": {"day": 1.0, "default": 1.0},
    "pcc": {"m3": 25.0, "default": 22.0},
    "rcc": {"m3": 18.0, "default": 16.0},
    "reinforcement": {"kg": 1500.0, "t": 1.5, "default": 1.5},
    "formwork": {"m2": 65.0, "ft2": 700.0, "default": 55.0},
    "masonry": {"m3": 8.0, "m2": 35.0, "default": 20.0},
    "waterproofing": {"m2": 80.0, "ft2": 860.0, "default": 70.0},
    "sewer_pipe": {"m": 35.0, "default": 30.0},
    "storm_pipe": {"m": 30.0, "default": 25.0},
    "pressure_pipe": {"m": 45.0, "default": 40.0},
    "internal_drainage": {"m": 30.0, "no": 8.0, "default": 20.0},
    "manhole": {"no": 0.5, "default": 0.5},
    "borewell": {"m": 12.0, "default": 10.0},
    "plumbing": {"point": 8.0, "no": 8.0, "default": 10.0},
    "electrical": {"point": 12.0, "m": 100.0, "default": 12.0},
    "mechanical_equipment": {"no": 1.0, "default": 1.0},
    "finishes": {"m2": 55.0, "ft2": 590.0, "default": 45.0},
    "backfill": {"m3": 140.0, "default": 120.0},
    "road_reinstatement": {"m2": 120.0, "m": 40.0, "default": 80.0},
    "testing": {"no": 1.0, "default": 1.0},
    "unknown": {"default": 1.0},
}


def normalise_unit(unit: str) -> str:
    compact = unit.lower().replace(".", "").replace(" ", "")
    if compact in {"cum", "m3", "cu m", "cubicmetre", "cubicmeter"}:
        return "m3"
    if compact in {"sqm", "m2", "sqmeter", "squaremetre", "squaremeter"}:
        return "m2"
    if compact in {"sqft", "ft2", "squarefeet", "squarefoot"}:
        return "ft2"
    if compact in {"rm", "rmt", "m", "metre", "meter"}:
        return "m"
    if compact in {"kg", "kilogram"}:
        return "kg"
    if compact in {"mt", "ton", "tonne", "t"}:
        return "t"
    if compact in {"nos", "no", "number", "each", "ea"}:
        return "no"
    if compact in {"day", "days"}:
        return "day"
    if compact in {"point", "points"}:
        return "point"
    return "default"


def productivity_for(package: str, unit: str) -> float:
    table = PRODUCTIVITY.get(package, PRODUCTIVITY["unknown"])
    return table.get(normalise_unit(unit), table["default"])


def _stp_template() -> list[StageSpec]:
    return [
        StageSpec("MOB", "Mobilisation, survey and setting out", {"preliminaries": 1.0}, minimum_days=3, assumption="One mobilisation crew"),
        StageSpec("EXC", "Deep excavation and formation", {"earthwork": 1.0, "dewatering_shoring": 1.0}, ("MOB",), minimum_days=3, assumption="Mechanical excavation; shoring/dewatering only if present in BOQ"),
        StageSpec("PCC", "Blinding / PCC base", {"pcc": 1.0}, ("EXC",), minimum_days=2),
        StageSpec("RAFT", "RCC raft: reinforcement, formwork and pour", {"rcc": 0.35, "reinforcement": 0.35, "formwork": 0.15}, ("PCC",), minimum_days=4, wait_days=7, assumption="Includes seven working days before loading the pour"),
        StageSpec("WALL", "RCC tank walls", {"rcc": 0.45, "reinforcement": 0.50, "formwork": 0.60}, ("RAFT",), minimum_days=6, wait_days=7, assumption="Monolithic tank wall sequence; pour-cycle curing allowance included"),
        StageSpec("ROOF", "RCC roof slab", {"rcc": 0.20, "reinforcement": 0.15, "formwork": 0.25}, ("WALL",), minimum_days=4, wait_days=7),
        StageSpec("WPR", "Waterproofing and protective treatment", {"waterproofing": 1.0}, ("ROOF",), minimum_days=3),
        StageSpec("EQP", "Mechanical, electrical and piping installation", {"mechanical_equipment": 1.0, "electrical": 1.0, "plumbing": 1.0}, ("ROOF",), minimum_days=5),
        StageSpec("FIN", "Plant room masonry and finishes", {"masonry": 1.0, "finishes": 1.0}, ("ROOF",), minimum_days=4),
        StageSpec("BFL", "Backfilling around completed structures", {"backfill": 1.0}, ("WPR",), minimum_days=2),
        StageSpec("TST", "Hydrostatic testing and commissioning", {"testing": 1.0}, ("WPR", "EQP"), minimum_days=7, assumption="Mandatory engineering hold point even if absent from the civil BOQ"),
    ]


def _building_template() -> list[StageSpec]:
    return [
        StageSpec("MOB", "Mobilisation and setting out", {"preliminaries": 1.0}, minimum_days=3),
        StageSpec("EXC", "Excavation for foundations", {"earthwork": 1.0, "dewatering_shoring": 1.0}, ("MOB",), minimum_days=3),
        StageSpec("PCC", "PCC and foundation preparation", {"pcc": 1.0}, ("EXC",), minimum_days=2),
        StageSpec("SUB", "RCC substructure", {"rcc": 0.30, "reinforcement": 0.30, "formwork": 0.20}, ("PCC",), minimum_days=6, wait_days=5),
        StageSpec("SUP", "RCC superstructure", {"rcc": 0.70, "reinforcement": 0.70, "formwork": 0.80}, ("SUB",), minimum_days=12, wait_days=7, assumption="Aggregate floor-cycle; calibrate using the actual number of floors"),
        StageSpec("MAS", "Masonry and internal partitions", {"masonry": 1.0}, ("SUP",), minimum_days=5),
        StageSpec("ME1", "MEP first fix", {"plumbing": 0.65, "electrical": 0.65}, ("MAS",), minimum_days=5),
        StageSpec("WPR", "Roof and wet-area waterproofing", {"waterproofing": 1.0}, ("SUP",), minimum_days=3),
        StageSpec("FIN", "Architectural finishes", {"finishes": 1.0}, ("ME1", "WPR"), minimum_days=8),
        StageSpec("ME2", "MEP final fix and equipment", {"plumbing": 0.35, "electrical": 0.35, "mechanical_equipment": 1.0}, ("FIN",), minimum_days=4),
        StageSpec("EXT", "Backfill and external reinstatement", {"backfill": 1.0, "road_reinstatement": 1.0}, ("SUB",), minimum_days=3),
        StageSpec("TST", "Testing and handover", {"testing": 1.0}, ("ME2", "EXT"), minimum_days=5),
    ]


def _linear_template() -> list[StageSpec]:
    return [
        StageSpec("MOB", "Mobilisation, utility survey and setting out", {"preliminaries": 1.0}, minimum_days=3),
        StageSpec("SEX", "Sewer trench excavation", {"earthwork": 1.0}, ("MOB",), "sewer", ("sewer",), 3),
        StageSpec("SPP", "Lay and joint external sewer pipes", {"sewer_pipe": 1.0}, ("SEX",), "sewer", ("sewer",), 4),
        StageSpec("SMH", "Construct sewer manholes", {"manhole": 1.0}, ("SPP",), "sewer", ("sewer", "general"), 4),
        StageSpec("SBF", "Sewer trench testing and backfill", {"backfill": 1.0}, ("SMH",), "sewer", ("sewer",), 3),
        StageSpec("TEX", "Storm-water trench excavation", {"earthwork": 1.0}, ("MOB",), "storm", ("storm",), 3),
        StageSpec("TPP", "Lay RCC storm-water pipes", {"storm_pipe": 1.0}, ("TEX",), "storm", ("storm",), 4),
        StageSpec("TMH", "Construct storm-water chambers", {"manhole": 1.0}, ("TPP",), "storm", ("storm",), 3),
        StageSpec("TBF", "Storm-water backfill", {"backfill": 1.0}, ("TMH",), "storm", ("storm",), 3),
        StageSpec("PEX", "Pressure-line trench excavation", {"earthwork": 1.0}, ("MOB",), "pressure", ("pressure",), 2),
        StageSpec("PPP", "Lay, joint and pressure-test HDPE pipe", {"pressure_pipe": 1.0}, ("PEX",), "pressure", ("pressure",), 4),
        StageSpec("PBF", "Pressure-line backfill", {"backfill": 1.0}, ("PPP",), "pressure", ("pressure",), 2),
        StageSpec("INT", "Internal drainage connections", {"internal_drainage": 1.0, "plumbing": 1.0}, ("MOB",), "internal", ("internal", "general"), 4),
        StageSpec("RST", "Road reinstatement", {"road_reinstatement": 1.0}, ("SBF", "TBF", "PBF"), minimum_days=4),
        StageSpec("TST", "Integrated testing and handover", {"testing": 1.0}, ("RST", "INT"), minimum_days=3),
    ]


def _rwh_template(count: int) -> list[StageSpec]:
    count = max(1, count)
    share = 1.0 / count
    specs = [StageSpec("MOB", "Mobilisation and setting out", {"preliminaries": 1.0}, minimum_days=2)]
    previous_pcc = "MOB"
    tank_finishes: list[str] = []
    for index in range(1, count + 1):
        prefix = f"T{index}"
        specs.extend(
            [
                StageSpec(f"{prefix}E", f"Tank {index}: excavation", {"earthwork": share}, (previous_pcc,), "tank", ("tank", "general"), 2),
                StageSpec(f"{prefix}P", f"Tank {index}: PCC base", {"pcc": share}, (f"{prefix}E",), "tank", ("tank", "general"), 1),
                StageSpec(
                    f"{prefix}R",
                    f"Tank {index}: RCC raft, walls and cover",
                    {"rcc": share, "reinforcement": share, "formwork": share},
                    (f"{prefix}P",),
                    "tank",
                    ("tank", "general"),
                    5,
                    7,
                    "Shared RCC crew; includes curing/strike allowance",
                ),
                StageSpec(f"{prefix}W", f"Tank {index}: waterproofing", {"waterproofing": share}, (f"{prefix}R",), "tank", ("tank", "general"), 2),
                StageSpec(
                    f"{prefix}F",
                    f"Tank {index}: finishes and backfill",
                    {"finishes": share, "masonry": share, "backfill": share},
                    (f"{prefix}W",),
                    "tank",
                    ("tank", "general"),
                    2,
                ),
            ]
        )
        previous_pcc = f"{prefix}P"  # crew-reuse offset without making complete tanks sequential
        tank_finishes.append(f"{prefix}F")
    specs.append(StageSpec("BOR", "Recharge borewell drilling and completion", {"borewell": 1.0}, ("MOB",), "borewell", ("borewell",), 4, assumption="Independent specialist drilling crew"))
    specs.append(StageSpec("TST", "System testing and commissioning", {"testing": 1.0, "plumbing": 1.0}, tuple(tank_finishes + ["BOR"]), minimum_days=3))
    return specs


def template_for(typology: str, config: ProjectConfig) -> list[StageSpec]:
    if typology == "stp_tank":
        return _stp_template()
    if typology == "linear_mep":
        return _linear_template()
    if typology == "rwh":
        return _rwh_template(config.structure_count)
    return _building_template()


def _track_matches(item: BOQItem, spec: StageSpec) -> bool:
    if not spec.accepted_tracks:
        return True
    return item.track in spec.accepted_tracks


def _activity_from_spec(spec: StageSpec, items: list[BOQItem], crew_multiplier: float) -> tuple[Activity, dict[str, float]]:
    cost = 0.0
    workload_days = 0.0
    raw_quantity = 0.0
    item_ids: list[str] = []
    applied: dict[str, float] = {}
    units: set[str] = set()
    productivities: list[float] = []
    for item in items:
        share = spec.allocations.get(item.work_package, 0.0)
        if share <= 0 or not _track_matches(item, spec):
            continue
        applied[item.id] = share
        cost += item.amount * share
        raw_quantity += item.quantity * share
        capacity = productivity_for(item.work_package, item.unit)
        # CPWD/DSR lift rows price deeper excavation bands; they are not extra
        # physical volume and must not be counted a second time for duration.
        if not any("Cost-only excavation lift surcharge" in flag for flag in item.flags):
            workload_days += (item.quantity * share) / max(capacity, 0.001)
        productivities.append(capacity)
        units.add(normalise_unit(item.unit))
        item_ids.append(item.id)
    productive_days = math.ceil(workload_days / max(crew_multiplier, 0.1)) if workload_days else 0
    duration = max(spec.minimum_days, productive_days) + spec.wait_days
    display_unit = next(iter(units)) if len(units) == 1 else ("mixed" if units else "allowance")
    productivity = sum(productivities) / len(productivities) if productivities else 0.0
    activity = Activity(
        id=spec.id,
        name=spec.name,
        work_package=" + ".join(spec.allocations) if spec.allocations else "milestone",
        track=spec.track,
        quantity=round(raw_quantity, 3),
        unit=display_unit,
        productivity_per_day=round(productivity, 3),
        duration_days=duration,
        predecessors=list(spec.predecessors),
        cost=round(cost, 2),
        boq_item_ids=item_ids,
        assumption=spec.assumption or "Published/site productivity default; calibrate before baseline approval",
    )
    return activity, applied


def build_activities(items: list[BOQItem], typology: str, config: ProjectConfig) -> list[Activity]:
    specs = template_for(typology, config)
    activities: list[Activity] = []
    allocated: dict[str, float] = defaultdict(float)
    for spec in specs:
        activity, applied = _activity_from_spec(spec, items, config.crew_multiplier)
        activities.append(activity)
        for item_id, share in applied.items():
            allocated[item_id] += share

    remaining = [(item, max(0.0, 1.0 - allocated[item.id])) for item in items if allocated[item.id] < 0.999]
    if remaining:
        terminal_ids = _terminal_ids(activities)
        cost = sum(item.amount * share for item, share in remaining)
        workdays = sum(
            0.0
            if any("Cost-only excavation lift surcharge" in flag for flag in item.flags)
            else (item.quantity * share) / productivity_for(item.work_package, item.unit)
            for item, share in remaining
        )
        duration = max(2, math.ceil(workdays / max(config.crew_multiplier, 0.1)))
        activities.append(
            Activity(
                id="OTH",
                name="Unmapped / miscellaneous BOQ work",
                work_package="mixed",
                track="general",
                quantity=round(sum(item.quantity * share for item, share in remaining), 3),
                unit="mixed",
                productivity_per_day=0.0,
                duration_days=duration,
                predecessors=terminal_ids,
                cost=round(cost, 2),
                boq_item_ids=[item.id for item, _ in remaining],
                assumption="Conservative terminal activity; planner must classify or reposition these items",
            )
        )

    terminal_ids = _terminal_ids(activities)
    activities.append(
        Activity(
            id="HND",
            name="Completion milestone",
            work_package="milestone",
            track="general",
            quantity=0,
            unit="milestone",
            productivity_per_day=0,
            duration_days=0,
            predecessors=terminal_ids,
            cost=0,
            assumption="All generated work tracks complete",
        )
    )
    return calculate_cpm(activities, config.start_date, config.workweek_days)


def _terminal_ids(activities: list[Activity]) -> list[str]:
    predecessors = {pred for activity in activities for pred in activity.predecessors}
    return [activity.id for activity in activities if activity.id not in predecessors]


def topological_order(activities: list[Activity]) -> list[str]:
    ids = {activity.id for activity in activities}
    successors: dict[str, list[str]] = defaultdict(list)
    indegree = {activity.id: 0 for activity in activities}
    for activity in activities:
        for predecessor in activity.predecessors:
            if predecessor not in ids:
                raise ValueError(f"Activity {activity.id} references missing predecessor {predecessor}")
            successors[predecessor].append(activity.id)
            indegree[activity.id] += 1
    queue = deque(activity_id for activity_id, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for successor in successors[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    if len(order) != len(activities):
        raise ValueError("The generated dependency network contains a cycle")
    return order


def calculate_cpm(activities: list[Activity], start_date: str | date, workweek_days: int = 6) -> list[Activity]:
    by_id = {activity.id: activity for activity in activities}
    order = topological_order(activities)
    successors: dict[str, list[str]] = defaultdict(list)
    for activity in activities:
        for predecessor in activity.predecessors:
            successors[predecessor].append(activity.id)

    for activity_id in order:
        activity = by_id[activity_id]
        activity.start_day = max((by_id[pred].finish_day for pred in activity.predecessors), default=0)
        activity.finish_day = activity.start_day + activity.duration_days
    project_finish = max((activity.finish_day for activity in activities), default=0)
    latest_start: dict[str, int] = {}
    for activity_id in reversed(order):
        activity = by_id[activity_id]
        latest_finish = min((latest_start[succ] for succ in successors[activity_id]), default=project_finish)
        latest_start[activity_id] = latest_finish - activity.duration_days
        activity.total_float_days = latest_start[activity_id] - activity.start_day
        activity.critical = activity.total_float_days == 0

    calendar_start = parse_date(start_date)
    for activity in activities:
        activity.start_date = working_day_at(calendar_start, activity.start_day, workweek_days).isoformat()
        finish_offset = activity.finish_day - 1 if activity.duration_days else activity.start_day
        activity.finish_date = working_day_at(calendar_start, finish_offset, workweek_days).isoformat()
    return sorted(activities, key=lambda activity: (activity.start_day, activity.id))


def apply_duration_overrides(
    activities: list[Activity], overrides: list[dict], start_date: str, workweek_days: int
) -> list[Activity]:
    by_id = {activity.id: activity for activity in activities}
    for override in overrides:
        activity_id = str(override.get("id", ""))
        if activity_id not in by_id:
            raise ValueError(f"Cannot override unknown activity {activity_id}")
        if "duration_days" in override:
            duration = int(override["duration_days"])
            if duration < 0:
                raise ValueError("Activity durations cannot be negative")
            by_id[activity_id].duration_days = duration
        if "predecessors" in override:
            by_id[activity_id].predecessors = [str(value) for value in override["predecessors"]]
        by_id[activity_id].assumption = "Planner override"
    return calculate_cpm(list(by_id.values()), start_date, workweek_days)
