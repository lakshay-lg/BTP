from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent import ProjectAgent
from .exporter import export_xlsx
from .ingestion import read_boq
from .models import ProjectConfig


def main() -> None:
    parser = argparse.ArgumentParser(prog="buildflow", description="Auditable BOQ-to-schedule/cash-flow prototype")
    parser.add_argument("boq", help="Path to .csv or .xlsx BOQ")
    parser.add_argument("--name", default="BOQ analysis")
    parser.add_argument("--typology", choices=["auto", "building", "stp_tank", "linear_mep", "rwh"], default="auto")
    parser.add_argument("--start", default="2026-04-25")
    parser.add_argument("--contract-days", type=int)
    parser.add_argument("--structures", type=int, default=1)
    parser.add_argument("--classifier", choices=["rules", "retrieval", "hybrid"], default="hybrid")
    parser.add_argument("--llm", action="store_true", help="Opt in to sending ambiguous descriptions to the Groq API (needs GROQ_API_KEY)")
    parser.add_argument("--llm-model", help="Groq model id (default: $BUILDFLOW_LLM_MODEL or openai/gpt-oss-120b)")
    parser.add_argument("--output", default="buildflow_analysis.xlsx")
    parser.add_argument("--json", action="store_true", help="Also print result JSON")
    args = parser.parse_args()
    config = ProjectConfig(
        name=args.name,
        typology=args.typology,
        start_date=args.start,
        contract_duration_days=args.contract_days,
        structure_count=args.structures,
        classifier_mode=args.classifier,
        use_llm_fallback=args.llm,
        **({"llm_model": args.llm_model} if args.llm_model else {}),
    )
    result = ProjectAgent().run(read_boq(args.boq), config)
    Path(args.output).write_bytes(export_xlsx(result))
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    print(f"Created {args.output}: {result.metrics['item_count']} BOQ rows, {result.metrics['duration_working_days']} working days")


if __name__ == "__main__":
    main()
