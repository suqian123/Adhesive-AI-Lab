import sys
import time

from adhesive_ai.campaign import CalculationTask, MultiscaleCampaign
from adhesive_ai.campaign_runner import (
    campaign_environment_frame,
    engine_profiles_from_env,
    get_campaign_run,
    load_engine_profiles,
    save_engine_profiles,
    start_campaign_run,
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
