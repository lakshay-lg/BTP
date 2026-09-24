from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from .models import BOQItem
from .taxonomy import classify_items


def evaluate(rows: list[dict[str, str]], mode: str) -> dict:
    items = [BOQItem(f"B-{index:03d}", row["description"], row.get("unit", ""), 1, 1, 1) for index, row in enumerate(rows, 1)]
    classify_items(items, mode)
    expected = [row["expected_package"] for row in rows]
    predicted = [item.work_package for item in items]
    labels = sorted(set(expected) | set(predicted))
    f1_scores = []
    for label in labels:
        true_positive = sum(e == label and p == label for e, p in zip(expected, predicted))
        false_positive = sum(e != label and p == label for e, p in zip(expected, predicted))
        false_negative = sum(e == label and p != label for e, p in zip(expected, predicted))
        precision = true_positive / max(1, true_positive + false_positive)
        recall = true_positive / max(1, true_positive + false_negative)
        f1_scores.append(2 * precision * recall / max(precision + recall, 1e-9))
    mistakes = [
        {"description": row["description"], "expected": truth, "predicted": prediction}
        for row, truth, prediction in zip(rows, expected, predicted)
        if truth != prediction
    ]
    return {
        "mode": mode,
        "rows": len(rows),
        "accuracy": round(sum(e == p for e, p in zip(expected, predicted)) / max(len(rows), 1), 4),
        "macro_f1": round(sum(f1_scores) / max(len(f1_scores), 1), 4),
        "unknown_rate": round(sum(p == "unknown" for p in predicted) / max(len(rows), 1), 4),
        "mean_confidence": round(sum(item.confidence for item in items) / max(len(items), 1), 4),
        "mistakes": mistakes,
    }


def run_benchmark(path: str | Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    required = {"description", "expected_package"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Benchmark CSV requires description and expected_package columns")
    return [evaluate(rows, mode) for mode in ("rules", "retrieval", "hybrid")]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run BOQ reader ablation benchmark")
    parser.add_argument("labels", nargs="?", default="data/benchmark_labels.csv")
    parser.add_argument("--output", help="Optional path for the JSON report")
    args = parser.parse_args()
    results = run_benchmark(args.labels)
    report = json.dumps(results, indent=2)
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
