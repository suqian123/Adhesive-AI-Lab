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
import shlex
import signal
import subprocess
import sys
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
    error: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_dir(root: str | Path, job_id: str, *, create: bool = False) -> Path:
    if not job_id or any(value in job_id for value in ("/", "\\", "..")):
        raise ValueError(f"Invalid job ID: {job_id!r}")
    directory = Path(root) / job_id
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def _record_path(root: str | Path, job_id: str) -> Path:
    return _job_dir(root, job_id) / "job.json"


def _write_record(record: JobRecord, root: str | Path) -> None:
    path = _record_path(root, record.job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _read_record(root: str | Path, job_id: str) -> JobRecord:
    payload = json.loads(_record_path(root, job_id).read_text(encoding="utf-8"))
    payload["command"] = tuple(payload.get("command", ()))
    return JobRecord(**payload)


def _updated(record: JobRecord, **changes: object) -> JobRecord:
    return JobRecord(**{**asdict(record), **changes})


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return bool(ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError):
        return False
    return True


def split_job_command(command_text: str) -> tuple[str, ...]:
    """Split a command line without invoking a shell, preserving Windows paths."""
    parts = shlex.split(command_text, posix=os.name != "nt")
    if os.name == "nt":
        parts = [part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"} else part for part in parts]
    return tuple(parts)


def submit_job(
    engine: str, command: Sequence[str], *, workdir: str | Path, root: str | Path = "work/jobs",
    metadata: dict[str, object] | None = None,
) -> JobRecord:
    """Start an external job and persist its lifecycle record."""
    args = tuple(str(value) for value in command)
    if not args or not args[0].strip():
        raise ValueError("External job command cannot be empty")
    directory = Path(workdir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    job_id = f"{engine.lower().replace(' ', '-')}-{uuid.uuid4().hex[:10]}"
    root_path = Path(root).expanduser().resolve()
    job_directory = _job_dir(root_path, job_id, create=True)
    stdout_path, stderr_path = job_directory / "stdout.log", job_directory / "stderr.log"
    stdout_path.touch()
    stderr_path.touch()
    record = JobRecord(
        job_id=job_id, engine=engine, command=args, workdir=str(directory), status="queued",
        submitted_at=_now(), stdout_path=str(stdout_path), stderr_path=str(stderr_path), metadata=metadata or {},
    )
    _write_record(record, root_path)

    launcher = [sys.executable, str(Path(__file__).resolve()), "--run-job", str(root_path), job_id]
    popen_options: dict[str, object] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        popen_options["start_new_session"] = True
    try:
        process = subprocess.Popen(launcher, **popen_options)
    except Exception as exc:
        failed = _updated(record, status="failed", finished_at=_now(), error=f"Unable to start job runner: {exc}")
        _write_record(failed, root_path)
        raise
    (job_directory / "process.pid").write_text(str(process.pid), encoding="ascii")
    return record


def _run_job(root: str | Path, job_id: str) -> int:
    """Worker entry point that preserves the real exit status across UI reruns."""
    record = _read_record(root, job_id)
    if record.status == "cancelled":
        return 0
    running = _updated(record, status="running", started_at=_now(), error=None)
    _write_record(running, root)
    try:
        with Path(record.stdout_path).open("w", encoding="utf-8") as stdout_handle, Path(record.stderr_path).open("w", encoding="utf-8") as stderr_handle:
            completed = subprocess.run(
                list(record.command), cwd=record.workdir, stdin=subprocess.DEVNULL,
                stdout=stdout_handle, stderr=stderr_handle, text=True, check=False,
            )
        return_code = completed.returncode
        latest = _read_record(root, job_id)
        if latest.status == "cancelled":
            return 0
        final = _updated(
            running, status="completed" if return_code == 0 else "failed",
            finished_at=_now(), return_code=return_code,
            error=None if return_code == 0 else f"Command exited with code {return_code}",
        )
    except Exception as exc:
        with Path(record.stderr_path).open("a", encoding="utf-8") as stderr_handle:
            stderr_handle.write(f"\nJob runner error: {exc}\n")
        final = _updated(running, status="failed", finished_at=_now(), return_code=None, error=str(exc))
    _write_record(final, root)
    return final.return_code or (0 if final.status == "completed" else 1)


def cancel_job(job_id: str, *, root: str | Path = "work/jobs") -> JobRecord:
    """Terminate a locally managed job runner and prevent queued work from starting."""
    record = _read_record(root, job_id)
    if record.status not in {"queued", "running"}:
        return record
    pid_path = _job_dir(root, job_id) / "process.pid"
    try:
        pid = int(pid_path.read_text(encoding="ascii")) if pid_path.is_file() else None
    except (OSError, ValueError):
        pid = None
    if pid is not None and _pid_is_running(pid):
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "taskkill failed").strip()
                raise RuntimeError(f"Unable to terminate job runner {job_id}: {detail}")
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    cancelled = _updated(
        record,
        status="cancelled",
        finished_at=_now(),
        return_code=None,
        error="Cancelled by user",
    )
    _write_record(cancelled, root)
    return cancelled


def get_job_status(job_id: str, *, root: str | Path = "work/jobs") -> JobRecord:
    """Refresh and return a persisted job record."""
    record = _read_record(root, job_id)
    pid_path = _job_dir(root, job_id) / "process.pid"
    if record.status in {"queued", "running"} and pid_path.exists():
        try:
            running = _pid_is_running(int(pid_path.read_text(encoding="ascii")))
        except (OSError, ValueError):
            running = False
        if not running:
            failed = _updated(record, status="failed", finished_at=_now(), error="Job runner exited unexpectedly")
            _write_record(failed, root)
            return failed
    return record


def _read_text(path: str, tail_chars: int | None = None) -> str:
    if not path or not Path(path).exists():
        return ""
    if tail_chars is None:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    with Path(path).open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - tail_chars * 4))
        return handle.read().decode("utf-8", errors="replace")[-tail_chars:]


def read_job_output(job_id: str, *, root: str | Path = "work/jobs", tail_chars: int | None = None) -> dict[str, object]:
    """Read captured stdout/stderr for a completed or running job."""
    record = get_job_status(job_id, root=root)
    return {
        "stdout": _read_text(record.stdout_path, tail_chars),
        "stderr": _read_text(record.stderr_path, tail_chars),
        "status": record.status,
        "return_code": record.return_code,
        "error": record.error,
    }


def read_job_result_text(job_id: str, *, root: str | Path = "work/jobs") -> str:
    """Read a configured engine output file, falling back to captured stdout."""
    record = get_job_status(job_id, root=root)
    result_file = str((record.metadata or {}).get("result_file") or "").strip()
    if result_file:
        workdir = Path(record.workdir).resolve()
        target = (workdir / result_file).resolve()
        try:
            target.relative_to(workdir)
        except ValueError as exc:
            raise ValueError("Job result file must stay inside the task working directory") from exc
        if target.is_file():
            return target.read_text(encoding="utf-8", errors="replace")
    return str(read_job_output(job_id, root=root)["stdout"])


def update_job_metadata(job_id: str, updates: dict[str, object], *, root: str | Path = "work/jobs") -> JobRecord:
    """Merge integration metadata into a persisted job record."""
    record = get_job_status(job_id, root=root)
    metadata = dict(record.metadata or {})
    metadata.update(updates)
    updated = _updated(record, metadata=metadata)
    _write_record(updated, root)
    return updated


def parse_job_result(job_id: str, *, root: str | Path = "work/jobs") -> object:
    """Parse captured output using the selected engine adapter."""
    from .engines import parse_dft_output, parse_gromacs_xvg, parse_lammps_thermo

    record = get_job_status(job_id, root=root)
    if record.status != "completed":
        raise RuntimeError(f"Job {job_id} is {record.status}; only completed jobs can be parsed")
    output = read_job_result_text(job_id, root=root)
    engine = record.engine.lower().replace(" ", "")
    if engine in {"vasp", "quantumespresso", "qe", "cp2k"}:
        metadata = record.metadata or {}
        surface_energy = metadata.get("surface_energy_ev")
        oxygen_energy = metadata.get("oxygen_energy_ev")
        return parse_dft_output(
            engine,
            str(output),
            surface_energy_ev=float(surface_energy) if surface_energy is not None else None,
            oxygen_energy_ev=float(oxygen_energy) if oxygen_energy is not None else None,
        )
    if engine == "lammps":
        return parse_lammps_thermo(str(output))
    if engine in {"gromacs", "gmx"}:
        return parse_gromacs_xvg(str(output))
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


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--run-job":
        raise SystemExit(_run_job(sys.argv[2], sys.argv[3]))
    raise SystemExit("Usage: jobs.py --run-job ROOT JOB_ID")
