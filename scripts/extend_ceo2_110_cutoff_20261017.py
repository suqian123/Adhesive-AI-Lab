"""Extend the finished neutral CeO2(110) matrix by one 700 eV cutoff point."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "deploy" / "compute"))

from dotenv import load_dotenv
from adhesive_ai.campaign_runner import vasp_convergence_progress
from adhesive_ai.vasp_checkpoint import electronic_converged
from adhesive_ai.vasp_production import validate_vasp_input_set
from mpi_launch import RankPool, process_identity

VALIDATION = ROOT / "work" / "vasp_validation" / "ceo2-110-baseline-v1"
SOURCE = VALIDATION / "encut" / "650"
TARGET = VALIDATION / "encut" / "700"
RESOURCES = ROOT / "work" / "vasp_resources.json"
RUNNER = ROOT / "scripts" / "run_vasp_convergence_110_cutoff700.sh"
STATIC = ("POSCAR", "POTCAR", "KPOINTS", "vdw_kernel.bindat")


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def rank_preflight() -> dict:
    load_dotenv(ROOT / ".env", override=True)
    expected = {"ADHESIVE_VALIDATION_USE_LAUNCHER": "1",
                "ADHESIVE_VALIDATION_MPI_RANKS": "16", "ADHESIVE_MPI_BUDGET": "48"}
    for key, value in expected.items():
        if os.environ.get(key) != value:
            raise RuntimeError(f"Unexpected {key}: {os.environ.get(key)!r}")
    pool = RankPool(ROOT / "work" / "compute_resources", 48)
    with pool.ledger() as leases:
        checked = dict(leases)
    occupied = sum(lease["ranks"] for lease in checked.values())
    if occupied > 32:
        raise RuntimeError("Fewer than 16 MPI ranks remain free")
    children = {}
    for lease in checked.values():
        child = lease.get("child_pid")
        if not child or process_identity(child) != lease.get("child_identity"):
            raise RuntimeError(f"MPI lease has no live child: {lease}")
        children[child] = lease["ranks"]
    output = subprocess.check_output(["ps", "-eo", "pid=,ppid=,comm="], text=True)
    vasp = [(int(pid), int(ppid)) for line in output.splitlines()
            for pid, ppid, comm in [line.split(maxsplit=2)] if comm == "vasp_std"]
    if any(ppid not in children for _, ppid in vasp):
        raise RuntimeError(f"Untracked VASP processes exist: {vasp}")
    if any(sum(ppid == child for _, ppid in vasp) > ranks for child, ranks in children.items()):
        raise RuntimeError("VASP process count exceeds a rank lease")
    return {"free_ranks": 48 - occupied, "tracked_vasp_processes": len(vasp)}


def preflight() -> dict:
    progress = vasp_convergence_progress(VALIDATION)
    if (progress.get("facet") != "(110)" or progress.get("total") != 14
            or progress.get("completed") != 14 or progress.get("failed") != 0
            or progress.get("active") or progress.get("approved")
            or progress.get("runner_state") != "failed"):
        raise RuntimeError(f"Unexpected matrix state: {progress}")
    if (VALIDATION / "approved.json").exists() or (VALIDATION / ".runner.lock").exists():
        raise RuntimeError("Matrix is approved or locked")
    if TARGET.exists():
        raise RuntimeError(f"Extension already exists: {TARGET}")
    plan = json.loads((VALIDATION / "validation_plan.json").read_text())
    report = json.loads((VALIDATION / "convergence_report.json").read_text())
    if (plan.get("facet") != "(110)" or plan.get("job_count") != 14
            or len(plan.get("jobs", [])) != 14
            or plan["acceptance"].get("total_energy_change_ev_per_atom_max") != 0.001):
        raise RuntimeError("Unexpected acceptance plan")
    if (report.get("passed") is not False
            or report.get("checks") != {
                "all_jobs_complete": True, "all_electronic_steps_converged": True,
                "encut": False, "kpoints": True, "vacuum": True, "slab_layers": True,
            }):
        raise RuntimeError("Matrix failed for a different reason")
    delta = float(report["deltas"]["encut_ev_per_atom"])
    if not 0.001 < delta < 0.002:
        raise RuntimeError(f"Unexpected cutoff delta: {delta}")
    source_manifest = json.loads((SOURCE / "input_manifest.json").read_text())
    if (source_manifest.get("facet") != "(110)"
            or source_manifest.get("surface_termination") != "neutral-CeO2-layers-v2"
            or source_manifest.get("validation_axis") != "encut"
            or source_manifest.get("encut_ev") != 650
            or source_manifest.get("kpoints") != [1, 1, 1]
            or not electronic_converged(SOURCE)):
        raise RuntimeError("650 eV source is not a verified neutral baseline")
    incar = (SOURCE / "INCAR").read_text()
    for tag in ("ENCUT = 650", "EDIFF = 1E-6", "ALGO = All",
                "LDIPOL = .TRUE.", "DIPOL = 0.5 0.5 0.5"):
        if tag not in incar:
            raise RuntimeError(f"Source INCAR changed: {tag}")
    if not RUNNER.is_file() or '"encut/700"' not in RUNNER.read_text():
        raise RuntimeError("Dedicated extension runner is missing")
    return {"ready": True, "cutoff_delta_ev_per_atom": delta,
            "threshold_ev_per_atom": 0.001,
            "source_input_sha256": {name: digest(SOURCE / name) for name in STATIC},
            **rank_preflight()}


def apply() -> dict:
    checks = preflight()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = VALIDATION / ".cutoff-extensions" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    for name in ("validation_plan.json", "convergence_report.json", "runner_control.json",
                 "status.tsv", "runner.stdout.log", "runner.stderr.log"):
        path = VALIDATION / name
        if path.is_file():
            shutil.copy2(path, backup / name)
    temporary = TARGET.with_name(f".700.{uuid.uuid4().hex}.tmp")
    temporary.mkdir()
    for name in (*STATIC, "INCAR", "input_manifest.json"):
        shutil.copy2(SOURCE / name, temporary / name)
    incar = (temporary / "INCAR").read_text()
    if len(re.findall(r"(?m)^ENCUT\s*=\s*650\s*$", incar)) != 1:
        raise RuntimeError("Expected exactly one ENCUT setting")
    (temporary / "INCAR").write_text(re.sub(r"(?m)^ENCUT\s*=\s*650\s*$",
                                           "ENCUT = 700", incar))
    manifest = json.loads((temporary / "input_manifest.json").read_text())
    manifest["encut_ev"] = 700
    manifest["extension_reason"] = "650-600 eV delta exceeded unchanged 0.001 eV/atom threshold"
    (temporary / "input_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    validity = validate_vasp_input_set(temporary, resources=RESOURCES)
    if not validity["valid"]:
        raise RuntimeError(f"700 eV input failed validation: {validity}")
    if any(digest(temporary / name) != expected for name, expected in checks["source_input_sha256"].items()):
        raise RuntimeError("Physical input changed during extension")
    os.replace(temporary, TARGET)
    plan = json.loads((VALIDATION / "validation_plan.json").read_text())
    plan["jobs"].append({"path": str(TARGET), **manifest})
    plan["job_count"] = 15
    plan["scientific_status"] = "pending-extended-cutoff-validation"
    plan.setdefault("extension_history", []).append({
        "at": datetime.now(timezone.utc).isoformat(), "added": "encut/700",
        "previous_report": str(backup / "convergence_report.json"),
        "previous_cutoff_delta_ev_per_atom": checks["cutoff_delta_ev_per_atom"],
        "acceptance_threshold_ev_per_atom": 0.001,
    })
    plan_temp = VALIDATION / f".validation_plan.{uuid.uuid4().hex}.tmp"
    plan_temp.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    os.replace(plan_temp, VALIDATION / "validation_plan.json")
    (VALIDATION / "convergence_report.json").unlink()
    snapshot = VALIDATION / ".runner-script-cutoff700.sh"
    shutil.copy2(RUNNER, snapshot)
    environment = {**os.environ, "ADHESIVE_VASP_VALIDATION_ROOT": str(VALIDATION),
                   "ADHESIVE_VASP_SCRIPT_DIR": str(RUNNER.parent)}
    with (VALIDATION / "runner.stdout.log").open("ab") as stdout, \
         (VALIDATION / "runner.stderr.log").open("ab") as stderr:
        process = subprocess.Popen(["bash", str(snapshot)], cwd=ROOT, env=environment,
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   start_new_session=True)
    time.sleep(2)
    if process.poll() is not None:
        raise RuntimeError(f"Extension runner exited with {process.returncode}; inspect {snapshot}")
    progress = vasp_convergence_progress(VALIDATION)
    if progress.get("runner_state") != "running":
        raise RuntimeError(f"Extension runner did not enter running state: {progress}")
    return {"started": True, "runner_pid": process.pid, "total": progress["total"],
            "completed": progress["completed"], "backup": str(backup),
            "free_ranks_before_start": checks["free_ranks"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply() if args.apply else preflight(), ensure_ascii=False))
