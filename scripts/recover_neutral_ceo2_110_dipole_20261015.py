"""Archive divergent neutral-(110) final SCF and prepare a stable seeded retry."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.campaign_runner import _update_incar_settings, vasp_convergence_progress
from adhesive_ai.vasp_checkpoint import electronic_converged
from adhesive_ai.vasp_production import validate_vasp_input_set

VALIDATION = ROOT / "work" / "vasp_validation" / "ceo2-110-baseline-v1"
JOB = VALIDATION / "encut" / "450"
SEED = JOB / ".model-preconverge" / "step3-dftu"
RESOURCES = ROOT / "work" / "vasp_resources.json"
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
    "LDIPOL": ".TRUE.", "IDIPOL": "3", "DIPOL": "0.5 0.5 0.5",
    "ISTART": "1", "ICHARG": "1",
}


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def preflight() -> dict:
    progress = vasp_convergence_progress(VALIDATION)
    if (progress.get("facet") != "(110)" or progress.get("total") != 14
            or progress.get("completed") != 0 or progress.get("failed") != 0
            or progress.get("active") or progress.get("runner_state") != "cancelled"):
        raise RuntimeError(f"Unexpected stopped validation state: {progress}")
    if (VALIDATION / ".runner.lock").exists():
        raise RuntimeError("Validation runner lock remains active")
    record = json.loads((VALIDATION / "model_rebuild.json").read_text())
    if record.get("scientific_status") != "pending-convergence-validation":
        raise RuntimeError("This is not the neutral model")
    manifest = json.loads((JOB / "input_manifest.json").read_text())
    if manifest.get("surface_termination") != "neutral-CeO2-layers-v2":
        raise RuntimeError("Unexpected model termination")
    status = json.loads((JOB / "run_status.json").read_text())
    if status.get("status") != "running" or electronic_converged(JOB):
        raise RuntimeError(f"Unexpected formal-job status: {status}")
    incar = (JOB / "INCAR").read_text()
    for tag in ("ALGO = Normal", "NELM = 160", "EDIFF = 1E-6", "LDIPOL = .TRUE."):
        if tag not in incar:
            raise RuntimeError(f"Original final setting changed: {tag}")
    outcar = (JOB / "OUTCAR").read_text(errors="replace")
    dipoles = [abs(float(value)) for value in re.findall(
        r"(?m)^\s*dipolmoment\s+[-+0-9.Ee]+\s+[-+0-9.Ee]+\s+([-+0-9.Ee]+)", outcar
    )]
    if not dipoles or max(dipoles) < 100:
        raise RuntimeError(f"Divergent dipole signature missing: {dipoles[-5:]}")
    seed_status = json.loads((SEED / "run_status.json").read_text())
    if not seed_status.get("complete") or not electronic_converged(SEED):
        raise RuntimeError("DFT+U seed is not independently converged")
    for name in ("POSCAR", "POTCAR", "KPOINTS"):
        if digest(JOB / name) != digest(SEED / name):
            raise RuntimeError(f"Seed physical input mismatch: {name}")
    for name in ("CHGCAR", "WAVECAR"):
        if not (SEED / name).is_file() or not (SEED / name).stat().st_size:
            raise RuntimeError(f"Verified seed missing {name}")
    plan = json.loads((VALIDATION / "validation_plan.json").read_text())
    jobs = [Path(item["path"]) for item in plan["jobs"]]
    if len(jobs) != 14 or JOB not in jobs:
        raise RuntimeError("Unexpected validation job list")
    if any((path / "run_status.json").exists() for path in jobs if path != JOB):
        raise RuntimeError("Another validation point has already started")
    return {"ready": True, "max_divergent_dipole_ea": max(dipoles),
            "seed": str(SEED), "jobs": jobs,
            "physical_hashes": {name: digest(JOB / name) for name in ("POSCAR", "POTCAR", "KPOINTS")}}


def apply() -> dict:
    checks = preflight()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = JOB / ".scf-recovery-backups" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(JOB / "INCAR", backup / "INCAR.before")
    for name in ("runner_control.json", "status.tsv"):
        path = VALIDATION / name
        if path.is_file(): shutil.copy2(path, backup / name)
    archived = []
    for name in MUTABLE:
        path = JOB / name
        if path.is_file():
            shutil.move(str(path), str(backup / name))
            archived.append(name)
    for path in checks["jobs"]:
        _update_incar_settings(path / "INCAR", SETTINGS)
        validation = validate_vasp_input_set(path, resources=RESOURCES)
        if not validation["valid"]:
            raise RuntimeError(f"Invalid corrected input: {path}, {validation}")
    for name, expected in checks["physical_hashes"].items():
        if digest(JOB / name) != expected or digest(SEED / name) != expected:
            raise RuntimeError(f"Physical input changed: {name}")
    now = datetime.now(timezone.utc).isoformat()
    marker = {"state": "prepared-model-preconvergence", "attempt": 1,
              "model": "neutral-CeO2-layers-v2", "job": "encut/450",
              "prepared_at": now, "reason": "LDIPOL+Normal caused charge sloshing after a converged DFT+U bridge.",
              "backup_directory": str(backup), "archived_files": archived,
              "seed": str(SEED), "settings": SETTINGS,
              "physical_input_sha256": checks["physical_hashes"]}
    (JOB / "scf_recovery.json").write_text(json.dumps(marker, indent=2) + "\n")
    (JOB / "run_status.json").write_text(json.dumps({"job":"encut/450",
        "status":"recovery-prepared", "complete":False, "prepared_at":now}) + "\n")
    (VALIDATION / "runner_control.json").write_text(json.dumps({
        "state":"recovery-prepared", "job":"encut/450", "updated_at":now}) + "\n")
    return {"prepared": True, "backup": str(backup), "updated_jobs": 14,
            "seed": str(SEED), "max_divergent_dipole_ea": checks["max_divergent_dipole_ea"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply() if args.apply else preflight(), ensure_ascii=False, default=str))
