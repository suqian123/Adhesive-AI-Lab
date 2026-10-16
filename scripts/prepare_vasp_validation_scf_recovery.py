"""Archive one failed clean CeO2(111) validation task and prepare one retry.

The recovery is deliberately constrained to a job declared by the validation
plan.  It keeps geometry, POTCAR, k-points, cutoff and all physical settings;
only the electronic solver settings needed for the clean retry are changed.
Failed charge densities and wavefunctions are archived and never reused.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from adhesive_ai.campaign_runner import _ensure_no_vasp_process_is_running, _update_incar_settings
from adhesive_ai.vasp_checkpoint import electronic_converged
from adhesive_ai.vasp_production import configured_ncore


MUTABLE_OUTPUTS = (
    "CHG", "CHGCAR", "CONTCAR", "DOSCAR", "EIGENVAL", "IBZKPT", "OSZICAR",
    "OUTCAR", "PCDAT", "PROCAR", "REPORT", "vasp.stderr.log", "vasp.stdout.log",
    "vasprun.xml", "WAVECAR", "XDATCAR", "run_status.json", "scf_recovery.json",
)
RECOVERY_SETTINGS = {
    "ALGO": "All",
    "TIME": "0.1",
    "NELM": "240",
    "NELMDL": "-5",
    "ISTART": "0",
    "ICHARG": "2",
    "AMIX": "0.05",
    "BMIX": "0.0001",
    "AMIX_MAG": "0.2",
    "BMIX_MAG": "0.0001",
    "AMIN": "0.01",
    "LREAL": ".FALSE.",
    "LWAVE": ".TRUE.",
    "LCHARG": ".TRUE.",
}


def _declared_jobs(root: Path, plan: dict[str, object]) -> set[str]:
    jobs: set[str] = set()
    for record in plan.get("jobs", []):
        if not isinstance(record, dict):
            continue
        raw_path = Path(str(record.get("path") or ""))
        path = raw_path if raw_path.is_absolute() else root / raw_path
        try:
            jobs.add(path.resolve().relative_to(root).as_posix())
        except ValueError:
            continue
    return jobs


def prepare(root: Path, *, job_name: str) -> dict[str, object]:
    root = root.resolve()
    canonical_name = str(job_name).replace("\\", "/").strip("/")
    job = (root / canonical_name).resolve()
    if not job.is_relative_to(root):
        raise RuntimeError("Recovery target is outside the validation root")

    clean = json.loads((root / "clean_baseline.json").read_text(encoding="utf-8"))
    plan = json.loads((root / "validation_plan.json").read_text(encoding="utf-8"))
    if clean.get("surface_termination") != "O-Ce-O-trilayers-v2" or plan.get("facet") != "(111)":
        raise RuntimeError("Recovery is restricted to the corrected O-Ce-O (111) baseline")
    if canonical_name not in _declared_jobs(root, plan):
        raise RuntimeError("Recovery target is not declared in this validation plan")

    status_path = job / "run_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("status") != "failed" or status.get("complete") is not False:
        raise RuntimeError(f"Expected an uncompleted failed VASP task, got {status!r}")
    if electronic_converged(job):
        raise RuntimeError("The target is already electronically converged")
    outcar = (job / "OUTCAR").read_text(encoding="utf-8", errors="replace")
    if "electronic self-consistency was not achieved" not in outcar:
        raise RuntimeError("The failed output is not a NELM-exhaustion SCF failure")
    if (job / "scf_recovery.json").exists():
        raise RuntimeError("A controlled recovery is already prepared for this task")

    incar = job / "INCAR"
    incar_before = incar.read_text(encoding="utf-8")
    if "ENCUT" not in incar_before or "LDAU" not in incar_before:
        raise RuntimeError("The task INCAR does not contain the expected physical setup")
    recovery_settings = dict(RECOVERY_SETTINGS)
    recovery_settings["NCORE"] = str(configured_ncore(4))

    _ensure_no_vasp_process_is_running()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = job / ".scf-recovery-backups" / timestamp
    backup.mkdir(parents=True, exist_ok=False)
    for name in MUTABLE_OUTPUTS:
        source = job / name
        if source.is_file():
            shutil.move(str(source), str(backup / name))
    shutil.copy2(incar, backup / "INCAR.before-recovery")
    _update_incar_settings(incar, recovery_settings)

    prepared_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "state": "prepared-fresh-atomic",
        "attempt": 1,
        "prepared_at": prepared_at,
        "job": canonical_name,
        "backup_directory": str(backup),
        "basis": "Normal SCF exhausted NELM; retry with direct optimization from atomic charge.",
        "settings": recovery_settings,
        "checkpoint_policy": "No failed-run CHGCAR or WAVECAR is retained or reused.",
    }
    (job / "scf_recovery.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    status_path.write_text(json.dumps({
        "job": canonical_name, "status": "recovery-prepared", "complete": False, "prepared_at": prepared_at,
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    (root / "runner_control.json").write_text(json.dumps({
        "state": "recovery-prepared", "job": canonical_name, "updated_at": prepared_at,
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-root", type=Path, default=PROJECT_ROOT / "work" / "vasp_validation" / "ceo2-111-baseline-v1")
    parser.add_argument("--job", required=True, help="failed validation task, relative to --validation-root")
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.validation_root, job_name=arguments.job), ensure_ascii=False))
