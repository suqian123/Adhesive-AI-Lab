"""Test the last complete h00 geometry without reusing interrupted SCF output.

The source CONTCAR is a geometry-only starting point. This isolated static
calculation cannot advance the campaign or approve a production result.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from ase.io import read as read_atoms
from ase.neighborlist import neighbor_list

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adhesive_ai.vasp_checkpoint import electronic_converged
from adhesive_ai.vasp_production import validate_vasp_input_set

RUN_ID = "CL5-00016-1b4ebc3d36"
TASK_ID = "dft-o-111-v00-h00"
JOB_ID = "vasp-1d70bc8b98"
RUN_PATH = ROOT / "work" / "campaign_runs" / RUN_ID / "run.json"
PILOT = ROOT / "work" / "recovery_checks" / RUN_ID / "h00-contcar-scf-20261027"
PHYSICAL = ("POTCAR", "KPOINTS", "vdw_kernel.bindat")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: dict) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temp, path)


def source_task() -> Path:
    run = json.loads(RUN_PATH.read_text())
    if (run.get("run_id") != RUN_ID
            or run.get("status") not in {"running", "failed"}
            or (run.get("status") == "failed" and not run.get("recovery_hold_20261027"))):
        raise RuntimeError("Campaign state changed")
    matches = [task for task in run["tasks"] if task.get("task_id") == TASK_ID]
    if len(matches) != 1 or matches[0].get("job_id") != JOB_ID:
        raise RuntimeError("Original h00 task changed")
    if matches[0].get("status") not in {"running", "failed"}:
        raise RuntimeError("Original h00 state changed")
    source = Path(matches[0]["workdir"]).resolve()
    if not source.is_relative_to(RUN_PATH.parent):
        raise RuntimeError("Original h00 workdir outside campaign")
    job_dir = ROOT / "work" / "jobs" / JOB_ID
    job = json.loads((job_dir / "job.json").read_text())
    if job.get("status") != "failed" or Path(job["workdir"]).resolve() != source:
        raise RuntimeError("Original h00 job is not the expected failed attempt")
    pid_file = job_dir / "process.pid"
    if pid_file.exists() and Path(f"/proc/{pid_file.read_text().strip()}").exists():
        raise RuntimeError("Original h00 runner may still be alive")
    report = validate_vasp_input_set(source, resources=ROOT / "work" / "vasp_resources.json")
    if not report.get("valid"):
        raise RuntimeError(f"Original static input invalid: {report}")
    marker = json.loads((source / ".production-scf-recovery.json").read_text())
    if marker.get("state") != "prepared-fresh-atomic":
        raise RuntimeError("Unexpected original production recovery marker")
    for name in ("POSCAR", "POTCAR", "KPOINTS"):
        if digest(source / name) != marker["physical_input_sha256"].get(name):
            raise RuntimeError(f"Original physical input changed: {name}")
    return source


def valid_geometry(source: Path) -> tuple[float, float, float]:
    seed = source / "CONTCAR"
    if not seed.is_file() or not seed.stat().st_size:
        raise RuntimeError("Original h00 CONTCAR missing")
    original = read_atoms(source / "POSCAR")
    geometry = read_atoms(seed)
    if (len(original) != len(geometry) or
            original.get_chemical_symbols() != geometry.get_chemical_symbols() or
            not (abs(original.cell - geometry.cell) < 1e-5).all()):
        raise RuntimeError("CONTCAR composition or cell changed")
    close_i, close_j = neighbor_list("ij", geometry, 0.90)
    if len(close_i) or len(close_j):
        raise RuntimeError("CONTCAR has interatomic contacts below 0.90 A")
    steps = re.findall(r"^\s*(\d+)\s+F=", (source / "OSZICAR").read_text(errors="replace"), re.M)
    if not steps or int(steps[-1]) < 66:
        raise RuntimeError("Expected completed ionic-step history missing")
    center = tuple(float(value) for value in geometry.get_center_of_mass(scaled=True))
    if not all(0 <= value <= 1 for value in center):
        raise RuntimeError(f"Invalid dipole center: {center}")
    return center


def static_incar(source: str, center: tuple[float, float, float]) -> str:
    overrides = {
        "NSW": "0", "IBRION": "-1", "NELM": "240", "EDIFF": "1E-6",
        "ISTART": "0", "ICHARG": "2", "LWAVE": ".TRUE.", "LCHARG": ".TRUE.",
        "LDIPOL": ".TRUE.", "IDIPOL": "3",
        "DIPOL": " ".join(f"{value:.6f}" for value in center),
    }
    skip = set(overrides) | {"EDIFFG"}
    lines = [line for line in source.splitlines()
             if "=" not in line or line.split("=", 1)[0].strip().upper() not in skip]
    lines.extend(f"{key} = {value}" for key, value in overrides.items())
    return "\n".join(lines) + "\n"


def prepare() -> dict:
    source = source_task()
    center = valid_geometry(source)
    if PILOT.exists():
        raise RuntimeError(f"Pilot already exists: {PILOT}")
    if shutil.disk_usage(PILOT.parent).free < 10 * 1024**3:
        raise RuntimeError("Insufficient disk for independent diagnostic")
    PILOT.mkdir(mode=0o700)
    try:
        shutil.copy2(source / "CONTCAR", PILOT / "POSCAR")
        for name in (*PHYSICAL, "input_manifest.json"):
            shutil.copy2(source / name, PILOT / name)
        (PILOT / "INCAR").write_text(static_incar((source / "INCAR").read_text(), center))
        report = validate_vasp_input_set(PILOT, resources=ROOT / "work" / "vasp_resources.json")
        if not report.get("valid"):
            raise RuntimeError(f"Diagnostic static input invalid: {report}")
        manifest = {
            "state": "prepared", "prepared_at": now(), "run_id": RUN_ID,
            "task_id": TASK_ID, "source_job_id": JOB_ID, "source_workdir": str(source),
            "source_contcar_sha256": digest(source / "CONTCAR"),
            "pilot_poscar_sha256": digest(PILOT / "POSCAR"),
            "physical_sha256": {name: digest(PILOT / name) for name in PHYSICAL},
            "incar_sha256": digest(PILOT / "INCAR"),
            "dipole_center_direct": center,
            "scientific_scope": "Geometry-only recovery diagnostic; no production approval or candidate writeback",
        }
        write_json(PILOT / "pilot.json", manifest)
        return {"state": "prepared", "directory": str(PILOT),
                "dipole_center_direct": center, "static_checks": report["checks"]}
    except Exception:
        shutil.rmtree(PILOT)
        raise


def run() -> int:
    manifest_path = PILOT / "pilot.json"
    with (PILOT / ".run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("state") != "prepared":
            raise RuntimeError(f"Pilot state is {manifest.get('state')}")
        source = source_task()
        if source != Path(manifest["source_workdir"]) or digest(source / "CONTCAR") != manifest["source_contcar_sha256"]:
            raise RuntimeError("Geometry source changed")
        for name, expected in {"POSCAR": manifest["pilot_poscar_sha256"],
                               "INCAR": manifest["incar_sha256"],
                               **manifest["physical_sha256"]}.items():
            if digest(PILOT / name) != expected:
                raise RuntimeError(f"Diagnostic input changed: {name}")
        manifest.update(state="running", started_at=now(), runner_pid=os.getpid())
        write_json(manifest_path, manifest)
        try:
            command = [str(ROOT / ".venv" / "bin" / "python"),
                       str(ROOT / "deploy" / "compute" / "mpi_launch.py"),
                       "--engine", "vasp", "--ranks", "16"]
            with (PILOT / "stage.stdout.log").open("wb") as stdout, \
                 (PILOT / "stage.stderr.log").open("wb") as stderr:
                result = subprocess.run(command, cwd=PILOT, stdin=subprocess.DEVNULL,
                                        stdout=stdout, stderr=stderr, check=False)
            passed = result.returncode == 0 and electronic_converged(PILOT)
            manifest.update(state="ready-for-reviewed-production-restart" if passed else "failed",
                            finished_at=now(), return_code=result.returncode,
                            electronic_converged=passed)
            write_json(manifest_path, manifest)
            print(json.dumps({"state": manifest["state"], "return_code": result.returncode,
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
