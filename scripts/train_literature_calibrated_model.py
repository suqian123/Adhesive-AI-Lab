"""Train and archive a model calibrated with traceable literature results."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.database import (
    initialize_schema, load_candidates, load_experiments, load_literature_candidates, load_literature_results, save_model_version,
)
from adhesive_ai.screening import save_model, train_screening_models


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Optional explicit version name")
    arguments = parser.parse_args()
    initialize_schema()
    candidates = load_candidates()
    literature_candidates = load_literature_candidates()
    if literature_candidates.empty:
        raise SystemExit("No registered literature formulations are available; import a validated CSV first.")
    candidates = pd.concat([candidates, literature_candidates], ignore_index=True, sort=False)
    candidate_ids = candidates["candidate_id"].astype(str).tolist()
    formulations = dict(zip(candidate_ids, candidates["formulation_id"].astype(str)))
    literature = load_literature_results(candidate_ids, formulation_ids=formulations)
    if literature.empty:
        raise SystemExit("No matched literature rows are available; import a validated CSV first.")
    experiments = load_experiments(candidate_ids, formulation_ids=formulations)
    version = arguments.version or f"literature-v1-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    model = train_screening_models(candidates, experiments=experiments, literature=literature, version=version)
    artifact = save_model(model, ROOT / "work" / "models" / f"{version}.npz")
    save_model_version(model, str(artifact.relative_to(ROOT)))
    print(f"Archived {model.version}: literature_rows={model.literature_rows}, experimental_rows={model.experimental_rows}")
    print(f"Artifact: {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
