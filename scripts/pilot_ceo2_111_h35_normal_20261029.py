"""Isolated CeO2(111) h35 DFT+U SCF test using ALGO=Normal.

Seed only from the electronically converged PBE stage. Never modify the
campaign, approve production, or write candidate data.
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
PILOT = ROOT / "work" / "recovery_checks" / RUN_ID / "h35-20261022"
TARGET = ROOT / "work" / "recovery_checks" / RUN_ID / "h35-dftu-normal-20261029"
PHYSICAL = ("POSCAR", "POTCAR", "KPOINTS", "vdw_kernel.bindat")
SEEDS = ("CHGCAR", "WAVECAR")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def source_and_seed() -> tuple[Path, Path, Path, dict]:
    run = json.loads(RUN_FILE.read_text())
    if run.get("run_id") != RUN_ID:
        raise RuntimeError("Campaign identity changed")
    matches = [item for item in run.get("tasks", []) if item.get("task_id") == TASK_ID]
    if len(matches) != 1 or matches[0].get("status") != "failed" or matches[0].get("job_id") != JOB_ID:
        raise RuntimeError("Failed h35 production task state changed")
    job = json.loads((ROOT / "work" / "jobs" / JOB_ID / "job.json").read_text())
    if job.get("status") != "failed":
        raise RuntimeError("Original production job is not terminal")
    source = Path(matches[0]["workdir"]).resolve()
    if not source.is_relative_to(RUN_FILE.parent):
        raise RuntimeError("Production source is outside campaign")
    original_report = validate_vasp_input_set(source, resources=ROOT / "work" / "vasp_resources.json")
    if not original_report.get("valid"):
        raise RuntimeError(f"Original physical input failed static validation: {original_report}")

    pilot = json.loads((PILOT / "pilot.json").read_text())
    if pilot.get("source_job_id") != JOB_ID:
        raise RuntimeError("PBE pilot identity changed")
    pbe = PILOT / "02-pbe"
    template = PILOT / "03-dftu"
    if not electronic_converged(pbe):
        raise RuntimeError("PBE seed is not electronically converged")
    for name in PHYSICAL:
        if digest(source / name) != pilot["source_hashes"].get(name) or digest(pbe / name) != pilot["source_hashes"].get(name):
            raise RuntimeError(f"Physical input changed: {name}")
    for name in SEEDS:
        if not (pbe / name).is_file() or (pbe / name).stat().st_size == 0:
            raise RuntimeError(f"PBE seed missing: {name}")
    return source, pbe, template, pilot


def normal_incar(template: Path) -> str:
    lines = (template / "INCAR").read_text().splitlines()
    updated = []
    changed = False
    for line in lines:
        if line.split("=", 1)[0].strip().upper() == "ALGO":
            updated.append("ALGO = Normal")
            changed = True
        else:
            updated.append(line)
    result = "\n".join(updated) + "\n"
    required = ("LDAU = .TRUE.", "LDAUU = 4.5 0 0", "LMAXMIX = 6", "LDIPOL = .FALSE.",
                "ISTART = 1", "ICHARG = 1", "NSW = 0", "NELM = 240", "EDIFF = 1E-6")
    if not changed or any(tag not in result for tag in required):
        raise RuntimeError("DFT+U template lacks expected physical settings")
    return result


def prepare() -> dict:
    source, pbe, template, pilot = source_and_seed()
    if TARGET.exists():
        raise RuntimeError(f"Diagnostic already exists: {TARGET}")
    if shutil.disk_usage(TARGET.parent).free < 16 * 1024**3:
        raise RuntimeError("Insufficient free disk for independent diagnostic")
    TARGET.mkdir(mode=0o700)
    try:
        for name in (*PHYSICAL, "input_manifest.json"):
            shutil.copy2(source / name, TARGET / name)
        (TARGET / "INCAR").write_text(normal_incar(template))
        report = validate_vasp_input_set(TARGET, resources=ROOT / "work" / "vasp_resources.json")
        checks = report.get("checks") or {}
        diagnostic_tags = ("LDAU = .TRUE.", "IVDW = 12", "GGA = PE", "LDIPOL = .FALSE.")
        incar = (TARGET / "INCAR").read_text()
        if (
            any(not value for name, value in checks.items() if name != "required_incar_tags")
            or checks.get("required_incar_tags") is not False
            or any(tag not in incar for tag in diagnostic_tags)
        ):
            raise RuntimeError(f"Diagnostic static validation failed beyond expected dipole hold: {report}")
        for name in SEEDS:
            shutil.copy2(pbe / name, TARGET / name)
            if (TARGET / name).stat().st_size != (pbe / name).stat().st_size:
                raise RuntimeError(f"Incomplete PBE seed copy: {name}")
        manifest = {
            "state": "prepared", "prepared_at": now(), "run_id": RUN_ID,
            "task_id": TASK_ID, "source_job_id": JOB_ID,
            "source_workdir": str(source), "pbe_source": str(pbe),
            "physical_sha256": {name: pilot["source_hashes"][name] for name in PHYSICAL},
            "seed_sizes": {name: (TARGET / name).stat().st_size for name in SEEDS},
            "incar_sha256": digest(TARGET / "INCAR"),
            "static_checks": report.get("checks"),
            "static_diagnostic_exception": "Only the production LDIPOL=.TRUE. requirement is held for this pre-dipole SCF pilot",
            "scientific_scope": "Isolated DFT+U SCF diagnostic; not dipole-stage or production approval",
        }
        write_json(TARGET / "retry.json", manifest)
        return {"state": "prepared", "directory": str(TARGET), "static_checks": report.get("checks")}
    except Exception:
        shutil.rmtree(TARGET)
        raise


def run() -> int:
    with (TARGET / ".run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest_path = TARGET / "retry.json"
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("state") != "prepared":
            raise RuntimeError(f"Diagnostic state is {manifest.get('state')}")
        source, pbe, _, _ = source_and_seed()
        if source != Path(manifest["source_workdir"]) or pbe != Path(manifest["pbe_source"]):
            raise RuntimeError("Diagnostic source changed")
        for name in PHYSICAL:
            if digest(TARGET / name) != manifest["physical_sha256"][name]:
                raise RuntimeError(f"Diagnostic physical input changed: {name}")
        if digest(TARGET / "INCAR") != manifest["incar_sha256"]:
            raise RuntimeError("Diagnostic INCAR changed")
        for name in SEEDS:
            if (TARGET / name).stat().st_size != manifest["seed_sizes"][name]:
                raise RuntimeError(f"Diagnostic seed changed: {name}")
        manifest.update(state="running", started_at=now(), runner_pid=os.getpid())
        write_json(manifest_path, manifest)
        try:
            command = [str(ROOT / ".venv" / "bin" / "python"),
                       str(ROOT / "deploy" / "compute" / "mpi_launch.py"),
                       "--engine", "vasp", "--ranks", "16"]
            with (TARGET / "stage.stdout.log").open("wb") as stdout, \
                 (TARGET / "stage.stderr.log").open("wb") as stderr:
                completed = subprocess.run(command, cwd=TARGET, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, check=False)
            passed = completed.returncode == 0 and electronic_converged(TARGET)
            manifest.update(
                state="ready-for-reviewed-dipole-stage" if passed else "failed",
                finished_at=now(), return_code=completed.returncode,
                electronic_converged=passed,
            )
            write_json(manifest_path, manifest)
            return 0 if passed else 1
        except Exception as exc:
            manifest.update(state="failed", finished_at=now(), error=str(exc))
            write_json(manifest_path, manifest)
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    args = parser.parse_args()
    if args.action == "prepare":
        print(json.dumps(prepare(), ensure_ascii=False), flush=True)
    else:
        raise SystemExit(run())
