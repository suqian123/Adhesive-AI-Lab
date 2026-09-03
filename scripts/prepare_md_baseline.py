from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adhesive_ai.md_production import prepare_md_structure_baseline


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the versioned ODPA-ODA/catechol/PDBA MD baseline")
    parser.add_argument("--output", default="work/md_baselines/odpa-oda-catechol-pdba-v1")
    args = parser.parse_args()
    result = prepare_md_structure_baseline(args.output)
    print(json.dumps(result["manifest"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

