"""Prepare one audited, grid-consistent SCF recovery for CeO2(111) five-layer slab.

The failed 60-atom slab output is archived.  The convergence runner will build
an exact-task fixed-charge -> PBE -> DFT+U bridge before its damped final SCF,
so no unverified charge density is reused as a production checkpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
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
JOB_NAME = "slab-layers/5"
MUTABLE_OUTPUTS = (
    "CHG", "CHGCAR", "CONTCAR", "DOSCAR", "EIGENVAL", "IBZKPT", "OSZICAR",
    "OUTCAR", "PCDAT", "PROCAR", "REPORT", "WAVECAR", "XDATCAR", "vasprun.xml",
    "vasp.stdout.log", "vasp.stderr.log", "run_status.json", "scf_recovery.json",
)
RECOVERY_SETTINGS = {
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


def main() -> int:
    root = VALIDATION_ROOT.resolve()
    job = (root / JOB_NAME).resolve()
    if not job.is_relative_to(root):
        raise RuntimeError("Recovery target is outside the validation root.")
    status = _read_json_file(job / "run_status.json")
    if status.get("status") != "failed" or status.get("complete") is not False:
        raise RuntimeError(f"Expected an uncompleted failed five-layer slab job, got {status!r}.")
    if electronic_converged(job):
        raise RuntimeError("The five-layer slab is electronically converged; recovery is not allowed.")
    outcar = (job / "OUTCAR").read_text(encoding="utf-8", errors="replace")
    if "electronic self-consistency was not achieved" not in outcar:
        raise RuntimeError("The five-layer slab output is not a NELM-exhaustion SCF failure.")
    if _read_json_file(job / VASP_SCF_RECOVERY_FILENAME):
        raise RuntimeError("A five-layer slab recovery marker already exists; repeated changes are blocked.")
    _ensure_no_vasp_process_is_running()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = job / ".scf-model-recovery-backups" / timestamp
    backup.mkdir(parents=True, exist_ok=False)
    for name in MUTABLE_OUTPUTS:
        source = job / name
        if source.is_file():
            shutil.move(str(source), str(backup / name))
    stage = job / ".model-preconverge"
    if stage.exists():
        shutil.move(str(stage), str(backup / stage.name))
    incar = job / "INCAR"
    shutil.copy2(incar, backup / "INCAR.before-model-recovery")
    _update_incar_settings(incar, RECOVERY_SETTINGS)
    prepared_at = datetime.now(timezone.utc).isoformat()
    _write_json_file(job / VASP_SCF_RECOVERY_FILENAME, {
        "state": "prepared-model-preconvergence",
        "attempt": 1,
        "prepared_at": prepared_at,
        "job": JOB_NAME,
        "backup_directory": str(backup),
        "basis": "Fresh-atomic five-layer SCF exhausted NELM; rebuild an exact-task DFT+U seed before damped final SCF.",
        "settings": RECOVERY_SETTINGS,
        "checkpoint_policy": "All failed five-layer outputs archived; none retained or reused as a checkpoint.",
    })
    _write_json_file(job / "run_status.json", {
        "job": JOB_NAME, "status": "recovery-prepared", "complete": False, "prepared_at": prepared_at,
    })
    for name in (VASP_RUNNER_PID_FILENAME, VASP_RUNNER_PGID_FILENAME):
        (root / name).unlink(missing_ok=True)
    _write_json_file(root / VASP_RUNNER_CONTROL_FILENAME, {
        "state": "recovery-prepared", "job": JOB_NAME, "updated_at": prepared_at,
    })
    print(json.dumps({"prepared": True, "job": JOB_NAME, "backup_directory": str(backup), "settings": RECOVERY_SETTINGS}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
