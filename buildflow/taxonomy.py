from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .models import BOQItem


@dataclass(frozen=True)
class Taxon:
    key: str
    phase: str
    keywords: tuple[str, ...]
    examples: tuple[str, ...]


TAXONOMY: tuple[Taxon, ...] = (
    Taxon("preliminaries", "preconstruction", ("mobilisation", "mobilization", "site office", "barricading", "survey"), ("site mobilisation and setting out",)),
    Taxon("earthwork", "substructure", ("excavation", "earthwork", "excavate", "disposal of soil", "soil excavation"), ("earth work excavation in foundation trenches", "mechanical excavation and disposal")),
    Taxon("dewatering_shoring", "substructure", ("dewatering", "sheet pile", "shoring", "strutting", "well point"), ("dewatering excavation using pumps", "temporary shoring to deep excavation")),
    Taxon("pcc", "substructure", ("plain cement concrete", "pcc", "lean concrete", "blinding concrete"), ("providing pcc below foundations", "lean concrete blinding layer")),
    Taxon("rcc", "structure", ("reinforced cement concrete", "rcc", "m20 concrete", "m25 concrete", "m30 concrete", "design mix concrete"), ("reinforced cement concrete in raft", "rcc walls and roof slab")),
    Taxon("reinforcement", "structure", ("reinforcement steel", "tmt", "steel reinforcement", "thermo mechanically treated", "bar bending"), ("tmt reinforcement bars for rcc work",)),
    Taxon("formwork", "structure", ("formwork", "shuttering", "centering", "centering and shuttering"), ("formwork to walls slabs and foundations",)),
    Taxon("masonry", "superstructure", ("brick work", "brickwork", "blockwork", "aac block", "masonry"), ("brick masonry in cement mortar",)),
    Taxon("waterproofing", "envelope", ("waterproof", "water proof", "membrane", "sbr coating", "chemical coating"), ("waterproofing treatment to tank", "membrane waterproofing")),
    Taxon("sewer_pipe", "services", ("sewerage", "sewer line", "dwc pipe", "upvc sewer", "house connection"), ("laying dwc sewer pipe in trench", "external sewerage network")),
    Taxon("storm_pipe", "services", ("storm water", "stormwater", "rcc pipe", "np3 pipe", "np4 pipe"), ("laying rcc np3 storm water pipe",)),
    Taxon("pressure_pipe", "services", ("hdpe pipe", "pressure pipe", "water supply", "pumping main"), ("laying jointing and testing hdpe pressure pipe",)),
    Taxon("internal_drainage", "services", ("internal drainage", "soil waste pipe", "swr pipe", "floor trap", "nahani trap"), ("internal drainage using swr pipes",)),
    Taxon("manhole", "services", ("manhole", "inspection chamber", "catch basin", "gully chamber"), ("constructing brick masonry manhole",)),
    Taxon("borewell", "specialist", ("borewell", "bore well", "bore hole", "drilling", "recharge well"), ("drilling borewell to 30 metre depth", "recharge bore with slotted pipe")),
    Taxon("plumbing", "services", ("plumbing", "sanitary", "cpvc", "water closet", "wash basin"), ("internal plumbing and sanitary fixtures",)),
    Taxon("electrical", "services", ("electrical", "cable", "conduit", "earthing", "panel", "transformer"), ("electrical cables panels and earthing",)),
    Taxon("mechanical_equipment", "equipment", ("pump", "blower", "motor", "screen", "equipment", "stp machinery"), ("supply installation and testing of pumps and blowers",)),
    Taxon("finishes", "finishes", ("plaster", "painting", "paint", "tile", "flooring", "kota stone", "texture", "door", "window"), ("internal plaster paint and floor tiles",)),
    Taxon("backfill", "substructure", ("backfill", "back filling", "filling available excavated earth", "refilling"), ("backfilling around structure in layers", "refilling pipe trench")),
    Taxon("road_reinstatement", "external", ("road restoration", "reinstatement", "bituminous", "granular sub base", "gsb", "wmm"), ("road reinstatement after pipe laying",)),
    Taxon("testing", "commissioning", ("testing", "commissioning", "leak test", "hydrostatic", "trial run"), ("testing and commissioning of the completed system",)),
)


TRACK_HINTS = {
    "sewer": ("sewer", "dwc", "house connection"),
    "storm": ("storm", "np3", "np4", "rcc pipe"),
    "pressure": ("hdpe", "pressure pipe", "water supply", "pumping main"),
    "internal": ("internal drainage", "swr", "soil waste", "floor trap"),
    "borewell": ("borewell", "bore well", "recharge well", "drilling"),
    "tank": ("tank", "reservoir", "sump"),
}


TYPOLOGY_HINTS = {
    "stp_tank": {
        "sewage treatment plant": 6,
        "stp": 5,
        "tank": 2,
        "raft": 2,
        "water retaining": 3,
        "blower": 2,
        "dewatering": 1,
    },
    "linear_mep": {
        "road redevelopment": 3,
        "sewer": 3,
        "storm water": 3,
        "manhole": 2,
        "dwc": 2,
        "hdpe": 2,
        "pipe": 1,
    },
    "rwh": {
        "rain water harvesting": 8,
        "rainwater harvesting": 8,
        "rwh": 6,
        "recharge": 4,
        "borewell": 5,
        "bore well": 5,
    },
    "building": {
        "building": 3,
        "apartment": 4,
        "floor": 2,
        "masonry": 1,
        "brick work": 1,
        "door": 1,
        "window": 1,
    },
}

# BOQ descriptions often include both the network and the operation (for example,
# "backfilling sewer trench"). The operation governs scheduling, so these action
# phrases receive a stronger score than contextual words such as "sewer".
ACTION_PATTERNS = {
    "dewatering_shoring": ("dewatering", "shoring", "sheet pile"),
    "backfill": ("backfill", "back filling", "backfilling", "refilling"),
    "earthwork": ("excavation", "excavate", "earth work", "extra lift", "lead surcharge"),
    "pcc": ("plain cement concrete", "pcc", "blinding"),
    "reinforcement": ("reinforcement", "tmt steel", "bar bending"),
    "formwork": ("formwork", "shuttering", "centering"),
    "waterproofing": ("waterproof", "water proof", "membrane"),
    "manhole": ("manhole", "inspection chamber", "catch basin"),
    "borewell": ("borewell", "bore well", "bore hole", "drilling"),
    "road_reinstatement": ("road restoration", "road reinstatement", "granular sub base"),
}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _phrase_score(text: str, keywords: tuple[str, ...]) -> tuple[float, list[str]]:
    matches = [keyword for keyword in keywords if keyword in text]
    if not matches:
        return 0.0, []
    score = sum(1.0 + 0.18 * max(0, len(_tokens(match)) - 1) for match in matches)
    return score, matches


def _cosine_similarity(left: str, right: str) -> float:
    a, b = Counter(_tokens(left)), Counter(_tokens(right))
    if not a or not b:
        return 0.0
    dot = sum(a[token] * b[token] for token in a.keys() & b.keys())
    norm_a = math.sqrt(sum(value * value for value in a.values()))
    norm_b = math.sqrt(sum(value * value for value in b.values()))
    return dot / (norm_a * norm_b)


def classify_item(item: BOQItem, mode: str = "hybrid") -> BOQItem:
    text = item.description.lower()
    ranked: list[tuple[float, Taxon, list[str], float]] = []
    for taxon in TAXONOMY:
        rule_score, matches = _phrase_score(text, taxon.keywords)
        if any(pattern in text for pattern in ACTION_PATTERNS.get(taxon.key, ())):
            rule_score += 3.0
        retrieval_score = max((_cosine_similarity(text, example) for example in taxon.examples), default=0.0)
        if mode == "rules":
            score = rule_score
        elif mode == "retrieval":
            score = retrieval_score * 2.5
        else:
            score = rule_score + retrieval_score * 1.25
        ranked.append((score, taxon, matches, retrieval_score))
    ranked.sort(key=lambda result: result[0], reverse=True)
    best_score, best, matches, retrieval_score = ranked[0]
    runner_up = ranked[1][0]
    if best_score <= 0:
        item.work_package = "unknown"
        item.phase = "unclassified"
        item.confidence = 0.0
        item.classifier = mode
        item.evidence = []
    else:
        margin = best_score - runner_up
        confidence = min(0.98, 0.48 + 0.12 * best_score + 0.08 * margin)
        item.work_package = best.key
        item.phase = best.phase
        item.confidence = round(confidence, 3)
        item.classifier = mode
        item.evidence = matches[:3]
        if retrieval_score >= 0.45:
            item.evidence.append(f"retrieved example {retrieval_score:.2f}")
    if item.work_package == "earthwork" and "extra lift" in text:
        item.flags.append("Cost-only excavation lift surcharge; quantity is not added again to production workload")
        item.evidence.append("extra-lift surcharge")
    item.track = infer_track(text, item.work_package)
    return item


def infer_track(text: str, work_package: str = "") -> str:
    if work_package == "sewer_pipe":
        return "sewer"
    if work_package == "storm_pipe":
        return "storm"
    if work_package == "pressure_pipe":
        return "pressure"
    if work_package == "internal_drainage":
        return "internal"
    if work_package == "borewell":
        return "borewell"
    scores = {track: sum(1 for hint in hints if hint in text) for track, hints in TRACK_HINTS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "general"


def classify_items(items: list[BOQItem], mode: str = "hybrid") -> list[BOQItem]:
    safe_mode = mode if mode in {"rules", "retrieval", "hybrid"} else "hybrid"
    return [classify_item(item, safe_mode) for item in items]


def infer_typology(items: list[BOQItem], project_name: str = "") -> tuple[str, float, dict[str, float]]:
    corpus = " ".join([project_name] + [item.description for item in items]).lower()
    scores: dict[str, float] = {}
    for typology, hints in TYPOLOGY_HINTS.items():
        scores[typology] = float(sum(weight for phrase, weight in hints.items() if phrase in corpus))
    # A borewell plus RCC tank is more specific than a generic linear pipe network.
    if "borewell" in corpus or "bore well" in corpus:
        scores["rwh"] += 2.0
    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    best, best_score = ranked[0]
    second_score = ranked[1][1]
    if best_score <= 0:
        return "building", 0.35, scores
    confidence = min(0.98, 0.52 + 0.035 * best_score + 0.02 * (best_score - second_score))
    return best, round(confidence, 3), scores
