"""Retry the h35 DFT+U/dipole SCF from the converged PBE seed.

The retry is isolated from the running production job. Success only marks the
diagnostic ready for review; it never advances the campaign or writes candidates.
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
from adhesive_ai.vasp_checkpoint import electronic_converged
from adhesive_ai.vasp_production import validate_vasp_input_set

RUN_ID = "CL5-00016-1b4ebc3d36"
TASK_ID = "dft-o-111-v00-h35"
JOB_ID = "vasp-3ea271025d"
RUN_FILE = ROOT / "work" / "campaign_runs" / RUN_ID / "run.json"
OLD_PILOT = ROOT / "work" / "recovery_checks" / RUN_ID / "h35-20261022"
RETRY = ROOT / "work" / "recovery_checks" / RUN_ID / "h35-dftu-dipole-20261027"
INTERRUPTED = ROOT / "work" / "recovery_checks" / RUN_ID / "h35-dftu-dipole-20261026"
PHYSICAL = ("POSCAR", "POTCAR", "KPOINTS", "vdw_kernel.bindat")
SEEDS = ("CHGCAR", "WAVECAR")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def source_task() -> Path:
    run = json.loads(RUN_FILE.read_text())
    if (run.get("run_id") != RUN_ID
            or run.get("status") not in {"running", "failed"}
            or (run.get("status") == "failed" and not run.get("recovery_hold_20261027"))):
        raise RuntimeError("Campaign state changed")
    matches = [task for task in run.get("tasks", []) if task.get("task_id") == TASK_ID]
    if len(matches) != 1:
        raise RuntimeError("Recovery target missing or duplicated")
    task = matches[0]
    if task.get("status") not in {"running", "failed"} or task.get("job_id") != JOB_ID:
        raise RuntimeError("Recovery target changed")
    job_dir = ROOT / "work" / "jobs" / JOB_ID
    job = json.loads((job_dir / "job.json").read_text())
    if job.get("status") != "failed":
        raise RuntimeError("Original production job is not terminal")
    pid_file = job_dir / "process.pid"
    if pid_file.exists() and Path(f"/proc/{pid_file.read_text().strip()}").exists():
        raise RuntimeError("Original production runner may still be alive")
    source = Path(task["workdir"]).resolve()
    if not source.is_relative_to(RUN_FILE.parent):
        raise RuntimeError("Recovery target outside campaign")
    report = validate_vasp_input_set(source, resources=ROOT / "work" / "vasp_resources.json")
    if not report.get("valid"):
        raise RuntimeError(f"Production input static validation failed: {report}")
    return source


def validate_old_pilot(source: Path) -> tuple[Path, Path, dict]:
    interrupted = json.loads((INTERRUPTED / "retry.json").read_text())
    interrupted_pid = interrupted.get("runner_pid")
    if interrupted.get("state") != "running" or electronic_converged(INTERRUPTED):
        raise RuntimeError("Interrupted dipole retry state changed")
    if interrupted_pid and Path(f"/proc/{interrupted_pid}").exists():
        raise RuntimeError("Previous dipole retry runner may still be alive")
    old = json.loads((OLD_PILOT / "pilot.json").read_text())
    if old.get("state") != "failed" or old.get("source_job_id") != JOB_ID:
        raise RuntimeError("Unexpected original pilot state or job")
    for name in PHYSICAL:
        if digest(source / name) != old["source_hashes"].get(name):
            raise RuntimeError(f"Production physical input changed: {name}")
    pbe = OLD_PILOT / "02-pbe"
    failed_dftu = OLD_PILOT / "03-dftu"
    template = OLD_PILOT / "04-dftu-dipole"
    if not electronic_converged(pbe) or electronic_converged(failed_dftu):
        raise RuntimeError("Prior PBE/DFT+U convergence state changed")
    if (template / "OUTCAR").exists():
        raise RuntimeError("Dipole template has already been run")
    for name in PHYSICAL:
        expected = old["source_hashes"][name]
        if digest(pbe / name) != expected or digest(template / name) != expected:
            raise RuntimeError(f"Pilot physical input mismatch: {name}")
    for name in SEEDS:
        if not (pbe / name).is_file() or not (pbe / name).stat().st_size:
            raise RuntimeError(f"Converged PBE lacks {name}")
    incar = (template / "INCAR").read_text()
    required = ("LDAU = .TRUE.", "LDAUU = 4.5 0 0", "LMAXMIX = 6",
                "LDIPOL = .TRUE.", "IDIPOL = 3", "DIPOL = ",
                "ISTART = 1", "ICHARG = 1", "NSW = 0", "EDIFF = 1E-6")
    if any(tag not in incar for tag in required):
        raise RuntimeError("Dipole template INCAR lacks required settings")
    return pbe, template, old


def prepare() -> dict:
    source = source_task()
    pbe, template, old = validate_old_pilot(source)
    if RETRY.exists():
        raise RuntimeError(f"Retry already exists: {RETRY}")
    if shutil.disk_usage(RETRY.parent).free < 16 * 1024**3:
        raise RuntimeError("Insufficient free disk for independent checkpoints")
    RETRY.mkdir(mode=0o700)
    try:
        for name in (*PHYSICAL, "input_manifest.json"):
            shutil.copy2(source / name, RETRY / name)
        shutil.copy2(template / "INCAR", RETRY / "INCAR")
        report = validate_vasp_input_set(RETRY, resources=ROOT / "work" / "vasp_resources.json")
        if not report.get("valid"):
            raise RuntimeError(f"Retry static validation failed: {report}")
        for name in SEEDS:
            shutil.copy2(pbe / name, RETRY / name)
            if (RETRY / name).stat().st_size != (pbe / name).stat().st_size:
                raise RuntimeError(f"PBE checkpoint copy incomplete: {name}")
        manifest = {
            "state": "prepared", "prepared_at": now(), "run_id": RUN_ID,
            "task_id": TASK_ID, "source_job_id": JOB_ID,
            "source_workdir": str(source), "pbe_source": str(pbe),
            "source_physical_sha256": {name: old["source_hashes"][name] for name in PHYSICAL},
            "seed_sizes": {name: (RETRY / name).stat().st_size for name in SEEDS},
            "input_sha256": digest(RETRY / "INCAR"),
            "scientific_scope": "Post-shutdown isolated SCF diagnostic; not production approval or candidate writeback",
            "interrupted_retry": str(INTERRUPTED),
        }
        write_json(RETRY / "retry.json", manifest)
        return {"state": manifest["state"], "directory": str(RETRY),
                "seed_sizes": manifest["seed_sizes"], "static_checks": report["checks"]}
    except Exception:
        # Never leave a partial directory that could be mistaken for a valid retry.
        shutil.rmtree(RETRY)
        raise


def run() -> int:
    manifest_path = RETRY / "retry.json"
    with (RETRY / ".run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("state") != "prepared":
            raise RuntimeError(f"Retry state is {manifest.get('state')}")
        source = source_task()
        if source != Path(manifest["source_workdir"]):
            raise RuntimeError("Recovery target workdir changed")
        pbe, _, _ = validate_old_pilot(source)
        if pbe != Path(manifest["pbe_source"]):
            raise RuntimeError("PBE source changed")
        for name in PHYSICAL:
            if digest(RETRY / name) != manifest["source_physical_sha256"][name]:
                raise RuntimeError(f"Retry physical input changed: {name}")
        if digest(RETRY / "INCAR") != manifest["input_sha256"]:
            raise RuntimeError("Retry INCAR changed")
        for name in SEEDS:
            if (RETRY / name).stat().st_size != manifest["seed_sizes"][name]:
                raise RuntimeError(f"Retry seed changed: {name}")
        manifest.update(state="running", started_at=now(), runner_pid=os.getpid())
        write_json(manifest_path, manifest)
        try:
            command = [str(ROOT / ".venv" / "bin" / "python"),
                       str(ROOT / "deploy" / "compute" / "mpi_launch.py"),
                       "--engine", "vasp", "--ranks", "16"]
            with (RETRY / "stage.stdout.log").open("wb") as stdout, \
                 (RETRY / "stage.stderr.log").open("wb") as stderr:
                completed = subprocess.run(command, cwd=RETRY, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, check=False)
            passed = completed.returncode == 0 and electronic_converged(RETRY)
            manifest.update(state="ready-for-reviewed-production-restart" if passed else "failed",
                            finished_at=now(), return_code=completed.returncode,
                            electronic_converged=passed)
            write_json(manifest_path, manifest)
            print(json.dumps({"state": manifest["state"], "return_code": completed.returncode,
                              "electronic_converged": passed}), flush=True)
            return 0 if passed else 1
        except Exception as exc:
            manifest.update(state="failed", finished_at=now(), error=str(exc))
            write_json(manifest_path, manifest)
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    action = parser.parse_args().action
    if action == "prepare":
        print(json.dumps(prepare(), ensure_ascii=False), flush=True)
    else:
        raise SystemExit(run())
