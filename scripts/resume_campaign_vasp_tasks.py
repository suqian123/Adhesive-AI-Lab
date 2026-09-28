"""Resume approved VASP DFT tasks for one existing campaign run.

This is deliberately separate from starting a new campaign: it preserves the
candidate snapshot and formulation fingerprint captured in the original run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.campaign_runner import resume_approved_vasp_tasks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--campaign-root", type=Path, default=ROOT / "work" / "campaign_runs")
    parser.add_argument("--max-parallel", type=int, default=1)
    arguments = parser.parse_args()
    result = resume_approved_vasp_tasks(
        arguments.run_id,
        root=arguments.campaign_root,
        max_parallel=max(1, arguments.max_parallel),
    )
    tasks = result.get("tasks") or []
    print(json.dumps({
        "run_id": result.get("run_id"),
        "status": result.get("status"),
        "max_parallel": result.get("max_parallel"),
        "task_counts": {
            status: sum(task.get("status") == status for task in tasks)
            for status in sorted({str(task.get("status")) for task in tasks})
        },
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
