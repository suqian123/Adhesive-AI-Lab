"""Requeue two campaign jobs orphaned by a web-service restart, retaining their outputs."""
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
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adhesive_ai.campaign_runner import _launch_supervisor
from adhesive_ai.jobs import get_job_status

RUN_ID = "CL5-00016-1b4ebc3d36"
RUN_ROOT = ROOT / "work" / "campaign_runs"
RUN_PATH = RUN_ROOT / RUN_ID / "run.json"
JOB_ROOT = ROOT / "work" / "jobs"
TARGETS = {
    "dft-o-111-v00-h00": "vasp-a9804c360e",
    "dft-o-111-v00-h35": "vasp-0039cc3071",
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


def vasp_workdirs() -> set[Path]:
    result = subprocess.run(["pgrep", "-x", "vasp_std"], capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError("Could not inspect VASP processes")
    found = set()
    for raw in result.stdout.split():
        try:
            found.add(Path(os.readlink(f"/proc/{raw}/cwd")).resolve())
        except OSError:
            pass
    return found


def supervisor_pids() -> list[int]:
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            command = (proc / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if (b"python" in command.split(b" ", 1)[0] and b"_supervise(" in command and RUN_ID.encode() in command and b"import sys;" in command):
            found.append(int(proc.name))
    return found


def digest(path: Path) -> str:
    hash_value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            hash_value.update(block)
    return hash_value.hexdigest()


def preflight() -> tuple[dict, dict[str, dict]]:
    run = read_json(RUN_PATH)
    tasks = {item["task_id"]: item for item in run["tasks"]}
    if (run.get("run_id") != RUN_ID or run.get("status") != "running"
            or run.get("max_parallel") != 2 or set(TARGETS) - set(tasks)):
        raise RuntimeError("Unexpected campaign record")
    if {name for name, task in tasks.items() if task.get("status") == "running"} != set(TARGETS):
        raise RuntimeError("Unexpected running tasks")
    if supervisor_pids():
        raise RuntimeError("Campaign supervisor is already running")
    running_dirs = vasp_workdirs()
    for name, job_id in TARGETS.items():
        task = tasks[name]
        workdir = Path(task["workdir"]).resolve()
        if task.get("status") != "running" or task.get("job_id") != job_id:
            raise RuntimeError(f"Campaign task changed: {name}")
        if workdir in running_dirs or not workdir.is_relative_to(RUN_PATH.parent):
            raise RuntimeError(f"VASP job still running or outside campaign: {name}")
        record = read_json(JOB_ROOT / job_id / "job.json")
        if record.get("status") not in {"running", "failed"} or Path(record["workdir"]).resolve() != workdir:
            raise RuntimeError(f"Job record changed: {name}")
        pid_path = JOB_ROOT / job_id / "process.pid"
        if pid_path.is_file() and live(int(pid_path.read_text())):
            raise RuntimeError(f"Job runner still alive: {name}")
        marker = read_json(workdir / ".production-scf-recovery.json")
        if marker.get("state") != "prepared-fresh-atomic":
            raise RuntimeError(f"Expected fresh-SCF marker missing: {name}")
        incar = (workdir / "INCAR").read_text()
        if "ALGO = All" not in incar or "ISTART = 0" not in incar or "ICHARG = 2" not in incar:
            raise RuntimeError(f"Expected fresh solver missing: {name}")
        if (workdir / ".reboot-requeue-20261016.json").exists():
            raise RuntimeError(f"Job already requeued: {name}")
    return run, tasks


def apply() -> dict:
    lock_path = RUN_PATH.parent / ".production-recovery.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run, tasks = preflight()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = RUN_PATH.parent / ".production-scf-recovery-backups" / f"reboot-20261016-{stamp}"
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(RUN_PATH, backup / "run.json.before")
        markers = {}
        for name, old_job_id in TARGETS.items():
            workdir = Path(tasks[name]["workdir"])
            archive = workdir / ".production-scf-recovery-backups" / f"reboot-20261016-{stamp}"
            archive.mkdir(parents=True, exist_ok=False)
            for filename in ("INCAR", "POSCAR", "POTCAR", "KPOINTS", "input_manifest.json"):
                shutil.copy2(workdir / filename, archive / (filename + ".before"))
            for filename in ("job.json", "stdout.log", "stderr.log", "process.pid"):
                path = JOB_ROOT / old_job_id / filename
                if path.is_file():
                    shutil.copy2(path, archive / (filename + ".old-job"))
            hashes = {filename: digest(workdir / filename) for filename in ("POSCAR", "POTCAR", "KPOINTS", "INCAR")}
            moved = []
            for filename in MUTABLE:
                path = workdir / filename
                if path.is_file():
                    shutil.move(str(path), str(archive / filename))
                    moved.append(filename)
            if hashes != {filename: digest(workdir / filename) for filename in hashes}:
                raise RuntimeError(f"Input changed during archival: {name}")
            prior = get_job_status(old_job_id, root=JOB_ROOT)
            if prior.status not in {"failed", "cancelled"}:
                raise RuntimeError(f"Old job status not terminal: {name}: {prior.status}")
            markers[name] = {
                "task_id": name, "prior_job_id": old_job_id, "archive": str(archive),
                "archived_files": moved, "input_sha256": hashes,
                "reason": "Job runner was interrupted when server rebooted at 2026-10-16T04:43:16Z; no unverified VASP output is reused.",
                "prepared_at": datetime.now(timezone.utc).isoformat(),
            }
            (workdir / ".reboot-requeue-20261016.json").write_text(
                json.dumps(markers[name], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        current = read_json(RUN_PATH)
        current_tasks = {item["task_id"]: item for item in current["tasks"]}
        if current.get("max_parallel") != 2 or any(current_tasks[name].get("job_id") != job for name, job in TARGETS.items()):
            raise RuntimeError("Campaign changed during recovery")
        for name in TARGETS:
            current_tasks[name].update(status="pending", job_id=None, return_code=None, error=None,
                                       blocker=None, service_restart_recovery=markers[name])
        current["status"] = "running"
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        current.setdefault("recovery_history", []).append({
            "at": current["updated_at"], "reason": "Two VASP jobs interrupted by server reboot",
            "task_ids": list(TARGETS), "backup": str(backup),
        })
        temporary = RUN_PATH.with_name(f".{RUN_PATH.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, RUN_PATH)
        _launch_supervisor(RUN_ID, root=RUN_ROOT)
        return {"requeued": list(TARGETS), "backup": str(backup), "max_parallel": 2}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        print(json.dumps(apply(), ensure_ascii=False))
    else:
        run, tasks = preflight()
        print(json.dumps({"ready": True, "run_id": RUN_ID, "targets": TARGETS}, ensure_ascii=False))

