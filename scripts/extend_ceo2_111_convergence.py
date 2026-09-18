"""Append the two audited CeO2(111) convergence points required by the report.

The existing converged points remain immutable.  ENCUT=650 eV and the
five-trilayer slab are written as fresh input sets so the runner starts them
from atomic charge rather than an incompatible cutoff/slab checkpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.campaign_runner import _ensure_no_vasp_process_is_running
from adhesive_ai.vasp_checkpoint import electronic_converged
from adhesive_ai.vasp_production import VaspBaseline, build_ceo2_model, write_vasp_model


VALIDATION_ROOT = ROOT / "work" / "vasp_validation" / "ceo2-111-baseline-v1"
REQUIRED_COMPLETED = (
    "encut/450", "encut/520", "encut/600",
    "kpoints/1x1x1", "kpoints/2x2x1", "kpoints/3x3x1",
    "slab-layers/2", "slab-layers/3", "slab-layers/4",
    "vacuum/15A", "vacuum/18A", "vacuum/22A",
)


def _completed(root: Path, relative: str) -> bool:
    job = root / relative
    try:
        status = json.loads((job / "run_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return status.get("complete") is True and electronic_converged(job)


def main() -> int:
    root = VALIDATION_ROOT.resolve()
    if not (root / "clean_baseline.json").is_file():
        raise RuntimeError("Refusing extension: corrected clean CeO2(111) baseline is missing.")
    if any(not _completed(root, relative) for relative in REQUIRED_COMPLETED):
        raise RuntimeError("Refusing extension: all original 12 points must be electronically converged first.")
    if any((root / relative).exists() for relative in ("encut/650", "slab-layers/5")):
        raise RuntimeError("Refusing extension: an additional convergence point already exists.")
    _ensure_no_vasp_process_is_running()

    resources = ROOT / "work" / "vasp_resources.json"
    baseline = VaspBaseline(ncore=2, kpar=1, ce_initial_moment=0.0)
    atoms, metadata = build_ceo2_model(
        "(111)", objective="surface-convergence", settings=baseline, repeat_override=(1, 1, 1),
    )
    encut_directory = root / "encut" / "650"
    encut_manifest = write_vasp_model(
        atoms, encut_directory, metadata={**metadata, "validation_axis": "encut"},
        resources=resources, settings=baseline, cutoff_ev=650, kpoints=(1, 1, 1), static=True,
    )

    slab_settings = VaspBaseline(slab_layers=5, ncore=2, kpar=1, ce_initial_moment=0.0)
    slab, slab_metadata = build_ceo2_model(
        "(111)", objective="surface-convergence", settings=slab_settings, repeat_override=(1, 1, 1),
    )
    slab_directory = root / "slab-layers" / "5"
    slab_manifest = write_vasp_model(
        slab, slab_directory, metadata={**slab_metadata, "validation_axis": "slab_layers"},
        resources=resources, settings=slab_settings, kpoints=(1, 1, 1), static=True,
    )

    plan_path = root / "validation_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["jobs"].extend([
        {"path": str(encut_directory), **encut_manifest},
        {"path": str(slab_directory), **slab_manifest},
    ])
    plan["job_count"] = len(plan["jobs"])
    plan["scientific_status"] = "pending-convergence-calculations"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat()
    (root / "runner_control.json").write_text(json.dumps({
        "state": "extension-prepared", "jobs": ["encut/650", "slab-layers/5"], "updated_at": now,
    }) + "\n", encoding="utf-8")
    with (root / "status.tsv").open("a", encoding="utf-8") as stream:
        stream.write(f"{now}\tconvergence-extension\tprepared\t0\n")
    print(json.dumps({"prepared": ["encut/650", "slab-layers/5"], "job_count": plan["job_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
