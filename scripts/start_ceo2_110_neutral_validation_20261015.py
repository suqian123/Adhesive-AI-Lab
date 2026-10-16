"""Start the rebuilt (110) matrix alongside only tracked MPI jobs within 48 ranks."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "deploy" / "compute"))

from dotenv import load_dotenv
from adhesive_ai.campaign_runner import vasp_convergence_progress
from adhesive_ai.vasp_production import validate_vasp_input_set
from mpi_launch import RankPool, process_identity

VALIDATION = ROOT / "work" / "vasp_validation" / "ceo2-110-baseline-v1"
RESOURCES = ROOT / "work" / "vasp_resources.json"


def processes():
    output = subprocess.check_output(["ps", "-eo", "pid=,ppid=,comm="], text=True)
    return [(int(pid), int(ppid), comm) for line in output.splitlines()
            for pid, ppid, comm in [line.split(maxsplit=2)]]


def preflight() -> dict:
    load_dotenv(ROOT / ".env", override=True)
    expected = {"ADHESIVE_VALIDATION_USE_LAUNCHER": "1",
                "ADHESIVE_VALIDATION_MPI_RANKS": "16", "ADHESIVE_MPI_BUDGET": "48"}
    for key, value in expected.items():
        if os.environ.get(key) != value:
            raise RuntimeError(f"Unexpected {key}: {os.environ.get(key)!r}")
    progress = vasp_convergence_progress(VALIDATION)
    if (progress.get("facet") != "(110)" or progress.get("total") != 14
            or progress.get("completed") != 0 or progress.get("failed") != 0
            or progress.get("active") or progress.get("approved")):
        raise RuntimeError(f"Unexpected rebuilt matrix state: {progress}")
    record = json.loads((VALIDATION / "model_rebuild.json").read_text())
    if record.get("scientific_status") != "pending-convergence-validation":
        raise RuntimeError("New model has unexpected status")
    first = VALIDATION / "encut" / "450"
    if not validate_vasp_input_set(first, resources=RESOURCES)["valid"]:
        raise RuntimeError("First corrected job is not a valid input set")
    if (first / "CHGCAR").exists() or (first / "WAVECAR").exists():
        raise RuntimeError("Unverified charge or wavefunction found in new matrix")
    if (VALIDATION / ".runner.lock").exists():
        raise RuntimeError("Validation runner lock already exists")
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
            raise RuntimeError(f"MPI lease is not attached to a live child: {lease}")
        children[child] = lease["ranks"]
    vasp = [(pid, ppid) for pid, ppid, comm in processes() if comm == "vasp_std"]
    if any(ppid not in children for _, ppid in vasp):
        raise RuntimeError(f"Untracked VASP processes exist: {vasp}")
    if any(sum(ppid == child for _, ppid in vasp) > ranks for child, ranks in children.items()):
        raise RuntimeError(f"VASP process count exceeds a rank lease: {vasp}, {checked}")
    return {"ready": True, "tracked_vasp_ranks": len(vasp), "free_ranks": 48-occupied,
            "leases": len(checked), "validation_root": str(VALIDATION)}


def apply() -> dict:
    checks = preflight()
    runner = ROOT / "scripts" / "run_vasp_convergence.sh"
    snapshot = VALIDATION / ".runner-script.sh"
    shutil.copyfile(runner, snapshot)
    environment = {**os.environ, "ADHESIVE_VASP_VALIDATION_ROOT": str(VALIDATION),
                   "ADHESIVE_VASP_SCRIPT_DIR": str(runner.parent)}
    with (VALIDATION / "runner.stdout.log").open("ab") as stdout, \
         (VALIDATION / "runner.stderr.log").open("ab") as stderr:
        process = subprocess.Popen(
            ["bash", str(snapshot)], cwd=ROOT, env=environment,
            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True,
        )
    time.sleep(2)
    if process.poll() is not None:
        raise RuntimeError(f"Runner exited immediately with status {process.returncode}")
    progress = vasp_convergence_progress(VALIDATION)
    if not progress.get("active"):
        raise RuntimeError(f"Runner process alive but progress is not active: {progress}")
    return {"started": True, "runner_pid": process.pid, **checks,
            "runner_state": progress.get("runner_state")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply() if args.apply else preflight(), ensure_ascii=False))
