"""Statically validate generated VASP task inputs and licensed resources."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adhesive_ai.vasp_production import validate_vasp_input_set


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--resources", type=Path, default=Path("work/vasp_resources.json"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--run-record", type=Path, help="Update stale DFT blocker text without launching jobs")
    args = parser.parse_args()
    task_root = args.campaign_dir / "tasks"
    reports = [
        validate_vasp_input_set(path, resources=args.resources)
        for path in sorted(task_root.glob("dft-*"))
        if path.is_dir()
    ]
    payload = {
        "valid": bool(reports) and all(report["valid"] for report in reports),
        "task_count": len(reports), "reports": reports,
        "scientific_status": "static-input-valid; pending-convergence-validation",
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.run_record and payload["valid"]:
        record = json.loads(args.run_record.read_text(encoding="utf-8"))
        valid_task_ids = {Path(report["directory"]).name for report in reports if report["valid"]}
        for task in record.get("tasks", []):
            if task.get("task_id") in valid_task_ids:
                task["status"] = "blocked"
                task["blocker"] = "输入已生成并通过静态验证；等待收敛计算批准"
                task["input_validation"] = "static-valid; pending-convergence-approval"
        record["status"] = "blocked"
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        args.run_record.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"valid": payload["valid"], "task_count": payload["task_count"]}, ensure_ascii=False))
    return 0 if payload["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
