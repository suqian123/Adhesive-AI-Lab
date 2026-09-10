"""Archive the failed clean CeO2(111) ENCUT=600 run and prepare a fresh retry.

The retry is deliberately limited to the failed cutoff point. It starts from
atomic charge and never reuses the 520 eV charge density or wavefunctions,
whose FFT grids do not match the 600 eV calculation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from adhesive_ai.campaign_runner import _ensure_no_vasp_process_is_running, _update_incar_settings
from adhesive_ai.vasp_checkpoint import electronic_converged


MUTABLE_OUTPUTS = (
    "CHG", "CHGCAR", "CONTCAR", "DOSCAR", "EIGENVAL", "IBZKPT", "OSZICAR",
    "OUTCAR", "PCDAT", "PROCAR", "REPORT", "vasp.stderr.log", "vasp.stdout.log",
    "vasprun.xml", "WAVECAR", "XDATCAR",
)

RECOVERY_SETTINGS = {
    "ALGO": "Normal",
    "NELM": "180",
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


def prepare(root: Path) -> dict[str, object]:
    root = root.resolve()
    job = root / "encut" / "600"
    status_path = job / "run_status.json"
    if not (root / "clean_baseline.json").is_file():
        raise RuntimeError("Refusing recovery: this is not the corrected clean (111) baseline")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("status") != "failed" or status.get("complete") is not False:
        raise RuntimeError(f"Refusing recovery: expected failed ENCUT=600, got {status!r}")
    if electronic_converged(job):
        raise RuntimeError("Refusing recovery: ENCUT=600 is already electronically converged")
    outcar = (job / "OUTCAR").read_text(encoding="utf-8", errors="replace")
    if "electronic self-consistency was not achieved" not in outcar:
        raise RuntimeError("Refusing recovery: failed output is not a NELM exhaustion")
    if (job / "scf_recovery.json").exists():
        raise RuntimeError("Refusing recovery: an ENCUT=600 recovery is already prepared")

    _ensure_no_vasp_process_is_running()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = job / ".scf-recovery-backups" / timestamp
    backup.mkdir(parents=True, exist_ok=False)
    for name in MUTABLE_OUTPUTS:
        source = job / name
        if source.exists():
            shutil.move(str(source), str(backup / name))
    shutil.copy2(job / "INCAR", backup / "INCAR")
    shutil.copy2(status_path, backup / "run_status.json")
    _update_incar_settings(job / "INCAR", RECOVERY_SETTINGS)

    prepared_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "state": "prepared-fresh-atomic",
        "attempt": 1,
        "prepared_at": prepared_at,
        "job": "encut/600",
        "backup_directory": str(backup),
        "basis": "NELM=120 exhausted after incompatible ENCUT=520 CHGCAR/WAVECAR reuse",
        "settings": RECOVERY_SETTINGS,
        "checkpoint_policy": "No CHGCAR or WAVECAR is retained or reused",
    }
    (job / "scf_recovery.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    status_path.write_text(
        json.dumps(
            {"job": "encut/600", "status": "recovery-prepared", "complete": False, "prepared_at": prepared_at},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "runner_control.json").write_text(
        json.dumps(
            {"state": "recovery-prepared", "job": "encut/600", "updated_at": prepared_at},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(prepare(PROJECT_ROOT / "work" / "vasp_validation" / "ceo2-111-baseline-v1"), ensure_ascii=False))
