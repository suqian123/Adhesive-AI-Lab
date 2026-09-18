"""Prepare one fully clean, audited second SCF recovery for a failed VASP point.

The previous output is moved into a timestamped backup before any mutable output
is removed.  The subsequent runner must generate a fresh charge density through
its fixed-charge, PBE, and DFT+U preconvergence stages; no failed CHGCAR or
WAVECAR can seed the calculation.
"""

from __future__ import annotations

import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.campaign_runner import (
    VASP_RUNNER_CONTROL_FILENAME,
    VASP_RUNNER_PGID_FILENAME,
    VASP_RUNNER_PID_FILENAME,
    VASP_SCF_RECOVERY_FILENAME,
    _ensure_no_vasp_process_is_running,
    _read_json_file,
    _update_incar_settings,
    _write_json_file,
)
from adhesive_ai.vasp_checkpoint import electronic_converged


VALIDATION_ROOT = ROOT / "work" / "vasp_validation" / "ceo2-111-baseline-v1"
MUTABLE_OUTPUTS = (
    "CHG", "CHGCAR", "CONTCAR", "DOSCAR", "EIGENVAL", "IBZKPT", "OSZICAR",
    "OUTCAR", "PCDAT", "PROCAR", "REPORT", "WAVECAR", "XDATCAR", "vasprun.xml",
    "vasp.stdout.log", "vasp.stderr.log", "run_status.json", "scf_recovery.json",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", default="encut/600", help="failed validation job relative to the shared validation root")
    arguments = parser.parse_args()
    root = VALIDATION_ROOT.resolve()
    job_name = str(arguments.job).replace("\\", "/").strip("/")
    job = (root / job_name).resolve()
    if not job.is_relative_to(root):
        raise RuntimeError("Recovery target is outside the validation root.")
    if electronic_converged(job):
        raise RuntimeError("The target is already electronically converged; no recovery is allowed.")
    status = _read_json_file(job / "run_status.json")
    if status.get("status") != "failed":
        raise RuntimeError("The target is not a recorded failed VASP calculation.")
    prior = _read_json_file(job / VASP_SCF_RECOVERY_FILENAME)
    prior_attempt = int(prior.get("attempt") or 0)
    if prior.get("state") == "prepared-model-preconvergence":
        raise RuntimeError("A model-preconvergence recovery is already prepared; repeated parameter changes are blocked.")
    if prior and (prior.get("state") != "prepared-fresh-atomic" or prior_attempt != 1):
        raise RuntimeError("The existing recovery marker is not eligible for a clean model-preconvergence recovery.")
    _ensure_no_vasp_process_is_running()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = job / ".scf-second-recovery-backups" / timestamp
    backup.mkdir(parents=True, exist_ok=False)
    for name in MUTABLE_OUTPUTS:
        source = job / name
        if source.is_file():
            shutil.move(str(source), str(backup / name))
    stage = job / ".model-preconverge"
    if stage.exists():
        shutil.move(str(stage), str(backup / stage.name))
    incar = job / "INCAR"
    shutil.copy2(incar, backup / "INCAR.before-second-recovery")
    settings = {
        "ALGO": "Damped",
        "TIME": "0.4",
        "NELM": "240",
        "NELMDL": "-1",
        "AMIX": "0.02",
        "BMIX": "0.0001",
        "AMIX_MAG": "0.1",
        "BMIX_MAG": "0.0001",
        "AMIN": "0.01",
        "MAXMIX": "80",
        "ICHARG": "2",
        "ISTART": "0",
        "LREAL": ".FALSE.",
        "LWAVE": ".TRUE.",
        "LCHARG": ".TRUE.",
    }
    _update_incar_settings(incar, settings)
    _write_json_file(job / VASP_SCF_RECOVERY_FILENAME, {
        "state": "prepared-model-preconvergence",
        "attempt": prior_attempt + 1,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "job": job_name,
        "backup_directory": str(backup),
        "basis": "Fresh-atomic SCF exhausted NELM; regenerate a clean DFT+U seed before a damped final SCF.",
        "settings": settings,
        "checkpoint_policy": "All failed-run CHGCAR/WAVECAR and preconvergence stages archived; none retained or reused.",
    })
    _write_json_file(job / "run_status.json", {
        "job": job_name, "status": "recovery-prepared", "complete": False,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    })
    for filename in (VASP_RUNNER_PID_FILENAME, VASP_RUNNER_PGID_FILENAME):
        (root / filename).unlink(missing_ok=True)
    _write_json_file(root / VASP_RUNNER_CONTROL_FILENAME, {
        "state": "recovery-prepared", "job": job_name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    print(json.dumps({"prepared": True, "job": job_name, "backup_directory": str(backup), "settings": settings}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
