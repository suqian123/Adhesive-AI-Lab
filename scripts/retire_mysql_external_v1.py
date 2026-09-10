"""Back up and retire the known-invalid MySQL ``external-v1`` model snapshot.

The default is read-only. ``--apply`` affects only the ``external-v1`` row in
``model_versions`` and ``simulation_results`` rows that reference it. Candidate
definitions and all other model and simulation versions are retained.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.database import connection


VERSION = "external-v1"


def snapshot() -> dict[str, object]:
    with connection() as database:
        cursor = database.cursor(dictionary=True)
        try:
            cursor.execute("SELECT model_version, metadata, artifact_path, created_at FROM model_versions WHERE model_version = %s", (VERSION,))
            model = cursor.fetchone()
            cursor.execute(
                "SELECT id, candidate_id, formulation_id, candidate_library_version, model_version, qchem, md, interface_data, predictions, multi_objective_score, screening_class, created_at "
                "FROM simulation_results WHERE model_version = %s ORDER BY id",
                (VERSION,),
            )
            simulations = cursor.fetchall()
        finally:
            cursor.close()
    artifact = artifact_source(model) if model else None
    return {
        "model": model,
        "simulation_results": simulations,
        "artifact_exists": bool(artifact and artifact.is_file()),
    }


def artifact_source(model: dict[str, object] | None) -> Path | None:
    if not model or not model.get("artifact_path"):
        return None
    source = (ROOT / str(model["artifact_path"])).resolve()
    models_root = (ROOT / "work" / "models").resolve()
    if source != models_root and models_root not in source.parents:
        raise RuntimeError(f"Refusing to move artifact outside work/models: {source}")
    return source


def inspect() -> dict[str, object]:
    state = snapshot()
    model = state["model"]
    return {
        "version": VERSION,
        "model_present": bool(model),
        "artifact_path": model.get("artifact_path") if model else None,
        "artifact_exists": state["artifact_exists"],
        "simulation_count": len(state["simulation_results"]),
        "simulation_ids": [row["id"] for row in state["simulation_results"]],
    }


def retire() -> dict[str, object]:
    state = snapshot()
    model = state["model"]
    simulations = state["simulation_results"]
    if not model:
        raise RuntimeError(f"No {VERSION} model-version record exists")
    if not simulations:
        raise RuntimeError(f"No simulation_results rows reference {VERSION}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = ROOT / "work" / "experimental_backups" / f"mysql-{VERSION}-retired-{timestamp}"
    backup.mkdir(parents=True, exist_ok=False)
    (backup / "snapshot.json").write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    moved_artifact = None
    source = artifact_source(model)
    if source and source.is_file():
        artifacts = backup / "artifacts"
        artifacts.mkdir()
        destination = artifacts / source.name
        shutil.move(str(source), str(destination))
        moved_artifact = str(destination)

    with connection() as database:
        cursor = database.cursor()
        try:
            simulation_ids = [int(row["id"]) for row in simulations]
            placeholders = ",".join(["%s"] * len(simulation_ids))
            cursor.execute(f"DELETE FROM simulation_results WHERE id IN ({placeholders}) AND model_version = %s", (*simulation_ids, VERSION))
            deleted_simulations = cursor.rowcount
            cursor.execute("DELETE FROM model_versions WHERE model_version = %s", (VERSION,))
            deleted_models = cursor.rowcount
            cursor.execute("SELECT COUNT(*) FROM simulation_results WHERE model_version = %s", (VERSION,))
            remaining_simulations = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM model_versions WHERE model_version = %s", (VERSION,))
            remaining_models = int(cursor.fetchone()[0])
        finally:
            cursor.close()
    if remaining_simulations or remaining_models:
        raise RuntimeError("Retirement verification failed")
    return {
        "version": VERSION,
        "deleted_model_rows": deleted_models,
        "deleted_simulation_rows": deleted_simulations,
        "remaining_model_rows": remaining_models,
        "remaining_simulation_rows": remaining_simulations,
        "backup": str(backup),
        "moved_artifact": moved_artifact,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up and retire external-v1 and its linked simulation rows")
    arguments = parser.parse_args()
    result = retire() if arguments.apply else inspect()
    print(json.dumps({"applied": arguments.apply, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
