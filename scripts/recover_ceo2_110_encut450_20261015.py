"""Archive failed CeO2(110) ENCUT=450 SCF and retry from its verified DFT+U seed."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
from adhesive_ai.campaign_runner import (
    _ensure_no_vasp_process_is_running,
    _update_incar_settings,
    restart_vasp_convergence,
    vasp_convergence_progress,
)
from adhesive_ai.vasp_checkpoint import electronic_converged
from adhesive_ai.vasp_production import validate_vasp_input_set


VALIDATION = ROOT / "work" / "vasp_validation" / "ceo2-110-baseline-v1"
JOB = VALIDATION / "encut" / "450"
STAGE = JOB / ".model-preconverge" / "step3-dftu"
RESOURCE_CONFIG = ROOT / "work" / "vasp_resources.json"
MUTABLE = (
    "CHG", "CHGCAR", "CONTCAR", "DOSCAR", "EIGENVAL", "IBZKPT", "OSZICAR",
    "OUTCAR", "PCDAT", "PROCAR", "REPORT", "WAVECAR", "XDATCAR", "vasprun.xml",
    "vasp.stdout.log", "vasp.stderr.log", "run_status.json",
)
SETTINGS = {
    "ALGO": "All", "TIME": "0.05", "ISEARCH": "1", "NELM": "240",
    "NELMDL": "-5", "AMIX": "0.05", "BMIX": "0.0001",
    "AMIX_MAG": "0.2", "BMIX_MAG": "0.0001", "AMIN": "0.01",
    "LREAL": ".FALSE.", "LWAVE": ".TRUE.", "LCHARG": ".TRUE.",
    "ISTART": "1", "ICHARG": "1",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def preflight() -> dict:
    plan = read_json(VALIDATION / "validation_plan.json")
    declared = {Path(str(item["path"])).resolve() for item in plan["jobs"]}
    if plan.get("facet") != "(110)" or JOB.resolve() not in declared:
        raise RuntimeError("Unexpected validation plan")
    progress = vasp_convergence_progress(VALIDATION)
    if (progress.get("active") or progress.get("approved") or progress.get("completed") != 0
            or progress.get("failed") != 1 or progress.get("total") != 14):
        raise RuntimeError(f"Validation state changed: {progress}")
    status = read_json(JOB / "run_status.json")
    if status.get("status") != "failed" or status.get("complete") is not False:
        raise RuntimeError(f"Unexpected failed job status: {status}")
    if (JOB / "scf_recovery.json").exists() or electronic_converged(JOB):
        raise RuntimeError("Job is already recovered or converged")
    incar = (JOB / "INCAR").read_text(encoding="utf-8")
    for required in ("ALGO = Normal", "NELM = 160", "ENCUT = 450", "EDIFF = 1E-6", "LDIPOL = .TRUE."):
        if required not in incar:
            raise RuntimeError(f"Original INCAR changed: {required}")
    outcar = (JOB / "OUTCAR").read_text(encoding="utf-8", errors="replace")
    if "electronic self-consistency was not achieved" not in outcar:
        raise RuntimeError("Failure is not a documented NELM exhaustion")
    stage_status = read_json(STAGE / "run_status.json")
    model_status = read_json(JOB / ".model-preconverge" / "run_status.json")
    if (not stage_status.get("complete") or not model_status.get("complete")
            or not electronic_converged(STAGE)):
        raise RuntimeError("DFT+U stage is not independently converged")
    hashes = {}
    for name in ("POSCAR", "POTCAR", "KPOINTS"):
        original, seed = JOB / name, STAGE / name
        if not original.is_file() or not seed.is_file() or sha256(original) != sha256(seed):
            raise RuntimeError(f"Validated seed input mismatch: {name}")
        hashes[name] = sha256(original)
    seed_hashes = {}
    for name in ("CHGCAR", "WAVECAR"):
        path = STAGE / name
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f"Verified seed missing: {name}")
        seed_hashes[name] = sha256(path)
    validation = validate_vasp_input_set(JOB, resources=RESOURCE_CONFIG)
    if not validation["valid"]:
        raise RuntimeError(f"Static inputs invalid: {validation}")
    _ensure_no_vasp_process_is_running()
    return {"ready": True, "job": str(JOB), "stage": str(STAGE),
            "physical_sha256": hashes, "verified_seed_sha256": seed_hashes,
            "settings": SETTINGS}


def apply() -> dict:
    load_dotenv(ROOT / ".env", override=True)
    checks = preflight()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = JOB / ".scf-recovery-backups" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(JOB / "INCAR", backup / "INCAR.before")
    for name in ("runner_control.json", "status.tsv"):
        source = VALIDATION / name
        if source.is_file():
            shutil.copy2(source, backup / name)
    moved = []
    for name in MUTABLE:
        source = JOB / name
        if source.is_file():
            shutil.move(str(source), str(backup / name))
            moved.append(name)
    _update_incar_settings(JOB / "INCAR", SETTINGS)
    for name, expected in checks["physical_sha256"].items():
        if sha256(JOB / name) != expected:
            raise RuntimeError(f"Physical input changed: {name}")
    for name, expected in checks["verified_seed_sha256"].items():
        if sha256(STAGE / name) != expected:
            raise RuntimeError(f"Verified seed changed: {name}")
    if not validate_vasp_input_set(JOB, resources=RESOURCE_CONFIG)["valid"]:
        raise RuntimeError("Static input validation failed after INCAR update")
    now = datetime.now(timezone.utc).isoformat()
    marker = {
        "state": "prepared-model-preconvergence", "attempt": 1,
        "job": "encut/450", "prepared_at": now,
        "basis": "Retry from independently converged DFT+U preconvergence output with a direct optimizer.",
        "checkpoint_policy": "Failed top-level CHGCAR/WAVECAR/CONTCAR archived; only verified stage3 seed may be reused.",
        "backup_directory": str(backup), "archived_files": moved,
        "settings": SETTINGS, "physical_input_sha256": checks["physical_sha256"],
        "verified_seed_sha256": checks["verified_seed_sha256"],
    }
    (JOB / "scf_recovery.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (JOB / "run_status.json").write_text(json.dumps({
        "job": "encut/450", "status": "recovery-prepared", "complete": False,
        "prepared_at": now,
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    (VALIDATION / "runner_control.json").write_text(json.dumps({
        "state": "recovery-prepared", "job": "encut/450", "updated_at": now,
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    started = restart_vasp_convergence(VALIDATION, facet="(110)")
    if not started.get("started"):
        raise RuntimeError(f"Validation runner did not start: {started}")
    return {"started": True, "backup_directory": str(backup), "settings": SETTINGS,
            "seed": str(STAGE), "runner_pid": started.get("runner_pid")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=True)
    print(json.dumps(apply() if args.apply else preflight(), ensure_ascii=False))
