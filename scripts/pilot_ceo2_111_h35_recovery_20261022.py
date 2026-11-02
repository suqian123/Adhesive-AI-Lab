"""Independently test a clean electronic seed for the stalled hydroxylated (111) task.

This script never changes the campaign task or accepts a production result. Every
stage uses the original geometry, potentials, k-points and cutoff. A stage may
seed the next one only after normal VASP termination and final EDIFF convergence.
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

from ase.io import read as read_atoms

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adhesive_ai.vasp_checkpoint import electronic_converged
from adhesive_ai.vasp_production import validate_vasp_input_set

RUN_ID = "CL5-00016-1b4ebc3d36"
TASK_ID = "dft-o-111-v00-h35"
JOB_ID = "vasp-3ea271025d"
RUN_FILE = ROOT / "work" / "campaign_runs" / RUN_ID / "run.json"
PILOT = ROOT / "work" / "recovery_checks" / RUN_ID / "h35-20261022"
PHYSICAL = ("POSCAR", "POTCAR", "KPOINTS", "vdw_kernel.bindat")
STAGES = (
    ("01-fixed-charge", False, False, "Normal", "1E-4"),
    ("02-pbe", False, False, "All", "1E-4"),
    ("03-dftu", True, False, "All", "1E-6"),
    ("04-dftu-dipole", True, True, "All", "1E-6"),
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def source_task() -> Path:
    run = json.loads(RUN_FILE.read_text())
    if run.get("run_id") != RUN_ID or run.get("status") != "running":
        raise RuntimeError("Campaign state changed")
    matching = [item for item in run.get("tasks", []) if item.get("task_id") == TASK_ID]
    if len(matching) != 1 or matching[0].get("job_id") != JOB_ID:
        raise RuntimeError("Recovery target changed")
    task = matching[0]
    if task.get("status") != "running" or (task.get("conditions") or {}).get("facet") != "(111)":
        raise RuntimeError("Target is no longer the expected running (111) task")
    directory = Path(task["workdir"]).resolve()
    if not directory.is_relative_to(RUN_FILE.parent):
        raise RuntimeError("Target workdir is outside the campaign")
    report = validate_vasp_input_set(directory, resources=ROOT / "work" / "vasp_resources.json")
    if not report.get("valid"):
        raise RuntimeError(f"Original static input invalid: {report}")
    recovery = json.loads((directory / ".production-scf-recovery.json").read_text())
    expected = recovery.get("physical_input_sha256") or {}
    for name in ("POSCAR", "POTCAR", "KPOINTS"):
        if digest(directory / name) != expected.get(name):
            raise RuntimeError(f"Original {name} hash changed")
    return directory


def incar_for(source: str, *, lda_u: bool, dipole: bool, algo: str,
              ediff: str, stage_index: int, dipole_center: tuple[float, float, float]) -> str:
    override = {
        "NSW": "0", "IBRION": "-1", "NELM": "180" if stage_index == 0 else "240",
        "EDIFF": ediff, "ALGO": algo,
        "LREAL": ".FALSE." if stage_index == 0 else "Auto",
        "LWAVE": ".TRUE.", "LCHARG": ".TRUE.",
        "ISTART": "0" if stage_index == 0 else "1",
        "ICHARG": "12" if stage_index == 0 else "1",
        "LDIPOL": ".TRUE." if dipole else ".FALSE.",
        "AMIX": "0.05", "BMIX": "0.0001", "AMIX_MAG": "0.2",
        "BMIX_MAG": "0.0001", "AMIN": "0.01", "MAXMIX": "80",
    }
    if algo == "All":
        override.update(TIME="0.05", ISEARCH="1")
    if dipole:
        override.update(IDIPOL="3", DIPOL=" ".join(f"{v:.6f}" for v in dipole_center))
    skip = set(override) | {"NELMDL", "TIME", "ISEARCH", "IDIPOL", "DIPOL", "EDIFFG"}
    lines = []
    for line in source.splitlines():
        key = line.split("=", 1)[0].strip().upper() if "=" in line else ""
        if key in skip or (not lda_u and key.startswith("LDAU")):
            continue
        lines.append(line)
    lines.extend(f"{key} = {value}" for key, value in override.items())
    return "\n".join(lines) + "\n"


def prepare() -> dict:
    source = source_task()
    if PILOT.exists():
        raise RuntimeError(f"Pilot already exists: {PILOT}")
    atoms = read_atoms(source / "POSCAR")
    center = tuple(float(v) for v in atoms.get_center_of_mass(scaled=True))
    if not all(0 <= v <= 1 for v in center):
        raise RuntimeError(f"Invalid dipole center: {center}")
    PILOT.mkdir(parents=True, mode=0o700)
    original_incar = (source / "INCAR").read_text()
    for index, (name, lda_u, dipole, algo, ediff) in enumerate(STAGES):
        stage = PILOT / name
        stage.mkdir(mode=0o700)
        for filename in PHYSICAL:
            shutil.copy2(source / filename, stage / filename)
        (stage / "INCAR").write_text(incar_for(
            original_incar, lda_u=lda_u, dipole=dipole, algo=algo,
            ediff=ediff, stage_index=index, dipole_center=center,
        ))
    manifest = {
        "state": "prepared", "prepared_at": now(), "run_id": RUN_ID,
        "task_id": TASK_ID, "source_job_id": JOB_ID, "source_workdir": str(source),
        "source_hashes": {name: digest(source / name) for name in (*PHYSICAL, "INCAR")},
        "dipole_center_direct": center,
        "stages": [name for name, *_ in STAGES],
        "scientific_scope": "Isolated electronic-seed diagnostic; not production approval",
    }
    write_json(PILOT / "pilot.json", manifest)
    return {"prepared": True, "pilot": str(PILOT), "dipole_center_direct": center}


def run() -> int:
    manifest_path = PILOT / "pilot.json"
    manifest = json.loads(manifest_path.read_text())
    source = Path(manifest["source_workdir"])
    for name in PHYSICAL:
        if digest(source / name) != manifest["source_hashes"][name]:
            raise RuntimeError(f"Source physical input changed: {name}")
    if manifest.get("state") != "prepared":
        raise RuntimeError(f"Pilot has already run: {manifest.get('state')}")
    with (PILOT / ".run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest.update(state="running", started_at=now(), runner_pid=os.getpid())
        write_json(manifest_path, manifest)
        try:
            for index, (name, *_rest) in enumerate(STAGES):
                stage = PILOT / name
                if index:
                    previous = PILOT / STAGES[index - 1][0]
                    if not electronic_converged(previous):
                        raise RuntimeError(f"Previous stage is not converged: {previous.name}")
                    for filename in ("CHGCAR", "WAVECAR"):
                        seed = previous / filename
                        if not seed.is_file() or not seed.stat().st_size:
                            raise RuntimeError(f"Previous stage lacks {filename}")
                        shutil.copy2(seed, stage / filename)
                manifest.update(current_stage=name, stage_started_at=now())
                write_json(manifest_path, manifest)
                command = [str(ROOT / ".venv" / "bin" / "python"),
                           str(ROOT / "deploy" / "compute" / "mpi_launch.py"),
                           "--engine", "vasp", "--ranks", "16"]
                with (stage / "stage.stdout.log").open("wb") as stdout, (stage / "stage.stderr.log").open("wb") as stderr:
                    completed = subprocess.run(command, cwd=stage, stdin=subprocess.DEVNULL,
                                               stdout=stdout, stderr=stderr, check=False)
                passed = completed.returncode == 0 and electronic_converged(stage)
                write_json(stage / "stage_status.json", {
                    "state": "passed" if passed else "failed", "finished_at": now(),
                    "return_code": completed.returncode, "electronic_converged": passed,
                })
                if not passed:
                    raise RuntimeError(f"{name} failed its electronic convergence gate")
            manifest.update(state="ready-for-reviewed-production-restart", finished_at=now(), current_stage=None)
            write_json(manifest_path, manifest)
            print(json.dumps({"pilot": str(PILOT), "state": manifest["state"]}), flush=True)
            return 0
        except Exception as exc:
            manifest.update(state="failed", finished_at=now(), error=str(exc))
            write_json(manifest_path, manifest)
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    action = parser.parse_args().action
    if action == "prepare":
        print(json.dumps(prepare(), ensure_ascii=False))
    else:
        raise SystemExit(run())
