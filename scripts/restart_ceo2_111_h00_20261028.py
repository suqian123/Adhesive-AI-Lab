"""Resume the interrupted CeO2(111) h00 relaxation from a verified static SCF.

The failed production directory and job record are preserved. Only this task is
released from the post-shutdown hold; the remaining tasks stay blocked.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adhesive_ai.campaign_runner import (  # noqa: E402
    _launch_supervisor,
    _vasp_production_approved,
    _write_run,
    advance_campaign_run,
)
from adhesive_ai.vasp_checkpoint import electronic_converged  # noqa: E402
from adhesive_ai.vasp_production import validate_vasp_input_set  # noqa: E402

RUN_ID = "CL5-00016-1b4ebc3d36"
TASK_ID = "dft-o-111-v00-h00"
OLD_JOB_ID = "vasp-1d70bc8b98"
RUN_ROOT = ROOT / "work" / "campaign_runs"
RUN_DIR = RUN_ROOT / RUN_ID
RUN_FILE = RUN_DIR / "run.json"
PILOT = ROOT / "work" / "recovery_checks" / RUN_ID / "h00-contcar-scf-20261027"
ATTEMPT = RUN_DIR / "recovery_attempts" / "dft-o-111-v00-h00-20261028"
APPROVAL = ROOT / "work" / "vasp_validation" / "ceo2-111-baseline-v1" / "approved.json"
PHYSICAL = ("POTCAR", "KPOINTS", "vdw_kernel.bindat")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def live_vasp() -> bool:
    check = subprocess.run(["pgrep", "-u", "adhesive-ai", "-x", "vasp_std"], capture_output=True)
    if check.returncode not in (0, 1):
        raise RuntimeError("Cannot inspect VASP processes")
    return check.returncode == 0


def preflight(*, expect_attempt: bool) -> tuple[dict, dict, Path, dict]:
    run = read_json(RUN_FILE)
    if run.get("run_id") != RUN_ID or run.get("status") != "failed" or not run.get("recovery_hold_20261027"):
        raise RuntimeError("Campaign state or recovery hold changed")
    task = next((item for item in run["tasks"] if item.get("task_id") == TASK_ID), None)
    if not task or task.get("status") != "failed" or task.get("job_id") != OLD_JOB_ID:
        raise RuntimeError("Original h00 task changed")
    source = Path(task["workdir"]).resolve()
    if not source.is_relative_to(RUN_DIR.resolve()):
        raise RuntimeError("Original task directory is outside campaign")
    job = read_json(ROOT / "work" / "jobs" / OLD_JOB_ID / "job.json")
    if job.get("status") != "failed" or Path(job["workdir"]).resolve() != source:
        raise RuntimeError("Original job changed")
    if live_vasp():
        raise RuntimeError("A VASP calculation is already running")
    ledger = ROOT / "work" / "compute_resources" / "ranks.json"
    if ledger.exists() and read_json(ledger):
        raise RuntimeError("MPI rank ledger is not empty")
    if not _vasp_production_approved(APPROVAL, expected_facet="(111)"):
        raise RuntimeError("CeO2(111) production approval is missing")
    pilot = read_json(PILOT / "pilot.json")
    if (pilot.get("state") != "ready-for-reviewed-production-restart"
            or pilot.get("return_code") != 0 or not pilot.get("electronic_converged")
            or pilot.get("source_job_id") != OLD_JOB_ID
            or Path(pilot.get("source_workdir", "")).resolve() != source
            or not electronic_converged(PILOT)):
        raise RuntimeError("Pilot is not independently converged for this task")
    if digest(source / "CONTCAR") != pilot["source_contcar_sha256"]:
        raise RuntimeError("Interrupted geometry changed")
    if digest(PILOT / "POSCAR") != pilot["pilot_poscar_sha256"]:
        raise RuntimeError("Pilot geometry changed")
    for name in PHYSICAL:
        if digest(source / name) != pilot["physical_sha256"][name] or digest(PILOT / name) != pilot["physical_sha256"][name]:
            raise RuntimeError(f"Physical input changed: {name}")
    for name in ("CHGCAR", "WAVECAR"):
        if not (PILOT / name).is_file() or (PILOT / name).stat().st_size == 0:
            raise RuntimeError(f"Converged pilot lacks {name}")
    if ATTEMPT.exists() != expect_attempt:
        raise RuntimeError("Recovery attempt directory has unexpected state")
    if shutil.disk_usage(RUN_DIR).free < 16 * 1024**3:
        raise RuntimeError("Less than 16 GiB of free disk remains")
    return run, task, source, pilot


def restart_incar(source: Path, pilot: dict) -> str:
    replacements = {
        "ISTART": "1", "ICHARG": "1", "NELM": "240",
        "DIPOL": " ".join(f"{value:.6f}" for value in pilot["dipole_center_direct"]),
    }
    lines = []
    seen = set()
    for line in (source / "INCAR").read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip().upper() if "=" in line else ""
        if key in replacements:
            if key not in seen:
                lines.append(f"{key} = {replacements[key]}")
                seen.add(key)
        else:
            lines.append(line)
    lines.extend(f"{key} = {value}" for key, value in replacements.items() if key not in seen)
    return "\n".join(lines) + "\n"


def prepare() -> dict:
    _, _, source, pilot = preflight(expect_attempt=False)
    ATTEMPT.mkdir(parents=True, mode=0o700)
    try:
        shutil.copy2(PILOT / "POSCAR", ATTEMPT / "POSCAR")
        for name in (*PHYSICAL, "input_manifest.json"):
            shutil.copy2(source / name, ATTEMPT / name)
        for name in ("CHGCAR", "WAVECAR"):
            shutil.copy2(PILOT / name, ATTEMPT / name)
        (ATTEMPT / "INCAR").write_text(restart_incar(source, pilot), encoding="utf-8")
        report = validate_vasp_input_set(ATTEMPT, resources=ROOT / "work" / "vasp_resources.json")
        if not report["valid"]:
            raise RuntimeError(f"Restart inputs failed static validation: {report}")
        manifest = {
            "state": "prepared", "run_id": RUN_ID, "task_id": TASK_ID,
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "previous_job_id": OLD_JOB_ID, "previous_workdir": str(source),
            "converged_pilot": str(PILOT),
            "input_sha256": {name: digest(ATTEMPT / name) for name in
                             ("POSCAR", "INCAR", *PHYSICAL, "CHGCAR", "WAVECAR")},
            "scientific_gate": "Final relaxation must terminate normally and pass electronic convergence before integration",
        }
        (ATTEMPT / "restart.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return {"state": "prepared", "directory": str(ATTEMPT), "checks": report["checks"]}
    except Exception:
        shutil.rmtree(ATTEMPT)
        raise


def launch() -> dict:
    with (RUN_DIR / ".production-recovery.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run, task, _, _ = preflight(expect_attempt=True)
        manifest = read_json(ATTEMPT / "restart.json")
        if manifest.get("state") != "prepared" or manifest.get("task_id") != TASK_ID:
            raise RuntimeError("Recovery attempt is not prepared")
        for name, expected in manifest["input_sha256"].items():
            if digest(ATTEMPT / name) != expected:
                raise RuntimeError(f"Recovery input changed: {name}")
        report = validate_vasp_input_set(ATTEMPT, resources=ROOT / "work" / "vasp_resources.json")
        if not report["valid"]:
            raise RuntimeError(f"Restart inputs failed static validation: {report}")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = RUN_DIR / ".production-scf-recovery-backups" / f"h00-restart-{stamp}"
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(RUN_FILE, backup / "run.json.before")
        shutil.copy2(ROOT / "work" / "jobs" / OLD_JOB_ID / "job.json", backup / "job.json.before")
        for item in run["tasks"]:
            if item["task_id"] == TASK_ID:
                item.update(status="pending", workdir=str(ATTEMPT), job_id=None,
                            blocker=None, error=None, return_code=None,
                            recovery_previous_job_id=OLD_JOB_ID,
                            recovery_previous_workdir=task["workdir"])
        run.update(status="running", result_payload={}, max_parallel=1)
        run["controlled_restart_20261028"] = {
            "task_id": TASK_ID, "attempt": str(ATTEMPT), "backup": str(backup),
            "checkpoint": str(PILOT), "at": datetime.now(timezone.utc).isoformat(),
            "other_tasks_held": True,
        }
        _write_run(run, RUN_ROOT)
        result = advance_campaign_run(RUN_ID, root=RUN_ROOT)
        updated = next(item for item in result["tasks"] if item["task_id"] == TASK_ID)
        if updated["status"] not in {"queued", "running"} or not updated.get("job_id"):
            raise RuntimeError(f"Restart dispatch failed: {updated.get('status')} {updated.get('error')}")
        manifest.update(state="launched", job_id=updated["job_id"],
                        launched_at=datetime.now(timezone.utc).isoformat(), backup=str(backup))
        (ATTEMPT / "restart.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        _launch_supervisor(RUN_ID, root=RUN_ROOT)
        return {"state": updated["status"], "job_id": updated["job_id"],
                "workdir": updated["workdir"], "backup": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "prepare", "launch"))
    action = parser.parse_args().action
    result = ({"ready": True, "task_id": TASK_ID} if action == "preflight" and preflight(expect_attempt=False)
              else prepare() if action == "prepare" else launch())
    print(json.dumps(result, ensure_ascii=False))
