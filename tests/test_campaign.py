import json
from decimal import Decimal

from adhesive_ai import build_candidate_library
from adhesive_ai.campaign import (
    build_multiscale_campaign,
    campaign_task_frame,
    requirement_coverage,
    validate_candidate_contract,
    write_multiscale_campaign,
)


def test_candidate_contract_and_multiscale_task_matrix_are_complete():
    candidates = build_candidate_library(max_records=12, seed=5)
    report = validate_candidate_contract(candidates)
    campaign = build_multiscale_campaign(candidates.iloc[0])
    tasks = campaign_task_frame(campaign)

    assert report == {
        "valid": True,
        "rows": 12,
        "missing_columns": [],
        "null_counts": {},
        "feature_count": 12,
        "output_count": 5,
    }
    assert len(campaign.tasks) == 26
    assert set(tasks.scale) == {"quantum", "atomistic-md", "coarse-grained"}
    assert any(task.conditions.get("temperatures_c", ())[0] == -180 for task in campaign.tasks if task.scale == "atomistic-md")
    assert candidates.iloc[0].data_source == "physics-informed-proxy"


def test_campaign_package_contains_manifests_and_runnable_cg_starting_model(tmp_path):
    candidate = build_candidate_library(max_records=1, seed=8).iloc[0]
    campaign = build_multiscale_campaign(candidate)

    paths = write_multiscale_campaign(campaign, tmp_path / "campaigns")
    manifest = json.loads(paths["campaign"].read_text(encoding="utf-8"))

    assert paths["cg_data"].exists() and paths["cg_input"].exists()
    assert manifest["candidate_id"] == candidate.candidate_id
    assert len(manifest["tasks"]) == 26
    assert "requires-calibration" in next(task["readiness"] for task in manifest["tasks"] if task["scale"] == "coarse-grained")


def test_requirement_coverage_separates_implementation_environment_and_science():
    profiles = {
        "dft": {"engine": "VASP", "command": "vasp_std"},
        "bulk_md": {"engine": "LAMMPS", "command": "lmp"},
        "interface_md": {"engine": "LAMMPS", "command": "lmp"},
        "coarse_grained": {"engine": "LAMMPS", "command": "lmp"},
    }

    coverage = {row["模块"]: row for row in requirement_coverage(profiles)}

    assert coverage["真实 DFT 计算"] == {
        "模块": "真实 DFT 计算",
        "实现状态": "已实现",
        "运行环境": "已配置（VASP）",
        "科学就绪": "待验证输入",
        "说明": "任务调度、求解器调用、输出解析和回写已实现；生产计算仍需验证结构、赝势及 DFT(+U) 参数",
    }
    assert coverage["树脂 MD 与宽温域评价"]["运行环境"] == "已配置（LAMMPS）"
    assert coverage["界面与粗粒化动力学"]["科学就绪"] == "待多尺度标定"
    assert coverage["多尺度任务自动编排"]["运行环境"] == "已配置（LAMMPS/VASP）"
    assert coverage["实验闭环"]["实现状态"] == "已实现"


def test_requirement_coverage_reports_partial_external_environment():
    coverage = {
        row["模块"]: row
        for row in requirement_coverage(
            {
                "dft": {"engine": "VASP", "command": "vasp_std"},
                "bulk_md": {"engine": "LAMMPS", "command": ""},
            }
        )
    }

    assert coverage["树脂 MD 与宽温域评价"]["运行环境"] == "未配置"
    assert coverage["多尺度任务自动编排"]["运行环境"] == "部分配置（1/4）"


def test_database_decimal_candidate_can_be_written_as_campaign_json(tmp_path):
    candidate = build_candidate_library(max_records=1, seed=12).iloc[0].copy()
    candidate["blend_fraction"] = Decimal("0.125")
    candidate["filler_pct"] = Decimal("8.500")
    candidate["crosslink_density"] = Decimal("0.8125")

    campaign = build_multiscale_campaign(candidate)
    paths = write_multiscale_campaign(campaign, tmp_path / "campaigns")
    manifest = json.loads(paths["campaign"].read_text(encoding="utf-8"))

    assert manifest["candidate_snapshot"]["blend_fraction"] == 0.125
    assert manifest["candidate_snapshot"]["filler_pct"] == 8.5
    assert manifest["candidate_snapshot"]["crosslink_density"] == 0.8125
