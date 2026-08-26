import sys
import time

import pandas as pd

from adhesive_ai import build_candidate_library
from adhesive_ai.jobs import JobRecord, get_job_status, submit_job
import adhesive_ai.workflow as workflow


def _record(engine, kind):
    return JobRecord(
        job_id="job-1",
        engine=engine,
        command=("solver",),
        workdir=".",
        status="completed",
        submitted_at="2026-01-01T00:00:00+00:00",
        metadata={"calculation_kind": kind, "temperature_unit": "K", "energy_unit": "kcal/mol"},
    )


def _wait(job_id, root, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = get_job_status(job_id, root=root)
        if record.status in {"completed", "failed"}:
            return record
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_lammps_bulk_payload_reduces_thermo_to_candidate_observables():
    parsed = {
        "Step": [0, 1, 2, 3],
        "Temp": [250, 275, 300, 325],
        "Pe": [-100, -105, -110, -108],
        "Vol": [1000, 1005, 1015, 1030],
    }

    payload = workflow.calculation_payload(_record("LAMMPS", "bulk_md"), parsed)

    assert payload["md"]["observables_available"] is True
    assert "glass_transition_c" in payload["md"]
    assert payload["md"]["temperatures_c"][0] < 0


def test_interface_payload_persists_real_trajectory(monkeypatch):
    record = JobRecord(
        job_id="interface-1", engine="LAMMPS", command=("solver",), workdir=".",
        status="completed", submitted_at="2026-01-01T00:00:00+00:00",
        metadata={
            "calculation_kind": "interface_md", "area_nm2": 10.0, "temperature_c": 25.0,
            "energy_unit": "kcal/mol",
        },
    )
    monkeypatch.setattr(
        workflow,
        "read_job_result_text",
        lambda job_id, root: "Step Temp Pe Vol\n0 298 -10 1000\n100 299 -12 1001\n",
    )

    payload = workflow.calculation_payload(record, {}, root="unused")["interface"]

    assert payload["job_id"] == "interface-1"
    assert payload["steps"] == [0.0, 100.0]
    assert payload["energy"] == [-10.0, -12.0]
    assert payload["binding_energy_mj_m2"] > 0


def test_completed_dft_job_is_persisted_retrained_and_marked_integrated(monkeypatch, tmp_path):
    candidates = build_candidate_library(max_records=16, seed=2)
    candidate_id = str(candidates.iloc[0].candidate_id)
    root = tmp_path / "jobs"
    submitted = submit_job(
        "VASP",
        [sys.executable, "-c", "print('free energy TOTEN = -10.0')"],
        workdir=tmp_path / "calculation",
        root=root,
        metadata={
            "candidate_id": candidate_id,
            "calculation_kind": "dft",
            "surface_energy_ev": -5.0,
            "oxygen_energy_ev": -3.0,
        },
    )
    assert _wait(submitted.job_id, root).status == "completed"

    saved = {}
    monkeypatch.setattr(workflow, "load_latest_simulation_results", lambda candidate_ids: {})
    monkeypatch.setattr(workflow, "save_simulation", lambda row, qchem, md, interface, version: saved.update(row=row, qchem=qchem, version=version))
    monkeypatch.setattr(workflow, "save_model_version", lambda model, artifact_path: saved.update(artifact_path=artifact_path))

    result = workflow.integrate_completed_job(
        submitted.job_id,
        candidates,
        experiments=pd.DataFrame(),
        root=root,
        model_root=tmp_path / "models",
        top_n=3,
    )

    assert result is not None
    updated = result.candidates.loc[result.candidates.candidate_id == candidate_id].iloc[0]
    assert updated.filler_oxygen_adsorption_ev == -2.0
    assert saved["qchem"]["adsorption_energy_ev"] == -2.0
    assert saved["row"]["candidate_id"] == candidate_id
    assert get_job_status(submitted.job_id, root=root).metadata["integrated_at"]
    assert workflow.integrate_completed_job(submitted.job_id, candidates, root=root) is None
