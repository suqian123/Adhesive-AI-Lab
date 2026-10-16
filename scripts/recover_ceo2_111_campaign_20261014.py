"""Controlled fresh-SCF retry of the seven failed/divergent CeO2(111) campaign jobs.

This one-off recovery is pinned to a single campaign and its original job IDs.
It archives old outputs, retains the original physical inputs, starts the
waiting (110) validation first, and then lets the campaign supervisor requeue
the (111) production jobs with a direct electronic optimizer.
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
import signal
import subprocess
import sys
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from dotenv import load_dotenv

from adhesive_ai.campaign_runner import (
    _update_incar_settings,
    restart_vasp_convergence,
    vasp_convergence_progress,
    vasp_validation_root_for_facet,
)
from adhesive_ai.jobs import cancel_job
from adhesive_ai.vasp_production import validate_vasp_input_set


RUN_ID = "CL5-00016-1b4ebc3d36"
RUN_PATH = PROJECT / "work" / "campaign_runs" / RUN_ID / "run.json"
EXPECTED = {
    "dft-o-111-v00-h00": ("failed", "vasp-1c8d96d642"),
    "dft-o-111-v00-h35": ("running", "vasp-f9a71c26ea"),
    "dft-o-111-v08-h00": ("running", "vasp-6bb6382f03"),
    "dft-o-111-v08-h35": ("running", "vasp-0c92877a9c"),
    "dft-o-111-v15-h00": ("running", "vasp-30b8f71882"),
    "dft-o-111-v15-h35": ("running", "vasp-7237053145"),
    "dft-pda-binding-111": ("running", "vasp-03275cbce7"),
}
SETTINGS = {
    "ALGO": "All", "TIME": "0.1", "ISEARCH": "1", "NELM": "240",
    "ISTART": "0", "ICHARG": "2", "LWAVE": ".TRUE.",
}
MUTABLE = (
    "OUTCAR", "OSZICAR", "vasprun.xml", "CONTCAR", "WAVECAR", "CHGCAR", "CHG",
    "DOSCAR", "EIGENVAL", "IBZKPT", "PCDAT", "PROCAR", "REPORT", "XDATCAR",
    "STOPCAR", "vasp.stdout.log", "vasp.stderr.log",
)
RESOURCE_CONFIG = PROJECT / "work" / "vasp_resources.json"


def _run() -> dict:
    return json.loads(RUN_PATH.read_text(encoding="utf-8"))


def _tasks(run: dict) -> dict[str, dict]:
    return {item["task_id"]: item for item in run["tasks"] if item["task_id"] in EXPECTED}


def _live(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _vasp_workdirs() -> set[Path]:
    result = subprocess.run(["pgrep", "-x", "vasp_std"], capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError("Cannot inspect VASP processes")
    workdirs = set()
    for value in result.stdout.split():
        try:
            workdirs.add(Path(os.readlink(f"/proc/{value}/cwd")).resolve())
        except OSError:
            continue
    return workdirs


def _supervisor_pid() -> int:
    matches = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            command = (proc / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if b"_supervise(" in command and RUN_ID.encode("ascii") in command:
            matches.append(int(proc.name))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one campaign supervisor, found {matches}")
    return matches[0]


def preflight() -> tuple[dict, dict[str, dict], Path, int]:
    run = _run()
    tasks = _tasks(run)
    if set(tasks) != set(EXPECTED) or run.get("run_id") != RUN_ID or run.get("max_parallel") != 6:
        raise RuntimeError("Unexpected campaign identity or task set")
    job_root = Path(run["job_root"]).resolve()
    if job_root != (PROJECT / "work" / "jobs").resolve():
        raise RuntimeError("Unexpected job root")
    for name, (expected_status, expected_job_id) in EXPECTED.items():
        task = tasks[name]
        if task.get("status") != expected_status or task.get("job_id") != expected_job_id:
            raise RuntimeError(f"{name}: campaign state changed")
        if (task.get("conditions") or {}).get("facet") != "(111)":
            raise RuntimeError(f"{name}: unexpected facet")
        workdir = Path(task["workdir"]).resolve()
        if not workdir.is_relative_to(PROJECT / "work" / "campaign_runs" / RUN_ID):
            raise RuntimeError(f"{name}: workdir outside campaign")
        incar = (workdir / "INCAR").read_text(encoding="utf-8")
        if "ALGO = Normal" not in incar or "NELM = 160" not in incar or "NCORE = 4" not in incar:
            raise RuntimeError(f"{name}: unexpected original solver")
        if (workdir / ".production-scf-recovery.json").exists():
            raise RuntimeError(f"{name}: recovery already prepared")
        validation = validate_vasp_input_set(workdir, resources=RESOURCE_CONFIG)
        if not validation["valid"]:
            raise RuntimeError(f"{name}: invalid physical input {validation}")
        job = json.loads((job_root / expected_job_id / "job.json").read_text(encoding="utf-8"))
        if job.get("status") != expected_status or Path(job["workdir"]).resolve() != workdir:
            raise RuntimeError(f"{name}: job record changed")
    workdirs = {Path(item["workdir"]).resolve() for item in tasks.values()}
    if not _vasp_workdirs() <= workdirs:
        raise RuntimeError("VASP has work outside the targeted campaign")
    progress110 = vasp_convergence_progress(vasp_validation_root_for_facet("(110)"))
    if (progress110.get("approved") or progress110.get("active") or progress110.get("failed")
            or progress110.get("total") != 14):
        raise RuntimeError(f"Unexpected (110) validation state: {progress110}")
    return run, tasks, job_root, _supervisor_pid()


def _wait_stopped(tasks: dict[str, dict], job_root: Path, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    target_dirs = {Path(item["workdir"]).resolve() for item in tasks.values()}
    while time.monotonic() < deadline:
        active = []
        for name, task in tasks.items():
            if EXPECTED[name][0] != "running":
                continue
            path = job_root / task["job_id"] / "process.pid"
            if path.exists() and _live(int(path.read_text(encoding="ascii"))):
                active.append(name)
        running_vasp = _vasp_workdirs() & target_dirs
        if not active and not running_vasp:
            return
        time.sleep(1)
    raise RuntimeError(f"Old processes did not stop: runners={active}, vasp={running_vasp}")


def _prepare_job(name: str, task: dict, job_root: Path, stamp: str) -> dict:
    workdir = Path(task["workdir"]).resolve()
    archive = workdir / ".production-scf-recovery-backups" / stamp
    archive.mkdir(parents=True, exist_ok=False)
    for filename in ("INCAR", "POSCAR", "POTCAR", "KPOINTS", "input_manifest.json"):
        shutil.copy2(workdir / filename, archive / (filename + ".before"))
    old_job_dir = job_root / task["job_id"]
    for filename in ("job.json", "stdout.log", "stderr.log", "process.pid"):
        path = old_job_dir / filename
        if path.is_file():
            shutil.copy2(path, archive / (filename + ".old-job"))
    physical_hashes = {
        filename: hashlib.sha256((workdir / filename).read_bytes()).hexdigest()
        for filename in ("POSCAR", "POTCAR", "KPOINTS")
    }
    moved = []
    for filename in MUTABLE:
        path = workdir / filename
        if path.is_file():
            shutil.move(str(path), str(archive / filename))
            moved.append(filename)
    _update_incar_settings(workdir / "INCAR", SETTINGS)
    if physical_hashes != {
        filename: hashlib.sha256((workdir / filename).read_bytes()).hexdigest()
        for filename in physical_hashes
    }:
        raise RuntimeError(f"{name}: physical input changed")
    validation = validate_vasp_input_set(workdir, resources=RESOURCE_CONFIG)
    if not validation["valid"]:
        raise RuntimeError(f"{name}: post-recovery input invalid: {validation}")
    marker = {
        "task_id": name, "attempt": 1, "state": "prepared-fresh-atomic",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "prior_job_id": task["job_id"], "prior_status": task["status"],
        "archive": str(archive), "archived_files": moved,
        "settings": SETTINGS, "physical_input_sha256": physical_hashes,
        "checkpoint_policy": "Original POSCAR and atomic charge; prior CHGCAR/WAVECAR/CONTCAR are not reused.",
    }
    (workdir / ".production-scf-recovery.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return marker


def apply() -> dict:
    load_dotenv(PROJECT / ".env", override=True)
    lock_path = PROJECT / "work" / "campaign_runs" / RUN_ID / ".production-recovery.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run, tasks, job_root, supervisor = preflight()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = RUN_PATH.parent / ".production-scf-recovery-backups" / f"campaign111-{stamp}"
        backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(RUN_PATH, backup / "run.json.before")
        os.kill(supervisor, signal.SIGSTOP)
        try:
            current = _run()
            for name in EXPECTED:
                if (current["tasks"][next(i for i, item in enumerate(current["tasks"]) if item["task_id"] == name)]
                        .get("job_id") != tasks[name]["job_id"]):
                    raise RuntimeError("Campaign changed while pausing supervisor")
            for name in ("dft-o-111-v08-h35", "dft-o-111-v15-h35", "dft-pda-binding-111",
                         "dft-o-111-v00-h35", "dft-o-111-v08-h00", "dft-o-111-v15-h00"):
                cancelled = cancel_job(tasks[name]["job_id"], root=job_root)
                if cancelled.status != "cancelled":
                    raise RuntimeError(f"Could not cancel {name}")
            _wait_stopped(tasks, job_root)
            markers = [_prepare_job(name, tasks[name], job_root, stamp) for name in EXPECTED]
            current = _run()
            for item in current["tasks"]:
                if item["task_id"] not in EXPECTED:
                    continue
                marker = next(value for value in markers if value["task_id"] == item["task_id"])
                item.update(status="pending", job_id=None, return_code=None, error=None, blocker=None,
                            production_scf_recovery={"attempt": 1, "archive": marker["archive"],
                                                     "prior_job_id": marker["prior_job_id"]})
            current["status"] = "running"
            current.setdefault("recovery_history", []).append({
                "at": datetime.now(timezone.utc).isoformat(), "reason": "Normal SCF divergence and EDDDAV/ZHEGV failure",
                "task_ids": list(EXPECTED), "settings": SETTINGS, "backup": str(backup),
            })
            current["updated_at"] = datetime.now(timezone.utc).isoformat()
            temporary = RUN_PATH.with_name("." + RUN_PATH.name + "." + uuid.uuid4().hex + ".tmp")
            temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, RUN_PATH)
            root110 = vasp_validation_root_for_facet("(110)")
            launch110 = restart_vasp_convergence(root110, facet="(110)")
            if not launch110.get("started"):
                raise RuntimeError(f"(110) validation did not start: {launch110}")
            return {"prepared": len(markers), "campaign_backup": str(backup),
                    "validation_110_started": True, "supervisor_pid": supervisor,
                    "settings": SETTINGS}
        finally:
            if _live(supervisor):
                os.kill(supervisor, signal.SIGCONT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    if arguments.apply:
        print(json.dumps(apply(), ensure_ascii=False))
    else:
        load_dotenv(PROJECT / ".env", override=True)
        run, tasks, job_root, supervisor = preflight()
        print(json.dumps({"ready": True, "run_id": RUN_ID, "supervisor_pid": supervisor,
                          "target_tasks": list(tasks), "job_root": str(job_root)}, ensure_ascii=False))
