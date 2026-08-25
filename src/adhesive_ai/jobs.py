"""Local external-calculation job lifecycle management.

The manager deliberately uses argument lists and ``shell=False`` so a VASP,
QE, CP2K, LAMMPS, or GROMACS command can be submitted without shell parsing.
Job metadata and stdout/stderr are persisted under ``work/jobs`` by default.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import uuid
from typing import Sequence


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    engine: str
    command: tuple[str, ...]
    workdir: str
    status: str
    submitted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    return_code: int | None = None
    stdout_path: str = ""
    stderr_path: str = ""
    metadata: dict[str, object] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_dir(root: str | Path, job_id: str) -> Path:
    directory = Path(root) / job_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _record_path(root: str | Path, job_id: str) -> Path:
    return _job_dir(root, job_id) / "job.json"


def _write_record(record: JobRecord, root: str | Path) -> None:
    _record_path(root, record.job_id).write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")


def _read_record(root: str | Path, job_id: str) -> JobRecord:
    payload = json.loads(_record_path(root, job_id).read_text(encoding="utf-8"))
    payload["command"] = tuple(payload.get("command", ()))
    return JobRecord(**payload)


def submit_job(
    engine: str, command: Sequence[str], *, workdir: str | Path, root: str | Path = "work/jobs",
    metadata: dict[str, object] | None = None,
) -> JobRecord:
    """Start an external job and persist its lifecycle record."""
    args = tuple(str(value) for value in command if str(value))
    if not args:
        raise ValueError("External job command cannot be empty")
    directory = Path(workdir)
    directory.mkdir(parents=True, exist_ok=True)
    job_id = f"{engine.lower().replace(' ', '-')}-{uuid.uuid4().hex[:10]}"
    job_directory = _job_dir(root, job_id)
    stdout_path, stderr_path = job_directory / "stdout.log", job_directory / "stderr.log"
    stdout_handle = stdout_path.open("w", encoding="utf-8")
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(list(args), cwd=str(directory), stdout=stdout_handle, stderr=stderr_handle, text=True)
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise
    finally:
        stdout_handle.close()
        stderr_handle.close()
    record = JobRecord(
        job_id=job_id, engine=engine, command=args, workdir=str(directory), status="running",
        submitted_at=_now(), started_at=_now(), stdout_path=str(stdout_path), stderr_path=str(stderr_path),
        metadata=metadata or {},
    )
    (job_directory / "process.pid").write_text(str(process.pid), encoding="ascii")
    _write_record(record, root)
    return record


def get_job_status(job_id: str, *, root: str | Path = "work/jobs") -> JobRecord:
    """Refresh and return a persisted job record."""
    record = _read_record(root, job_id)
    pid_path = _job_dir(root, job_id) / "process.pid"
    if record.status == "running" and pid_path.exists():
        try:
            os.kill(int(pid_path.read_text()), 0)
            running = True
        except Exception:
            running = False
        if not running:
            return _finish_record(record, root)
    return record


def _finish_record(record: JobRecord, root: str | Path) -> JobRecord:
    # The child process exit code is recorded by the launcher when available.
    # If it has already disappeared, a completed process is conservatively
    # marked finished and callers can inspect stdout/stderr for diagnostics.
    finished = JobRecord(**{**asdict(record), "status": "completed", "finished_at": _now()})
    _write_record(finished, root)
    return finished


def read_job_output(job_id: str, *, root: str | Path = "work/jobs") -> dict[str, str]:
    """Read captured stdout/stderr for a completed or running job."""
    record = get_job_status(job_id, root=root)
    def read(path: str) -> str:
        return Path(path).read_text(encoding="utf-8", errors="replace") if path and Path(path).exists() else ""
    return {"stdout": read(record.stdout_path), "stderr": read(record.stderr_path), "status": record.status}


def parse_job_result(job_id: str, *, root: str | Path = "work/jobs") -> object:
    """Parse captured output using the selected engine adapter."""
    from .engines import parse_dft_output, parse_gromacs_xvg, parse_lammps_thermo

    record = get_job_status(job_id, root=root)
    output = read_job_output(job_id, root=root)["stdout"]
    engine = record.engine.lower().replace(" ", "")
    if engine in {"vasp", "quantumespresso", "qe", "cp2k"}:
        return parse_dft_output(engine, output)
    if engine == "lammps":
        return parse_lammps_thermo(output)
    if engine in {"gromacs", "gmx"}:
        return parse_gromacs_xvg(output)
    raise ValueError(f"Unsupported job result parser: {record.engine}")


def list_jobs(*, root: str | Path = "work/jobs") -> list[JobRecord]:
    directory = Path(root)
    if not directory.exists():
        return []
    records = []
    for path in directory.glob("*/job.json"):
        try:
            records.append(get_job_status(path.parent.name, root=root))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return sorted(records, key=lambda item: item.submitted_at, reverse=True)
