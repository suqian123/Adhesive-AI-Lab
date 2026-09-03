"""Generate baseline CeO2/PDA VASP inputs from campaign task contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adhesive_ai.vasp_production import prepare_campaign_dft_task, write_convergence_suite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_paths", type=Path, nargs="*")
    parser.add_argument("--campaign-dir", type=Path)
    parser.add_argument("--validation-root", type=Path)
    parser.add_argument("--resources", type=Path, default=Path("work/vasp_resources.json"))
    args = parser.parse_args()
    task_paths = list(args.task_paths)
    if args.campaign_dir:
        task_paths.extend(sorted((args.campaign_dir / "tasks").glob("dft-*/task.json")))
    outputs = []
    for task_path in task_paths:
        task_file = task_path / "task.json" if task_path.is_dir() else task_path
        task = json.loads(task_file.read_text(encoding="utf-8"))
        if task.get("calculation_kind") != "dft":
            continue
        output_dir = task_file.parent
        outputs.append(prepare_campaign_dft_task(task, output_dir, resources=args.resources))
    validation = write_convergence_suite(args.validation_root, resources=args.resources) if args.validation_root else None
    print(json.dumps({"tasks": outputs, "validation": validation}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
