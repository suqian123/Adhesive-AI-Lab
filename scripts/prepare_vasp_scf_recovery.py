"""Archive one failed VASP SCF and prepare a distinct, conservative retry.

This tool is intentionally explicit: it only accepts the corrected shared
CeO2(111) encut/450 job after a NELM exhaustion.  It preserves each mutable
output under a timestamped directory and restarts from atomic charge, rather
than treating an unconverged CHGCAR/WAVECAR as a physical checkpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from adhesive_ai.campaign_runner import _ensure_no_vasp_process_is_running, _update_incar_settings


MUTABLE_OUTPUTS = (
    "CHG", "CHGCAR", "CONTCAR", "DOSCAR", "EIGENVAL", "IBZKPT", "OSZICAR",
    "OUTCAR", "PCDAT", "PROCAR", "REPORT", "vasp.stderr.log", "vasp.stdout.log",
    "vasprun.xml", "WAVECAR", "XDATCAR",
)
RECOVERY_SETTINGS = {
    # IALGO=58 is VASP's direct, preconditioned CG method, recommended for
    # insulating systems. The smaller initial step avoids the prior mixing
    # oscillation while NELM leaves room for full convergence.
    "ALGO": "All", "TIME": "0.1", "NELM": "240", "ISTART": "0", "ICHARG": "2",
    "AMIX": "0.05", "BMIX": "0.0001", "AMIX_MAG": "0.2", "BMIX_MAG": "0.0001",
    "AMIN": "0.01", "LREAL": ".FALSE.", "LWAVE": ".TRUE.",
}


def prepare(root: Path) -> dict[str, object]:
    root = root.resolve()
    job = root / "encut" / "450"
    status_path = job / "run_status.json"
    if not (root / "clean_baseline.json").is_file():
        raise RuntimeError("Refusing recovery: this is not the corrected clean (111) baseline")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("status") != "failed":
        raise RuntimeError(f"Refusing recovery: expected failed encut/450, got {status.get('status')!r}")
    outcar = (job / "OUTCAR").read_text(encoding="utf-8", errors="replace")
    if "EDIFF was not reached (unconverged)" not in outcar:
        raise RuntimeError("Refusing recovery: the failure is not the expected NELM exhaustion")
    marker = job / "scf_recovery.json"
    if marker.exists():
        raise RuntimeError("Refusing recovery: a controlled retry is already prepared")
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
    payload = {
        "state": "prepared-fresh-atomic",
        "attempt": 1,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "job": "encut/450",
        "backup_directory": str(backup),
        "basis": "NELM exhaustion with oscillatory Normal-mixing SCF; no unconverged CHGCAR/WAVECAR reused",
        "settings": RECOVERY_SETTINGS,
    }
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    status_path.write_text(json.dumps({
        "job": "encut/450", "status": "recovery-prepared", "complete": False,
        "prepared_at": payload["prepared_at"],
    }) + "\n", encoding="utf-8")
    (root / "runner_control.json").write_text(json.dumps({
        "state": "recovery-prepared", "job": "encut/450", "updated_at": payload["prepared_at"],
    }) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    print(json.dumps(prepare(project / "work/vasp_validation/ceo2-111-baseline-v1"), ensure_ascii=False))
