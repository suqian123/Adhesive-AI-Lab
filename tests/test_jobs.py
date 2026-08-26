import sys
import time

import pytest

from adhesive_ai.jobs import get_job_status, list_jobs, read_job_output, read_job_result_text, split_job_command, submit_job, update_job_metadata


def _wait_for_job(job_id, root, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = get_job_status(job_id, root=root)
        if record.status in {"completed", "failed"}:
            return record
        time.sleep(0.05)
    pytest.fail(f"job {job_id} did not finish within {timeout} seconds")


def test_job_runner_records_stdout_and_real_exit_code(tmp_path):
    root = tmp_path / "jobs"
    workdir = tmp_path / "calculation"
    submitted = submit_job(
        "LAMMPS",
        [sys.executable, "-c", "print('calculation complete')"],
        workdir=workdir,
        root=root,
        metadata={"result_file": "engine.out"},
    )

    completed = _wait_for_job(submitted.job_id, root)
    output = read_job_output(submitted.job_id, root=root)

    assert completed.status == "completed"
    assert completed.return_code == 0
    assert "calculation complete" in output["stdout"]
    assert list_jobs(root=root)[0].job_id == submitted.job_id
    (workdir / "engine.out").write_text("result from engine file", encoding="utf-8")
    assert read_job_result_text(submitted.job_id, root=root) == "result from engine file"
    update_job_metadata(submitted.job_id, {"result_file": "../outside.out"}, root=root)
    with pytest.raises(ValueError, match="working directory"):
        read_job_result_text(submitted.job_id, root=root)


def test_job_runner_marks_nonzero_exit_as_failed(tmp_path):
    root = tmp_path / "jobs"
    submitted = submit_job(
        "CP2K",
        [sys.executable, "-c", "import sys; print('bad input', file=sys.stderr); raise SystemExit(7)"],
        workdir=tmp_path / "calculation",
        root=root,
    )

    failed = _wait_for_job(submitted.job_id, root)
    output = read_job_output(submitted.job_id, root=root, tail_chars=7)

    assert failed.status == "failed"
    assert failed.return_code == 7
    assert str(output["stderr"]).strip() == "input"
    assert "code 7" in failed.error


def test_split_job_command_preserves_quoted_arguments():
    assert split_job_command('solver --label "production run"') == ("solver", "--label", "production run")


def test_invalid_job_id_cannot_escape_job_root(tmp_path):
    with pytest.raises(ValueError, match="Invalid job ID"):
        get_job_status("../outside", root=tmp_path)
