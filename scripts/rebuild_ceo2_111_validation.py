"""Archive the known polar (111) suite and prepare its corrected clean rerun.

This is an explicit, one-time migration, not an automatic recovery mechanism.
It preserves the complete old directory and refuses to run while VASP exists.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from adhesive_ai.campaign_runner import _ensure_no_vasp_process_is_running
from adhesive_ai.vasp_production import write_convergence_suite, CLEAN_111_SCF_SETTINGS


def main():
    workspace = Path(__file__).resolve().parents[1]
    root = workspace / "work/vasp_validation/ceo2-111-baseline-v1"
    if not root.is_dir() or (root / "clean_baseline.json").exists():
        raise RuntimeError("Expected the original polar validation directory; migration refused")
    _ensure_no_vasp_process_is_running()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = root.parent / "archive" / (root.name + "-polar-" + timestamp)
    root.resolve().relative_to(workspace)
    archive.resolve().relative_to(workspace)
    archive.parent.mkdir(exist_ok=True)
    root.rename(archive)
    write_convergence_suite(root, resources=workspace / "work/vasp_resources.json")
    # Keep the physical model (PBE-D3, U, spin, dipole correction, EDIFF).
    # Use a small mixing amplitude, fresh atomic charge and save wavefunctions.
    settings = CLEAN_111_SCF_SETTINGS
    report = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "archive": str(archive), "surface_termination": "O-Ce-O-trilayers-v2",
        "reason": "Original Ce-bottom/O-top slab had a formal ionic dipole of -74.977 e Angstrom; all five completed markers lacked electronic convergence.",
        "settings": settings,
        "reuse_policy": "Only normally finished, electronically converged, same-POSCAR/POTCAR seeds; WAVECAR additionally requires same KPOINTS.",
    }
    (root / "clean_baseline.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
