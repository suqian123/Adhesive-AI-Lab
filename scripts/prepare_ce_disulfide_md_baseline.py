"""Prepare the CL5-00016 CE/disulfide molecular precursor baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from adhesive_ai.ce_disulfide_baseline import prepare_baseline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="work/md_baselines/cl5-00016-badcy-dcdds-ctbn-v1",
    )
    args = parser.parse_args()
    result = prepare_baseline(args.output)
    print(json.dumps({
        "path": result["path"],
        "scientific_status": result["contract"]["scientific_status"],
        "production_approved": result["manifest"]["production_approved"],
        "finite_cell_proposal": result["contract"]["finite_cell_proposal"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
