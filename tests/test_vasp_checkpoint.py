import json

import pytest

from adhesive_ai.vasp_checkpoint import electronic_converged, enable_charge_restart
from adhesive_ai.campaign_runner import vasp_convergence_progress, prepare_stalled_vasp_scf_recovery


CONVERGED = ("Iteration 1(12)\naborting loop because EDIFF is reached\n"
             "free energy TOTEN = -123.0\nGeneral timing and accounting\n")


@pytest.mark.parametrize("outcar,expected", [
    (CONVERGED, True),
    (CONVERGED.replace("aborting loop because EDIFF is reached", "NELM exhausted"), False),
    (CONVERGED.replace("General timing and accounting", ""), False),
    (CONVERGED + "Iteration 2(160)\nfree energy TOTEN = -122.0\nGeneral timing and accounting", False),
    (CONVERGED.replace("-123.0", "1E999"), False),
    ("", False),
])
def test_electronic_convergence_requires_final_loop_and_normal_finish(tmp_path, outcar, expected):
    (tmp_path / "OUTCAR").write_text(outcar)
    assert electronic_converged(tmp_path) is expected


def test_progress_rejects_legacy_complete_marker_without_ediff(tmp_path):
    jobs = []
    for index, text in enumerate([CONVERGED, CONVERGED.replace("aborting loop because EDIFF is reached", "")]):
        job = tmp_path / str(index)
        job.mkdir()
        jobs.append({"path": str(job)})
        (job / "run_status.json").write_text(json.dumps({"complete": True}))
        (job / "OUTCAR").write_text(text)
    (tmp_path / "validation_plan.json").write_text(json.dumps({"jobs": jobs}))
    progress = vasp_convergence_progress(tmp_path)
    assert progress["completed"] == 1
    assert progress["failed"] == 1


def test_finished_failed_job_is_not_reported_as_uncontrolled_runner(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (tmp_path / "validation_plan.json").write_text(json.dumps({"jobs": [{"path": str(job)}]}))
    (job / "run_status.json").write_text(json.dumps({"status": "failed", "complete": False}))
    progress = vasp_convergence_progress(tmp_path)
    assert progress["failed"] == 1
    assert not progress["active"]
    assert not progress["uncontrolled"]
    assert not progress["stalled"]


def test_recovery_rejects_stale_seed_charge(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    (tmp_path / "validation_plan.json").write_text(json.dumps({"jobs": [{"path": str(job)}]}))
    (job / "run_status.json").write_text(json.dumps({"status": "running"}))
    (job / "CHGCAR").write_text("old starting charge")
    (job / "INCAR").write_text("ALGO = Normal\n")
    with pytest.raises(RuntimeError, match="初始 CHGCAR"):
        prepare_stalled_vasp_scf_recovery(tmp_path)
    assert not (job / "scf_recovery.json").exists()


@pytest.mark.parametrize("wave", [b"", b"valid wave checkpoint"])
def test_charge_seed_restart_handles_changed_kpoint_mesh(tmp_path, wave):
    (tmp_path / "INCAR").write_text("ISTART = 1\nICHARG = 2\nNELMDL = -1\nEDIFF = 1E-6\n")
    (tmp_path / "WAVECAR").write_bytes(wave)
    enable_charge_restart(tmp_path)
    incar = (tmp_path / "INCAR").read_text()
    assert f"ISTART = {1 if wave else 0}" in incar
    assert "EDIFF = 1E-6" in incar
    assert "NELMDL = -5" in incar
    assert "ICHARG = 1" in incar
