import json
import hashlib
import io
import os
from pathlib import Path
import sys
import time
import zipfile

from adhesive_ai.campaign import CalculationTask, MultiscaleCampaign
from adhesive_ai.campaign_runner import (
    advance_campaign_run,
    _prepare_direct_vasp_inputs,
    _prepare_direct_lammps_inputs,
    _input_artifact_manifest,
    _preflight,
    _vasp_production_approved,
    available_engine_profiles,
    campaign_environment_frame,
    ensure_vasp_convergence_running,
    engine_profiles_from_env,
    get_campaign_run,
    load_engine_profiles,
    mark_external_campaign_task_imported,
    match_external_result_archive,
    prepare_standalone_external_task,
    register_external_campaign_package,
    resume_approved_vasp_tasks,
    resume_prepared_md_tasks,
    save_engine_profiles,
    start_campaign_run,
    terminate_campaign_run,
    VASP_LOG_STALE_SECONDS,
    vasp_convergence_progress,
)


def _cg_campaign() -> MultiscaleCampaign:
    task = CalculationTask(
        task_id="cg-pda-ceo2-dispersion",
        candidate_id="CL-00001",
        scale="coarse-grained",
        calculation_kind="interface_md",
        objective="pda-ceo2-dispersion-and-reinforcement",
        engine_options=("LAMMPS",),
        conditions={"filler_pct": 3.0, "temperature_c": 25.0},
        expected_outputs=("dispersion_index",),
        readiness="generator-available-requires-calibration",
    )
    return MultiscaleCampaign(
        campaign_id="CL-00001-test-campaign",
        candidate_id="CL-00001",
        created_at="2026-01-01T00:00:00+00:00",
        composition={"filler_pct": 3.0},
        process_conditions={},
        feature_contract={},
        output_contract=(),
        candidate_snapshot={
            "candidate_id": "CL-00001", "filler_pct": 3.0,
            "resin_polarity": 0.5, "low_temp_toughness_index": 0.6,
        },
        tasks=(task,),
    )


def _dft_campaign() -> MultiscaleCampaign:
    dft_task = CalculationTask(
        task_id="dft-o-111-v00-h00",
        candidate_id="CL-00001",
        scale="quantum",
        calculation_kind="dft",
        objective="atomic-oxygen-surface-chemistry",
        engine_options=("VASP",),
        conditions={"facet": "(111)", "oxygen_vacancy_fraction": 0.0, "hydroxyl_fraction": 0.0},
        expected_outputs=("adsorption_energy_ev",),
        readiness="pending-convergence-validation",
    )
    cg_task = CalculationTask(
        task_id="cg-pda-ceo2-dispersion",
        candidate_id="CL-00001",
        scale="coarse-grained",
        calculation_kind="interface_md",
        objective="pda-ceo2-dispersion-and-reinforcement",
        engine_options=("LAMMPS",),
        conditions={"filler_pct": 0.0, "temperature_c": 25.0},
        expected_outputs=("dispersion_index",),
        readiness="generator-available-requires-calibration",
    )
    return MultiscaleCampaign(
        campaign_id="CL-00001-test-dft-campaign",
        candidate_id="CL-00001",
        created_at="2026-01-01T00:00:00+00:00",
        composition={},
        process_conditions={},
        feature_contract={},
        output_contract=(),
        candidate_snapshot={
            "candidate_id": "CL-00001", "filler_pct": 0.0,
            "resin_polarity": 0.5, "low_temp_toughness_index": 0.6,
        },
        tasks=(dft_task, cg_task),
    )


def test_one_click_campaign_runs_configured_task_and_collects_result(tmp_path):
    wrapper = tmp_path / "fake_lammps.py"
    wrapper.write_text(
        "from pathlib import Path\n"
        "Path('log.lammps').write_text('Step Temp Pe Vol\\n0 298 -10 1000\\n100 299 -12 1001\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    profiles = {
        "coarse_grained": {
            "engine": "LAMMPS",
            "command": f'"{sys.executable}" "{wrapper}" {{task_file}}',
            "result_file": "log.lammps",
        },
    }
    root, job_root = tmp_path / "runs", tmp_path / "jobs"

    run = start_campaign_run(
        _cg_campaign(), profiles=profiles, root=root, job_root=job_root,
        max_parallel=1, launch_supervisor=True,
    )
    deadline = time.monotonic() + 10
    while run["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.05)
        run = get_campaign_run(run["run_id"], root=root)

    assert run["status"] == "completed"
    assert run["tasks"][0]["status"] == "completed"
    assert run["result_payload"]["interface"]["binding_energy_mj_m2"] > 0
    assert run["result_payload"]["interface"]["task_id"] == "cg-pda-ceo2-dispersion"


def test_one_click_campaign_marks_missing_command_as_blocked(tmp_path):
    run = start_campaign_run(
        _cg_campaign(), profiles={"coarse_grained": {"engine": "LAMMPS", "command": "", "result_file": "log.lammps"}},
        root=tmp_path / "runs", job_root=tmp_path / "jobs", launch_supervisor=False,
    )

    assert run["status"] == "blocked"
    assert "ADHESIVE_CG_COMMAND" in run["tasks"][0]["blocker"]


def test_external_campaign_package_is_registered_without_local_submission(tmp_path):
    root, job_root = tmp_path / "runs", tmp_path / "jobs"

    run = register_external_campaign_package(
        _cg_campaign(),
        profiles={
            "coarse_grained": {
                "engine": "LAMMPS",
                "command": "not-installed-on-this-machine -in in.cg",
                "result_file": "log.lammps",
            },
        },
        root=root,
        job_root=job_root,
    )

    assert run["execution_mode"] == "external"
    assert run["status"] == "external_pending"
    assert run["tasks"][0]["status"] == "external_pending"
    assert run["tasks"][0]["job_id"] is None
    assert (Path(run["package_directory"]) / "external_execution.json").is_file()
    assert Path(run["external_package_archive"]).is_file()
    assert not job_root.exists()

    updated = mark_external_campaign_task_imported(
        run["run_id"], "cg-pda-ceo2-dispersion", "external-job-001", root=root,
    )
    assert updated["tasks"][0]["status"] == "imported"
    assert updated["status"] == "completed"
    assert updated["integrated_at"]


def test_external_result_zip_matches_pending_task_by_task_directory(tmp_path):
    run = register_external_campaign_package(
        _cg_campaign(),
        profiles={"coarse_grained": {"engine": "LAMMPS", "result_file": "log.lammps"}},
        root=tmp_path / "runs",
    )
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("tasks/cg-pda-ceo2-dispersion/log.lammps", "Step Temp PotEng\n0 298 -10\n")
        archive.writestr("tasks/cg-pda-ceo2-dispersion/interface.data", "input topology")

    report = match_external_result_archive(run, archive_buffer.getvalue())

    assert report["pending_task_ids"] == []
    assert report["unmatched_files"] == []
    assert len(report["matches"]) == 1
    assert report["matches"][0]["task_id"] == "cg-pda-ceo2-dispersion"
    assert report["matches"][0]["result_content"].startswith(b"Step Temp")


def test_vasp_preflight_accepts_neb_image_zero_poscar(tmp_path):
    campaign = _cg_campaign()
    task = CalculationTask(
        task_id="dft-neb", candidate_id=campaign.candidate_id, scale="quantum",
        calculation_kind="dft", objective="atomic-oxygen-surface-chemistry",
        engine_options=("VASP",), conditions={}, expected_outputs=("reaction_barrier_ev",),
        readiness="pending-convergence-validation",
    )
    task_dir = tmp_path / "task"
    (task_dir / "00").mkdir(parents=True)
    for name in ("INCAR", "KPOINTS", "POTCAR"):
        (task_dir / name).write_text("input\n", encoding="utf-8")
    (task_dir / "00" / "POSCAR").write_text("neb endpoint\n", encoding="utf-8")

    _, blocker = _preflight(
        task, {"engine": "VASP", "command": sys.executable}, task_dir, campaign,
    )

    assert blocker is None


def test_direct_vasp_inputs_are_prepared_and_statically_validated(monkeypatch, tmp_path):
    calls = []

    def prepare(task, output_dir, *, resources):
        calls.append((task["task_id"], output_dir, resources))

    monkeypatch.setattr("adhesive_ai.vasp_production.prepare_campaign_dft_task", prepare)
    monkeypatch.setattr(
        "adhesive_ai.vasp_production.validate_vasp_input_set",
        lambda directory, *, resources: {"valid": True},
    )
    campaign = _dft_campaign()
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    status, blocker = _prepare_direct_vasp_inputs(
        campaign.tasks[0],
        {"engine": "VASP", "command": sys.executable},
        task_dir,
        resources=tmp_path / "resources.json",
    )

    assert status == "static-valid; pending-convergence-approval"
    assert blocker is None
    assert calls[0][0] == "dft-o-111-v00-h00"


def test_direct_lammps_md_generates_protocol_but_requires_validated_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "adhesive_ai.md_production.provision_bulk_md_inputs",
        lambda task, output_dir: {"provisioned": False, "missing_sources": ["system.data"]},
    )
    task = {
        "scale": "atomistic-md",
        "calculation_kind": "bulk_md",
        "conditions": {"temperatures_c": [-180.0, 25.0, 150.0]},
    }

    validation, blocker = _prepare_direct_lammps_inputs(
        task, {"engine": "LAMMPS", "command": "lmp -in in.production"}, tmp_path,
    )

    assert blocker is None
    assert validation == "template-valid; pending-topology-forcefield"
    production = (tmp_path / "in.production").read_text(encoding="utf-8")
    assert "read_data system.data" in production
    assert "include forcefield.production" in production
    assert "93.15 298.15 423.15" in production
    assert (tmp_path / "MD_INPUT_REQUIREMENTS.md").is_file()


def test_prepare_standalone_vasp_task_uses_campaign_input_generator(monkeypatch, tmp_path):
    captured = {}

    def prepare(task, profile, directory, *, resources):
        captured.update(
            task_id=task.task_id,
            candidate_id=task.candidate_id,
            conditions=task.conditions,
            directory=directory,
            resources=resources,
        )
        return "static-valid; pending-convergence-approval", None

    monkeypatch.setattr("adhesive_ai.campaign_runner._prepare_direct_vasp_inputs", prepare)

    result = prepare_standalone_external_task(
        candidate_id="CL-00001",
        calculation_kind="dft",
        engine="VASP",
        workdir=tmp_path / "manual-dft",
        conditions={"facet": "(111)", "oxygen_vacancy_fraction": 0.08},
        resources=tmp_path / "resources.json",
    )

    assert captured["candidate_id"] == "CL-00001"
    assert captured["conditions"]["facet"] == "(111)"
    assert result["input_status"] == "static-valid; pending-convergence-approval"
    assert result["blocker"] is None


def test_prepare_standalone_lammps_task_uses_md_input_generator(monkeypatch, tmp_path):
    captured = {}

    def prepare(task, profile, directory):
        captured.update(task=task, directory=directory)
        return "template-valid; pending-topology-forcefield", None

    monkeypatch.setattr("adhesive_ai.campaign_runner._prepare_direct_lammps_inputs", prepare)

    result = prepare_standalone_external_task(
        candidate_id="CL-00001",
        calculation_kind="bulk_md",
        engine="LAMMPS",
        workdir=tmp_path / "manual-md",
        conditions={"temperature_c": 25.0},
    )

    assert captured["task"]["calculation_kind"] == "bulk_md"
    assert result["input_status"] == "template-valid; pending-topology-forcefield"


def test_blocked_md_task_is_rechecked_after_validated_files_arrive(tmp_path):
    root = tmp_path / "runs"
    run_id = "md-resume"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    record = {
        "run_id": run_id,
        "campaign_id": "campaign",
        "candidate_id": "CL-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "blocked",
        "package_directory": str(tmp_path),
        "job_root": str(tmp_path / "jobs"),
        "max_parallel": 1,
        "candidate_snapshot": {},
        "tasks": [{
            "task_id": "md-1", "scale": "atomistic-md", "calculation_kind": "bulk_md",
            "category": "bulk_md", "conditions": {"temperatures_c": [25.0]},
            "dependencies": [], "engine": "LAMMPS", "command": [sys.executable],
            "result_file": "log.lammps", "workdir": str(task_dir), "status": "blocked",
            "blocker": "missing input", "job_id": None,
        }],
        "result_payload": {},
    }
    run_path = root / run_id / "run.json"
    run_path.parent.mkdir(parents=True)
    run_path.write_text(json.dumps(record), encoding="utf-8")
    (task_dir / "system.data").write_text("validated topology\n", encoding="utf-8")
    (task_dir / "forcefield.production").write_text("validated coefficients\n", encoding="utf-8")
    report = task_dir / "md_validation_report.json"
    report.write_text(json.dumps({
        "passed": True,
        "scientific_status": "md-production-approved",
        "resolution": "all-atom",
        "charge_model": "RESP",
        "baseline_id": "test-baseline-v1",
        "validated_files": {
            name: hashlib.sha256((task_dir / name).read_bytes()).hexdigest()
            for name in ("system.data", "forcefield.production")
        },
    }), encoding="utf-8")
    (task_dir / "md_approval.json").write_text(json.dumps({
        "approved": True,
        "evidence": [report.name],
    }), encoding="utf-8")

    resumed = resume_prepared_md_tasks(run_id, root=root, launch_supervisor=False)

    assert resumed["tasks"][0]["status"] in {"queued", "running"}
    assert resumed["tasks"][0]["input_validation"] == "static-valid; md-production-approved"
    assert resumed["tasks"][0]["blocker"] is None


def test_md_files_without_scientific_approval_remain_blocked(tmp_path):
    task = {
        "scale": "atomistic-md",
        "calculation_kind": "bulk_md",
        "conditions": {"temperatures_c": [25.0]},
    }
    (tmp_path / "system.data").write_text("unapproved topology\n", encoding="utf-8")
    (tmp_path / "forcefield.production").write_text("unapproved coefficients\n", encoding="utf-8")

    validation, blocker = _prepare_direct_lammps_inputs(
        task, {"engine": "LAMMPS", "command": "lmp -in in.production"}, tmp_path,
    )

    assert validation == "static-valid; pending-md-production-approval"
    assert "等待 MD 力场、交联拓扑与物性验证" in blocker


def test_advance_campaign_rechecks_md_approval_before_submission(tmp_path):
    root = tmp_path / "runs"
    run_id = "md-submit-gate"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "system.data").write_text("unapproved topology\n", encoding="utf-8")
    (task_dir / "forcefield.production").write_text("unapproved force field\n", encoding="utf-8")
    record = {
        "run_id": run_id,
        "campaign_id": "campaign",
        "candidate_id": "CL-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "queued",
        "package_directory": str(tmp_path),
        "job_root": str(tmp_path / "jobs"),
        "max_parallel": 1,
        "candidate_snapshot": {},
        "tasks": [{
            "task_id": "md-1", "scale": "atomistic-md", "calculation_kind": "bulk_md",
            "category": "bulk_md", "conditions": {}, "dependencies": [], "engine": "LAMMPS",
            "command": [sys.executable, "-c", "raise SystemExit('must not run')"],
            "result_file": "log.lammps", "workdir": str(task_dir), "status": "pending",
            "blocker": None, "job_id": None,
        }],
        "result_payload": {},
    }
    run_path = root / run_id / "run.json"
    run_path.parent.mkdir(parents=True)
    run_path.write_text(json.dumps(record), encoding="utf-8")

    advanced = advance_campaign_run(run_id, root=root)

    assert advanced["status"] == "blocked"
    assert advanced["tasks"][0]["status"] == "blocked"
    assert advanced["tasks"][0]["job_id"] is None
    assert advanced["tasks"][0]["input_validation"] == "static-valid; pending-md-production-approval"


def test_terminate_campaign_run_cancels_pending_and_blocked_tasks(tmp_path):
    root = tmp_path / "runs"
    run_id = "terminate-campaign"
    record = {
        "run_id": run_id,
        "campaign_id": "campaign",
        "candidate_id": "CL-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": "blocked",
        "package_directory": str(tmp_path),
        "job_root": str(tmp_path / "jobs"),
        "max_parallel": 2,
        "candidate_snapshot": {},
        "tasks": [
            {"task_id": "dft-1", "status": "blocked", "job_id": None, "blocker": "waiting"},
            {"task_id": "md-1", "status": "pending", "job_id": None, "blocker": None},
            {"task_id": "done-1", "status": "completed", "job_id": None, "blocker": None},
        ],
        "result_payload": {},
    }
    run_path = root / run_id / "run.json"
    run_path.parent.mkdir(parents=True)
    run_path.write_text(json.dumps(record), encoding="utf-8")

    terminated = terminate_campaign_run(run_id, root=root)

    assert terminated["status"] == "cancelled"
    assert terminated["tasks"][0]["status"] == "cancelled"
    assert terminated["tasks"][1]["status"] == "cancelled"
    assert terminated["tasks"][2]["status"] == "completed"
    assert terminated["terminated_at"]


def test_campaign_task_records_hashes_for_generated_lammps_inputs(tmp_path):
    for name, text in {
        "system.data": "topology\n",
        "forcefield.production": "coefficients\n",
        "in.production": "run 0\n",
    }.items():
        (tmp_path / name).write_text(text, encoding="utf-8")

    manifest = _input_artifact_manifest(tmp_path, "bulk_md", "LAMMPS")

    assert manifest["complete"] is True
    assert manifest["production_approved"] is False
    assert manifest["artifacts"]["system.data"]["sha256"]
    assert manifest["artifacts"]["in.production"]["bytes"] > 0


def test_direct_vasp_run_waits_for_convergence_approval(monkeypatch, tmp_path):
    def prepare_inputs(task, profile, task_dir, *, resources):
        for name in ("POSCAR", "INCAR", "KPOINTS", "POTCAR"):
            (task_dir / name).write_text("input\n", encoding="utf-8")
        return "static-valid; pending-convergence-approval", None

    monkeypatch.setattr("adhesive_ai.campaign_runner._prepare_direct_vasp_inputs", prepare_inputs)
    run = start_campaign_run(
        _dft_campaign(),
        profiles={"dft": {"engine": "VASP", "command": sys.executable, "result_file": "OUTCAR"}},
        root=tmp_path / "runs",
        job_root=tmp_path / "jobs",
        launch_supervisor=False,
        vasp_approval=tmp_path / "missing-approved.json",
    )

    assert run["status"] == "blocked"
    assert run["tasks"][0]["input_validation"] == "static-valid; pending-convergence-approval"
    assert "等待 VASP 收敛验证" in run["tasks"][0]["blocker"]


def test_approved_vasp_tasks_are_revalidated_and_resumed(monkeypatch, tmp_path):
    def prepare_inputs(task, profile, task_dir, *, resources):
        for name in ("POSCAR", "INCAR", "KPOINTS", "POTCAR"):
            (task_dir / name).write_text("input\n", encoding="utf-8")
        return "static-valid; pending-convergence-approval", None

    monkeypatch.setattr("adhesive_ai.campaign_runner._prepare_direct_vasp_inputs", prepare_inputs)
    root = tmp_path / "runs"
    approval = tmp_path / "approved.json"
    run = start_campaign_run(
        _dft_campaign(),
        profiles={"dft": {"engine": "VASP", "command": sys.executable, "result_file": "OUTCAR"}},
        root=root,
        job_root=tmp_path / "jobs",
        launch_supervisor=False,
        vasp_approval=approval,
    )
    report = tmp_path / "convergence_report.json"
    report.write_text(json.dumps({"facet": "(111)", "passed": True, "scientific_status": "convergence-approved"}), encoding="utf-8")
    approval.write_text(json.dumps({"facet": "(111)", "approved": True, "evidence": [str(report)]}), encoding="utf-8")
    monkeypatch.setattr(
        "adhesive_ai.vasp_production.validate_vasp_input_set",
        lambda directory, *, resources: {"valid": True},
    )

    resumed = resume_approved_vasp_tasks(
        run["run_id"], root=root, vasp_approval=approval, launch_supervisor=False,
    )

    dft_task = resumed["tasks"][0]
    assert dft_task["status"] in {"queued", "running"}
    assert dft_task["job_id"]
    assert dft_task["input_validation"] == "static-valid; convergence-approved"


def test_facet_approval_cannot_unlock_a_different_facet(tmp_path):
    report = tmp_path / "convergence_report.json"
    report.write_text(
        json.dumps({"facet": "(111)", "passed": True, "scientific_status": "convergence-approved"}),
        encoding="utf-8",
    )
    approval = tmp_path / "approved.json"
    approval.write_text(
        json.dumps({"facet": "(111)", "approved": True, "evidence": [str(report)]}),
        encoding="utf-8",
    )

    assert _vasp_production_approved(approval, expected_facet="(111)") is True
    assert _vasp_production_approved(approval, expected_facet="(110)") is False


def test_vasp_convergence_progress_reads_nested_running_stage(tmp_path):
    validation_root = tmp_path / "validation"
    job_path = validation_root / "encut" / "450"
    stage_path = job_path / ".model-preconverge" / "step2-pbe"
    stage_path.mkdir(parents=True)
    (validation_root / "validation_plan.json").write_text(
        json.dumps({"job_count": 1, "jobs": [{"path": str(job_path)}]}),
        encoding="utf-8",
    )
    (job_path / "run_status.json").write_text(
        json.dumps({"status": "running", "complete": False}), encoding="utf-8",
    )
    (stage_path / "run_status.json").write_text(
        json.dumps({"status": "running", "attempt": 2, "complete": False}), encoding="utf-8",
    )
    (stage_path / "stage.stdout.log").write_text(
        "DAV:   1  -1.0\nRMM:  17  -2.0\n", encoding="utf-8",
    )
    (validation_root / "runner.pid").write_text("123\n", encoding="utf-8")
    (validation_root / "runner.pgid").write_text("123\n", encoding="utf-8")
    (validation_root / "runner_control.json").write_text(
        json.dumps({"state": "running", "pid": 123, "pgid": 123}), encoding="utf-8",
    )

    progress = vasp_convergence_progress(
        validation_root, approval=tmp_path / "missing-approved.json",
    )

    assert progress["available"] is True
    assert progress["active"] is True
    assert progress["uncontrolled"] is False
    assert progress["job"] == "encut/450"
    assert progress["phase"] == "PBE 桥接预收敛"
    assert progress["attempt"] == 2
    assert progress["electronic_step"] == 17
    assert progress["completed"] == 0
    assert progress["total"] == 1


def test_vasp_convergence_progress_marks_stale_or_paused_stage_inactive(tmp_path):
    validation_root = tmp_path / "validation"
    job_path = validation_root / "encut" / "450"
    stage_path = job_path / ".model-preconverge" / "step2-pbe"
    stage_path.mkdir(parents=True)
    (validation_root / "validation_plan.json").write_text(
        json.dumps({"job_count": 1, "jobs": [{"path": str(job_path)}]}),
        encoding="utf-8",
    )
    (job_path / "run_status.json").write_text(
        json.dumps({"status": "running", "complete": False}), encoding="utf-8",
    )
    (stage_path / "run_status.json").write_text(
        json.dumps({"status": "running", "attempt": 2, "complete": False}), encoding="utf-8",
    )
    log_path = stage_path / "stage.stdout.log"
    log_path.write_text("RMM:  17  -2.0\n", encoding="utf-8")
    old_timestamp = time.time() - VASP_LOG_STALE_SECONDS - 1
    os.utime(log_path, (old_timestamp, old_timestamp))

    stale = vasp_convergence_progress(validation_root, approval=tmp_path / "missing-approved.json")

    assert stale["active"] is False
    assert stale["stalled"] is True
    assert stale["paused"] is False
    assert stale["electronic_step"] == 17

    log_path.touch()
    (validation_root / "runner.pid").write_text("123\n", encoding="utf-8")
    (validation_root / "runner.pgid").write_text("123\n", encoding="utf-8")
    (validation_root / "runner_control.json").write_text(
        json.dumps({"state": "paused", "pid": 123, "pgid": 123}), encoding="utf-8",
    )
    paused = vasp_convergence_progress(validation_root, approval=tmp_path / "missing-approved.json")

    assert paused["active"] is False
    assert paused["stalled"] is False
    assert paused["paused"] is True

    (validation_root / "runner_control.json").write_text(
        json.dumps({"state": "cancelled"}), encoding="utf-8",
    )
    cancelled = vasp_convergence_progress(validation_root, approval=tmp_path / "missing-approved.json")

    assert cancelled["active"] is False
    assert cancelled["paused"] is False
    assert cancelled["cancelled"] is True


def test_vasp_convergence_progress_marks_untracked_live_log_uncontrolled(tmp_path):
    validation_root = tmp_path / "validation"
    job_path = validation_root / "encut" / "450"
    stage_path = job_path / ".model-preconverge" / "step2-pbe"
    stage_path.mkdir(parents=True)
    (validation_root / "validation_plan.json").write_text(
        json.dumps({"job_count": 1, "jobs": [{"path": str(job_path)}]}), encoding="utf-8",
    )
    (job_path / "run_status.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    (stage_path / "run_status.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    (stage_path / "stage.stdout.log").write_text("RMM:  16  -2.0\n", encoding="utf-8")

    progress = vasp_convergence_progress(validation_root, approval=tmp_path / "missing-approved.json")

    assert progress["active"] is False
    assert progress["uncontrolled"] is True
    assert progress["runner_pid"] is None


def test_ensure_vasp_convergence_starts_only_when_inactive(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(
        "adhesive_ai.campaign_runner.vasp_convergence_progress",
        lambda root, *, approval: {"approved": False, "active": False, "paused": False},
    )
    monkeypatch.setattr(
        "adhesive_ai.campaign_runner.restart_vasp_convergence",
        lambda root, **kwargs: calls.append(root) or {"started": True},
    )

    started = ensure_vasp_convergence_running(tmp_path, approval=tmp_path / "approved.json")

    assert started["started"] is True
    assert calls == [tmp_path]

    monkeypatch.setattr(
        "adhesive_ai.campaign_runner.vasp_convergence_progress",
        lambda root, *, approval: {"approved": False, "active": True, "paused": False},
    )
    already_running = ensure_vasp_convergence_running(tmp_path, approval=tmp_path / "approved.json")

    assert already_running["started"] is False
    assert already_running["reason"] == "running"
    assert calls == [tmp_path]

    monkeypatch.setattr(
        "adhesive_ai.campaign_runner.vasp_convergence_progress",
        lambda root, *, approval: {"approved": False, "active": False, "paused": True},
    )
    monkeypatch.setattr(
        "adhesive_ai.campaign_runner.resume_vasp_convergence",
        lambda root: {"active": True, "paused": False},
    )
    resumed = ensure_vasp_convergence_running(tmp_path, approval=tmp_path / "approved.json")

    assert resumed["resumed"] is True
    assert resumed["reason"] == "resumed"
    assert calls == [tmp_path]


def test_page_selected_profiles_are_persisted_over_environment_defaults(tmp_path):
    path = tmp_path / "profiles.json"
    profiles = {
        "dft": {"engine": "CP2K", "command": "cp2k.psmp -i cp2k.inp", "result_file": "cp2k.out"},
        "bulk_md": {"engine": "LAMMPS", "command": "lmp -in in.production", "result_file": "log.lammps"},
        "interface_md": {"engine": "GROMACS", "command": "gmx mdrun -deffnm production", "result_file": "potential.xvg"},
        "coarse_grained": {"engine": "LAMMPS", "command": "lmp -in in.cg", "result_file": "log.lammps"},
    }

    saved_path = save_engine_profiles(profiles, path)
    loaded = load_engine_profiles(path, environ={"ADHESIVE_DFT_COMMAND": "vasp_std"})

    assert saved_path == path.resolve()
    assert loaded["dft"]["engine"] == "CP2K"
    assert loaded["dft"]["command"] == "cp2k.psmp -i cp2k.inp"
    assert loaded["interface_md"]["engine"] == "GROMACS"


def test_profile_check_reports_a_real_executable():
    profiles = {
        "coarse_grained": {
            "engine": "LAMMPS",
            "command": f'"{sys.executable}" fake_wrapper.py {{task_file}}',
            "result_file": "log.lammps",
        },
    }

    frame = campaign_environment_frame(profiles)

    assert frame.iloc[0]["程序检查"] == "已找到可执行程序"
    assert frame.iloc[0]["生产门槛"] == "依赖界面 MD 标定"


def test_lammps_environment_reports_inputs_and_scientific_gate():
    frame = campaign_environment_frame({
        "bulk_md": {"engine": "LAMMPS", "command": "lmp -in in.production", "result_file": "log.lammps"},
    })

    assert frame.iloc[0]["输入契约"] == "system.data、forcefield.production、in.production"
    assert frame.iloc[0]["生产门槛"] == "需 md_approval.json"


def test_engine_profiles_auto_detect_installed_solver(monkeypatch):
    monkeypatch.setattr(
        "adhesive_ai.campaign_runner.shutil.which",
        lambda name: "C:/tools/lmp.exe" if name == "lmp" else None,
    )

    profiles = engine_profiles_from_env({})

    assert profiles["bulk_md"]["engine"] == "LAMMPS"
    assert profiles["interface_md"]["command"].endswith("-in in.production")
    assert profiles["coarse_grained"]["command"].endswith("-in in.cg")
    assert profiles["dft"]["command"] == ""


def test_available_engine_profiles_filters_missing_launchers(monkeypatch):
    monkeypatch.setattr(
        "adhesive_ai.campaign_runner.shutil.which",
        lambda name: f"C:/tools/{name}.exe" if name in {"wsl.exe", "lmp"} else None,
    )
    profiles = {
        "dft": {"engine": "VASP", "command": "wsl.exe -d Ubuntu -- vasp_std", "result_file": "OUTCAR"},
        "bulk_md": {"engine": "LAMMPS", "command": "lmp -in in.production", "result_file": "log.lammps"},
        "interface_md": {"engine": "GROMACS", "command": "gmx mdrun", "result_file": "potential.xvg"},
        "coarse_grained": {"engine": "LAMMPS", "command": "", "result_file": "log.lammps"},
    }

    available = available_engine_profiles(profiles)

    assert set(available) == {"dft", "bulk_md"}
    assert available["dft"]["engine"] == "VASP"
    assert available["bulk_md"]["command"] == "lmp -in in.production"
