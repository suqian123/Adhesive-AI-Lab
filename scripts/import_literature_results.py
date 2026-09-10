"""Validate and import a traceable literature-data CSV into MySQL.

Required columns: doi, sample_label, source_location, data_license, and at least
one property column. Add formulation descriptors such as resin, dynamic_unit,
filler_type, filler_pct, and crosslink_density where reported. ``conditions`` is
optional JSON; ``test_temperature_c`` is merged into it when provided.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.database import initialize_schema, register_literature_candidates, save_literature_results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="UTF-8 literature-data CSV")
    arguments = parser.parse_args()
    frame = pd.read_csv(arguments.csv)
    initialize_schema()
    candidates = register_literature_candidates(frame)
    prepared = frame.copy()
    prepared[["candidate_id", "formulation_id", "candidate_library_version"]] = candidates[["candidate_id", "formulation_id", "candidate_library_version"]]
    formulations = dict(zip(candidates["candidate_id"].astype(str), candidates["formulation_id"].astype(str)))
    versions = dict(zip(candidates["candidate_id"].astype(str), candidates["candidate_library_version"].astype(str)))
    imported = save_literature_results(prepared, candidate_formulations=formulations, candidate_library_versions=versions)
    print(f"Imported {imported} literature rows from {arguments.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
