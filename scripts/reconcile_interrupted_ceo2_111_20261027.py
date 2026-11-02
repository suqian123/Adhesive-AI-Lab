"""Reconcile the power-off interrupted campaign without dispatching new work."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "CL5-00016-1b4ebc3d36"
RUN_PATH = ROOT / "work" / "campaign_runs" / RUN_ID / "run.json"
JOBS = ROOT / "work" / "jobs"
TARGETS = {
    "dft-o-111-v00-h00": "vasp-1d70bc8b98",
    "dft-o-111-v00-h35": "vasp-3ea271025d",
}
POWER_OFF_UTC = "2026-10-26T09:50:18+00:00"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def live(pid: str) -> bool:
    try:
        return Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, ValueError):
        return False


def preflight() -> tuple[dict, list[str]]:
    run = read_json(RUN_PATH)
    if run.get("run_id") != RUN_ID or run.get("status") != "running":
        raise RuntimeError("Unexpected campaign state")
    if run.get("recovery_hold_20261027"):
        raise RuntimeError("Campaign has already been reconciled")
    tasks = {task["task_id"]: task for task in run["tasks"]}
    if {task_id for task_id, task in tasks.items() if task["status"] == "running"} != set(TARGETS):
        raise RuntimeError("Unexpected running task set")
    for task_id, job_id in TARGETS.items():
        task = tasks[task_id]
        job_dir = JOBS / job_id
        job = read_json(job_dir / "job.json")
        if (task.get("job_id") != job_id or job.get("status") != "failed"
                or job.get("error") != "Job runner exited unexpectedly"
                or Path(job["workdir"]).resolve() != Path(task["workdir"]).resolve()):
            raise RuntimeError(f"Interrupted job changed: {task_id}")
        pid_file = job_dir / "process.pid"
        if pid_file.exists() and live(pid_file.read_text()):
            raise RuntimeError(f"Original runner still alive: {task_id}")
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            command = (proc / "cmdline").read_bytes()
        except OSError:
            continue
        if (b"python" in command.split(b"\0", 1)[0]
                and b"_supervise(" in command and RUN_ID.encode() in command):
            raise RuntimeError("Campaign supervisor is active")
    found = subprocess.run(["pgrep", "-x", "vasp_std"], capture_output=True, text=True)
    if found.returncode not in (0, 1):
        raise RuntimeError("Cannot inspect VASP processes")
    target_dirs = {Path(tasks[name]["workdir"]).resolve() for name in TARGETS}
    for pid in found.stdout.split():
        try:
            if Path(os.readlink(f"/proc/{pid}/cwd")).resolve() in target_dirs:
                raise RuntimeError("Original production VASP still running")
        except OSError:
            pass
    pending = [task_id for task_id, task in tasks.items() if task["status"] == "pending"]
    if len(pending) != 12:
        raise RuntimeError(f"Pending task set changed: {pending}")
    for task_id, directory in (
        ("dft-o-111-v00-h00", "h00-contcar-scf-20261027"),
        ("dft-o-111-v00-h35", "h35-dftu-dipole-20261027"),
    ):
        manifest_name = "pilot.json" if task_id.endswith("h00") else "retry.json"
        manifest = read_json(ROOT / "work" / "recovery_checks" / RUN_ID / directory / manifest_name)
        if manifest.get("state") != "prepared" or manifest.get("task_id") != task_id:
            raise RuntimeError(f"Isolated diagnostic is not prepared: {task_id}")
    return run, pending


def apply() -> dict:
    with (RUN_PATH.parent / ".production-recovery.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run, pending = preflight()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = RUN_PATH.parent / ".production-scf-recovery-backups" / f"poweroff-reconcile-{stamp}"
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(RUN_PATH, backup / "run.json.before")
        for task_id, job_id in TARGETS.items():
            shutil.copy2(JOBS / job_id / "job.json", backup / f"{task_id}.job.json.before")
        for task in run["tasks"]:
            if task["task_id"] in TARGETS:
                task.update(status="failed", return_code=None,
                            error=f"Job interrupted by server power-off at {POWER_OFF_UTC}; no validated final result",
                            recovery_diagnostic=("h00-contcar-scf-20261027" if task["task_id"].endswith("h00")
                                                 else "h35-dftu-dipole-20261027"))
            elif task["task_id"] in pending:
                task.update(status="blocked", blocker="Held for CeO2(111) post-shutdown recovery review",
                            recovery_hold_previous_status="pending")
        run["status"] = "failed"
        run["recovery_hold_20261027"] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "reason": "Two production jobs interrupted by power-off; hold pending tasks until controlled review",
            "task_ids": list(TARGETS), "held_pending_task_ids": pending,
            "backup": str(backup), "auto_dispatch": False,
        }
        run["updated_at"] = datetime.now(timezone.utc).isoformat()
        temp = RUN_PATH.with_name(f".{RUN_PATH.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n")
        os.replace(temp, RUN_PATH)
        return {"reconciled": list(TARGETS), "held_pending": len(pending),
                "backup": str(backup), "run_status": run["status"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        print(json.dumps(apply(), ensure_ascii=False))
    else:
        run, pending = preflight()
        print(json.dumps({"ready": True, "run_id": RUN_ID, "targets": TARGETS,
                          "pending_to_hold": pending}, ensure_ascii=False))
