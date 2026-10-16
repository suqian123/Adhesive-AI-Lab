"""Archive the failed polar CeO2(110) matrix and build a neutral 14-point matrix."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
from ase.io import read

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.campaign_runner import vasp_convergence_progress
from adhesive_ai.vasp_checkpoint import electronic_converged
from adhesive_ai.vasp_production import build_ceo2_model, validate_vasp_input_set, write_convergence_suite

VALIDATION = ROOT / "work" / "vasp_validation" / "ceo2-110-baseline-v1"
RESOURCES = ROOT / "work" / "vasp_resources.json"


def plane_composition(atoms):
    z = atoms.positions[:, 2]
    symbols = np.array(atoms.get_chemical_symbols())
    return [
        (int(np.count_nonzero(np.isclose(z, plane) & (symbols == "Ce"))),
         int(np.count_nonzero(np.isclose(z, plane) & (symbols == "O"))))
        for plane in np.unique(np.round(z, 6))
    ]


def preflight() -> dict:
    progress = vasp_convergence_progress(VALIDATION)
    if (progress.get("facet") != "(110)" or progress.get("total") != 14
            or progress.get("completed") != 0 or progress.get("failed") != 1
            or progress.get("active") or progress.get("paused") or progress.get("approved")):
        raise RuntimeError(f"Unexpected validation state: {progress}")
    if (VALIDATION / "approved.json").exists() or (VALIDATION / ".runner.lock").exists():
        raise RuntimeError("Old validation root is approved or locked")
    job = VALIDATION / "encut" / "450"
    status = json.loads((job / "run_status.json").read_text())
    if status.get("status") != "failed" or electronic_converged(job):
        raise RuntimeError(f"Old job is not a failed electronic calculation: {status}")
    manifest = json.loads((job / "input_manifest.json").read_text())
    if manifest.get("facet") != "(110)" or manifest.get("surface_termination") != "legacy-v1":
        raise RuntimeError(f"Old model identity changed: {manifest.get('surface_termination')}")
    old_planes = plane_composition(read(job / "POSCAR", format="vasp"))
    if old_planes[0] != (2, 2) or old_planes[-1] != (0, 2):
        raise RuntimeError(f"Old planes unexpectedly changed: {old_planes}")
    new, metadata = build_ceo2_model(
        "(110)", objective="surface-convergence", repeat_override=(1, 1, 1)
    )
    new_planes = plane_composition(new)
    if metadata["surface_termination"] != "neutral-CeO2-layers-v2" or any(
        plane != (2, 4) for plane in new_planes
    ):
        raise RuntimeError(f"New model is not neutral: {metadata}, {new_planes}")
    return {"ready": True, "old_planes": old_planes, "new_planes": new_planes,
            "old_job": str(job), "new_termination": metadata["surface_termination"]}


def apply() -> dict:
    checks = preflight()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = VALIDATION.with_name(VALIDATION.name + "-legacy-polar-" + stamp)
    if archive.exists():
        raise FileExistsError(archive)
    VALIDATION.rename(archive)
    try:
        plan = write_convergence_suite(VALIDATION, facet="(110)", resources=RESOURCES)
        if plan["job_count"] != 14 or len(plan["jobs"]) != 14:
            raise RuntimeError("New validation matrix has the wrong size")
        for job in plan["jobs"]:
            job_path = Path(job["path"])
            validation = validate_vasp_input_set(job_path, resources=RESOURCES)
            if not validation["valid"]:
                raise RuntimeError(f"Invalid new input: {job_path}, {validation}")
            if any(job_path.joinpath(name).exists() for name in ("CHGCAR", "WAVECAR", "run_status.json")):
                raise RuntimeError(f"Old computation contaminated new matrix: {job_path}")
        record = {"created_at": datetime.now(timezone.utc).isoformat(),
                  "reason": "Old CeO2(110) slab had charged outer planes and failed SCF twice.",
                  "old_root": str(archive), "new_root": str(VALIDATION),
                  "old_planes": checks["old_planes"], "new_planes": checks["new_planes"],
                  "scientific_status": "pending-convergence-validation"}
        (VALIDATION / "model_rebuild.json").write_text(json.dumps(record, indent=2) + "\n")
    except Exception:
        failed = VALIDATION.with_name(VALIDATION.name + "-failed-rebuild-" + stamp)
        if VALIDATION.exists():
            VALIDATION.rename(failed)
        archive.rename(VALIDATION)
        raise
    return {"rebuilt": True, "archive": str(archive), "validation_root": str(VALIDATION),
            "job_count": 14, "new_termination": checks["new_termination"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply() if args.apply else preflight(), ensure_ascii=False))
