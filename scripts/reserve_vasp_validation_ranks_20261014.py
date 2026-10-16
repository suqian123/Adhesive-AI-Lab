"""Reserve one 16-rank slot for live CeO2(110) validation without restarting the web service."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from adhesive_ai.jobs import cancel_job

RUN_ID = "CL5-00016-1b4ebc3d36"
RUN_PATH = PROJECT / "work" / "campaign_runs" / RUN_ID / "run.json"
JOB_ROOT = PROJECT / "work" / "jobs"
VALIDATION = PROJECT / "work" / "vasp_validation" / "ceo2-110-baseline-v1"
KEEP = {
    "dft-o-111-v00-h35": "vasp-c9c235efad",
    "dft-o-111-v08-h35": "vasp-e441f4f5d7",
}
DEFER = {
    "dft-o-111-v00-h00": "vasp-479ba4fcdb",
    "dft-o-111-v08-h00": "vasp-863094f7ab",
    "dft-o-111-v15-h00": "vasp-4e07d7dd5f",
    "dft-o-111-v15-h35": "vasp-3aa7eb18a3",
}
MUTABLE = (
    "OUTCAR", "OSZICAR", "vasprun.xml", "CONTCAR", "WAVECAR", "CHGCAR", "CHG",
    "DOSCAR", "EIGENVAL", "IBZKPT", "PCDAT", "PROCAR", "REPORT", "XDATCAR",
    "STOPCAR", "vasp.stdout.log", "vasp.stderr.log",
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def live(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def supervisor() -> int:
    found = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
            command = (path / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if b"_supervise(" in command and RUN_ID.encode() in command:
            found.append(int(path.name))
    if len(found) != 1:
        raise RuntimeError(f"Expected one campaign supervisor, found {found}")
    return found[0]


def preflight() -> tuple[dict, dict, int]:
    run = read_json(RUN_PATH)
    tasks = {task["task_id"]: task for task in run["tasks"]}
    if run.get("run_id") != RUN_ID or run.get("max_parallel") != 6 or run.get("status") != "running":
        raise RuntimeError("Unexpected campaign state")
    if set(KEEP | DEFER) - set(tasks):
        raise RuntimeError("Target task missing")
    for name, job_id in (KEEP | DEFER).items():
        task = tasks[name]
        if task.get("status") != "running" or task.get("job_id") != job_id:
            raise RuntimeError(f"Task changed: {name}")
        if read_json(JOB_ROOT / job_id / "job.json").get("status") != "running":
            raise RuntimeError(f"Job changed: {name}")
        if not (Path(task["workdir"]) / ".production-scf-recovery.json").is_file():
            raise RuntimeError(f"Fresh retry marker missing: {name}")
    if tasks["dft-pda-binding-111"].get("status") != "pending":
        raise RuntimeError("Binding task changed")
    stage = VALIDATION / "encut" / "450" / ".model-preconverge" / "step3-dftu"
    if read_json(stage / "run_status.json").get("status") != "running":
        raise RuntimeError("Validation stage is not running")
    if "Waiting for server MPI budget" not in (stage / "stage.stderr.log").read_text():
        raise RuntimeError("Validation is not waiting for MPI budget")
    leases = read_json(PROJECT / "work" / "compute_resources" / "ranks.json")
    leased = {Path(value["workdir"]).resolve() for value in leases.values()}
    expected = {Path(tasks[name]["workdir"]).resolve() for name in (*KEEP, "dft-o-111-v15-h00")}
    if leased != expected:
        raise RuntimeError(f"MPI leases changed: {leased}")
    return run, tasks, supervisor()


def apply() -> dict:
    lock_path = RUN_PATH.parent / ".production-recovery.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run, tasks, pid = preflight()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = RUN_PATH.parent / ".production-scf-recovery-backups" / f"validation-rank-reservation-{stamp}"
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(RUN_PATH, backup / "run.json.before")
        os.kill(pid, signal.SIGSTOP)
        try:
            current = read_json(RUN_PATH)
            current_tasks = {task["task_id"]: task for task in current["tasks"]}
            if current.get("max_parallel") != 6 or any(current_tasks[name].get("job_id") != old for name, old in (KEEP | DEFER).items()):
                raise RuntimeError("Campaign changed while pausing supervisor")
            for name in ("dft-o-111-v00-h00", "dft-o-111-v08-h00", "dft-o-111-v15-h35", "dft-o-111-v15-h00"):
                result = cancel_job(DEFER[name], root=JOB_ROOT)
                if result.status != "cancelled":
                    raise RuntimeError(f"Could not defer {name}")
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                old_runner = JOB_ROOT / DEFER["dft-o-111-v15-h00"] / "process.pid"
                old_pid = int(old_runner.read_text())
                leases = read_json(PROJECT / "work" / "compute_resources" / "ranks.json")
                target = Path(tasks["dft-o-111-v15-h00"]["workdir"]).resolve()
                if not live(old_pid) and all(Path(item["workdir"]).resolve() != target for item in leases.values()):
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Deferred production VASP did not stop")
            for name in DEFER:
                workdir = Path(tasks[name]["workdir"])
                archive = workdir / ".production-scf-recovery-backups" / f"validation-priority-{stamp}"
                archive.mkdir(parents=True, exist_ok=False)
                for filename in MUTABLE:
                    path = workdir / filename
                    if path.is_file():
                        shutil.move(str(path), str(archive / filename))
                shutil.copy2(JOB_ROOT / DEFER[name] / "job.json", archive / "job.json.before-resubmission")
                current_tasks[name].update(status="pending", job_id=None, return_code=None, error=None, blocker=None)
                current_tasks[name]["validation_priority_archive"] = str(archive)
            current["max_parallel"] = 2
            current.setdefault("recovery_history", []).append({
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": "Reserve 16 of 48 MPI ranks for active CeO2(110) validation",
                "deferred": list(DEFER), "backup": str(backup), "max_parallel": 2,
            })
            current["updated_at"] = datetime.now(timezone.utc).isoformat()
            temporary = RUN_PATH.with_name(f".{RUN_PATH.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, RUN_PATH)
            return {"reserved_rank_slot": 16, "max_parallel": 2, "backup": str(backup), "deferred": list(DEFER)}
        finally:
            if live(pid):
                os.kill(pid, signal.SIGCONT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    if arguments.apply:
        print(json.dumps(apply()))
    else:
        run, tasks, pid = preflight()
        print(json.dumps({"ready": True, "run_id": RUN_ID, "supervisor_pid": pid,
                          "keep": KEEP, "defer": DEFER}))
