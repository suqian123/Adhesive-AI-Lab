"""Reproducible multiscale campaign planning and scientific data contracts.

The campaign makes the gap between a proxy estimate and an executable,
calibrated atomistic calculation explicit. It generates a complete task matrix
and a runnable coarse-grained starting model; DFT and all-atom MD tasks remain
blocked until validated structures, topologies, and force fields are supplied.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .coarse_grained import build_cg_interface_model, write_cg_interface_model
from .result_integration import to_jsonable


FEATURE_CONTRACT = {
    "resin": (
        "functional_group_type", "crosslink_density", "glass_transition_c", "free_volume_fraction",
        "chain_mobility", "cohesive_energy_density_mj_m3", "elastic_modulus_gpa", "cte_ppm_k",
    ),
    "filler": ("filler_oxygen_adsorption_ev", "filler_radical_capture_index"),
    "interface": ("interface_binding_energy_mj_m2", "interface_covalent_bond_count"),
}
OUTPUT_CONTRACT = (
    "wide_temp_adhesion_mpa", "healing_efficiency_pct", "atomic_oxygen_retention_pct",
    "uv_retention_pct", "am_feasibility",
)


@dataclass(frozen=True)
class CalculationTask:
    task_id: str
    candidate_id: str
    scale: str
    calculation_kind: str
    objective: str
    engine_options: tuple[str, ...]
    conditions: dict[str, Any]
    expected_outputs: tuple[str, ...]
    readiness: str
    blocking_requirements: tuple[str, ...] = ()


@dataclass(frozen=True)
class MultiscaleCampaign:
    campaign_id: str
    candidate_id: str
    created_at: str
    composition: dict[str, Any]
    process_conditions: dict[str, Any]
    feature_contract: dict[str, tuple[str, ...]]
    output_contract: tuple[str, ...]
    candidate_snapshot: dict[str, Any]
    tasks: tuple[CalculationTask, ...]


def _candidate_record(candidate: Mapping[str, Any] | pd.Series) -> dict[str, Any]:
    record = candidate.to_dict() if isinstance(candidate, pd.Series) else dict(candidate)
    candidate_id = str(record.get("candidate_id") or "").strip()
    if not candidate_id:
        raise ValueError("A multiscale campaign requires candidate_id")
    return record


def validate_candidate_contract(candidates: pd.DataFrame) -> dict[str, Any]:
    """Report missing/null model features and outputs without silent filling."""
    required = tuple(name for names in FEATURE_CONTRACT.values() for name in names) + OUTPUT_CONTRACT
    missing = [name for name in required if name not in candidates.columns]
    null_counts: dict[str, int] = {}
    for name in required:
        if name not in candidates.columns:
            continue
        if name == "functional_group_type":
            invalid = candidates[name].isna() | candidates[name].astype(str).str.strip().eq("")
        else:
            invalid = pd.to_numeric(candidates[name], errors="coerce").isna()
        null_counts[name] = int(invalid.sum())
    return {
        "valid": not missing and not any(null_counts.values()),
        "rows": len(candidates),
        "missing_columns": missing,
        "null_counts": {name: count for name, count in null_counts.items() if count},
        "feature_count": sum(len(names) for names in FEATURE_CONTRACT.values()),
        "output_count": len(OUTPUT_CONTRACT),
    }


def build_multiscale_campaign(
    candidate: Mapping[str, Any] | pd.Series,
    *,
    facets: Sequence[str] = ("(111)", "(110)", "(100)"),
    vacancy_fractions: Sequence[float] = (0.0, 0.08, 0.15),
    hydroxyl_fractions: Sequence[float] = (0.0, 0.35),
    temperatures_c: Sequence[float] = (-180.0, -120.0, -60.0, 25.0, 80.0, 150.0),
) -> MultiscaleCampaign:
    """Build the complete DFT/MD/interface/CG task matrix for one candidate."""
    row = _candidate_record(candidate)
    candidate_id = str(row["candidate_id"])
    tasks: list[CalculationTask] = []
    for facet in facets:
        facet_id = facet.strip("()").replace("-", "m")
        for vacancy in vacancy_fractions:
            for hydroxyl in hydroxyl_fractions:
                task_id = f"dft-o-{facet_id}-v{int(round(100 * vacancy)):02d}-h{int(round(100 * hydroxyl)):02d}"
                tasks.append(CalculationTask(
                    task_id, candidate_id, "quantum", "dft", "atomic-oxygen-surface-chemistry",
                    ("VASP", "Quantum ESPRESSO", "CP2K"),
                    {"facet": facet, "oxygen_vacancy_fraction": float(vacancy), "hydroxyl_fraction": float(hydroxyl)},
                    ("adsorption_energy_ev", "reaction_energy_ev", "reaction_barrier_ev", "ce3_fraction", "reactive_oxygen_capture_index"),
                    "planned-requires-atomistic-structure",
                    ("validated CeO2 slab", "spin-polarized DFT(+U) settings", "adsorbate reference energy", "NEB endpoints"),
                ))
        tasks.append(CalculationTask(
            f"dft-pda-binding-{facet_id}", candidate_id, "quantum", "dft", "pda-resin-and-surface-binding",
            ("VASP", "Quantum ESPRESSO", "CP2K"),
            {"facet": facet, "functional_group_type": row.get("functional_group_type")},
            ("pda_resin_hbond_ev", "pda_surface_coordination_ev", "pda_reaction_ev"),
            "planned-requires-atomistic-structure",
            ("validated PDA/resin fragments", "ceria slab", "binding-energy reference calculations"),
        ))

    base_crosslink = float(row.get("crosslink_density", 0.65))
    crosslink_levels = sorted({float(np.clip(base_crosslink + delta, 0.15, 1.0)) for delta in (-0.15, 0.0, 0.15)})
    for index, crosslink in enumerate(crosslink_levels, start=1):
        tasks.append(CalculationTask(
            f"md-resin-x{index}", candidate_id, "atomistic-md", "bulk_md", "crosslinked-resin-temperature-sweep",
            ("LAMMPS", "GROMACS"),
            {"crosslink_density": crosslink, "temperatures_c": tuple(float(value) for value in temperatures_c)},
            ("glass_transition_c", "free_volume_fraction", "cohesive_energy_density_mj_m3", "elastic_modulus_gpa", "cte_ppm_k", "chain_mobility"),
            "planned-requires-crosslinked-topology",
            ("crosslinked atomistic topology", "validated resin/dynamic-bond force field", "equilibrated temperature replicas"),
        ))
    tasks.append(CalculationTask(
        "md-resin-pda-ceo2-interface", candidate_id, "atomistic-md", "interface_md", "resin-pda-ceo2-interface-binding",
        ("LAMMPS", "GROMACS"),
        {"temperatures_c": tuple(float(value) for value in temperatures_c), "filler_pct": float(row.get("filler_pct", 0.0))},
        ("interface_binding_energy_mj_m2", "interface_covalent_bond_count", "adhesion_retention", "self_healing_efficiency"),
        "planned-requires-interface-topology",
        ("resin/PDA@CeO2 atomistic interface", "validated cross interactions", "interface area"),
    ))
    tasks.append(CalculationTask(
        "cg-pda-ceo2-dispersion", candidate_id, "coarse-grained", "interface_md", "pda-ceo2-dispersion-and-reinforcement",
        ("LAMMPS",),
        {"filler_pct": float(row.get("filler_pct", 0.0)), "temperature_c": 25.0},
        ("dispersion_index", "coarse_grained_reinforcement_index", "interface_binding_energy_mj_m2"),
        "generator-available-requires-calibration",
        ("calibration against atomistic PMF/DFT/experiment",),
    ))
    composition_keys = (
        "resin", "resin_variant", "blend_resin", "blend_fraction", "dynamic_unit", "cure_system",
        "curing_agent", "catalyst", "toughener_type", "toughener_pct", "filler_type", "filler_pct",
    )
    process_keys = (
        "curing_temperature_c", "curing_time_h", "post_cure_temperature_c", "post_cure_time_h",
        "mixing_temperature_c", "vacuum_degassing_min",
    )
    timestamp = datetime.now(timezone.utc)
    return MultiscaleCampaign(
        f"{candidate_id}-{timestamp.strftime('%Y%m%d%H%M%S')}",
        candidate_id,
        timestamp.isoformat(),
        {key: to_jsonable(row.get(key)) for key in composition_keys},
        {key: to_jsonable(row.get(key)) for key in process_keys},
        FEATURE_CONTRACT,
        OUTPUT_CONTRACT,
        {str(key): to_jsonable(value) for key, value in row.items()},
        tuple(tasks),
    )


def campaign_task_frame(campaign: MultiscaleCampaign, *, run_id: str | None = None) -> pd.DataFrame:
    """Return a UI-friendly task table."""
    return pd.DataFrame([{
        "candidate_id": campaign.candidate_id,
        "run_id": run_id or "待启动",
        "task_id": task.task_id,
        "scale": task.scale,
        "objective": task.objective,
        "engines": "/".join(task.engine_options),
        "readiness": task.readiness,
        "expected_outputs": ", ".join(task.expected_outputs),
    } for task in campaign.tasks])


def write_multiscale_campaign(campaign: MultiscaleCampaign, output_root: str | Path = "work/campaigns") -> dict[str, Path]:
    """Write manifests and a runnable CG starting model for a campaign."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", campaign.candidate_id):
        raise ValueError("candidate_id contains unsafe path characters")
    directory = Path(output_root).resolve() / campaign.campaign_id
    directory.mkdir(parents=True, exist_ok=False)
    manifest_path = directory / "campaign.json"
    manifest_path.write_text(json.dumps(to_jsonable(asdict(campaign)), ensure_ascii=False, indent=2), encoding="utf-8")
    task_root = directory / "tasks"
    for task in campaign.tasks:
        task_directory = task_root / task.task_id
        task_directory.mkdir(parents=True, exist_ok=True)
        (task_directory / "task.json").write_text(json.dumps(to_jsonable(asdict(task)), ensure_ascii=False, indent=2), encoding="utf-8")
    cg_task = next(task for task in campaign.tasks if task.scale == "coarse-grained")
    filler_pct = float(cg_task.conditions["filler_pct"])
    cg_model = build_cg_interface_model(ceria_particles=max(1, round(4 + filler_pct)))
    cg_paths = write_cg_interface_model(cg_model, task_root / cg_task.task_id)
    return {"campaign": manifest_path, **{f"cg_{name}": path for name, path in cg_paths.items()}}


def requirement_coverage(
    profiles: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    vasp_validation_root: str | Path | None = None,
    vasp_validation_roots: Mapping[str, str | Path] | None = None,
    md_baseline_root: str | Path | None = None,
) -> tuple[dict[str, str], ...]:
    """Expose software, environment, and scientific readiness separately."""
    selected_profiles = profiles or {}

    bulk_md_scientific_status = "待力场标定"
    bulk_md_note = "温度扫描、外部日志解析和回写已实现；交联拓扑、力场及时间尺度仍需标定"
    interface_scientific_status = "待多尺度标定"
    interface_note = "依赖调度和 CG 起始模型已实现；定量使用前需由 DFT、PMF 和实验标定"
    if md_baseline_root is not None:
        baseline_root = Path(md_baseline_root).expanduser().resolve()
        resin_files = (
            baseline_root / "structure_contract.json",
            baseline_root / "polyimide-cell" / "system.data",
            baseline_root / "polyimide-cell" / "forcefield.production",
        )
        interface_files = (
            baseline_root / "interface-cell" / "interface.data",
            baseline_root / "interface-cell" / "forcefield.production",
        )
        if all(path.is_file() for path in resin_files):
            bulk_md_scientific_status = "结构与前驱输入已生成"
            bulk_md_note = (
                "ODPA-ODA/DABA-多巴胺 DP8 全原子结构和 GAFF2 可读拓扑已生成；"
                "PDBA 交联、RESP 电荷、独立副本及 Tg/模量/CTE 验证完成前保持生产锁定"
            )
        if all(path.is_file() for path in interface_files):
            interface_scientific_status = "全原子前驱输入已生成"
            interface_note = (
                "树脂/PDA@CeO2(111) 前驱体已通过 LAMMPS 静态读取；"
                "PDA 键合参数、IP10a 核壳模型和 VASP 标定交叉项完成前保持生产锁定"
            )

    dft_scientific_status = "待验证输入"
    dft_scientific_note = (
        "任务调度、求解器调用、输出解析和回写已实现；"
        "生产计算仍需验证结构、赝势及 DFT(+U) 参数"
    )
    if vasp_validation_root is not None:
        validation_root = Path(vasp_validation_root).expanduser().resolve()
        plan_path = validation_root / "validation_plan.json"
        report_path = validation_root / "convergence_report.json"
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.is_file() else {}
            jobs = list(plan.get("jobs", []))
            total = int(plan.get("job_count", len(jobs)))
            completed = 0
            active = False
            failed = False
            for job in jobs:
                job_root = Path(str(job["path"]))
                marker_path = job_root / "run_status.json"
                marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else {}
                completed += marker.get("complete") is True
                active = active or marker.get("status") == "running"
                failed = failed or marker.get("status") == "failed"
                stage_path = job_root / ".model-preconverge" / "run_status.json"
                if stage_path.is_file():
                    stage = json.loads(stage_path.read_text(encoding="utf-8"))
                    active = active or stage.get("status") == "running"
                    failed = failed or stage.get("status") == "failed"
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
            if report.get("passed") is True and report.get("scientific_status") == "convergence-approved":
                dft_scientific_status = "收敛验证已通过"
                dft_scientific_note = "数值收敛报告已通过；生产任务仍需逐任务检查结构弛豫与结果质量"
            elif total and completed == total:
                dft_scientific_status = "待汇总收敛结果"
                dft_scientific_note = f"{completed}/{total} 项计算已完成；等待生成并审核收敛报告"
            elif total and active:
                dft_scientific_status = f"收敛验证中（{completed}/{total}）"
                dft_scientific_note = (
                    "VASP 输入与资源已生成，数值收敛矩阵正在运行；"
                    "生产计算保持锁定，直至收敛报告通过"
                )
            elif total and failed:
                dft_scientific_status = f"收敛验证受阻（{completed}/{total}）"
                dft_scientific_note = "已有收敛任务失败或中断；生产计算保持锁定，需检查运行日志后续跑"
            elif total:
                dft_scientific_status = f"输入已生成（{completed}/{total}）"
                dft_scientific_note = "VASP 输入与资源已生成；等待完成数值收敛矩阵并审核报告"
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            dft_scientific_status = "验证状态不可读"
            dft_scientific_note = "VASP 验证文件存在但无法解析；生产计算保持锁定"

    if vasp_validation_roots:
        facet_rows = []
        for facet, root in vasp_validation_roots.items():
            coverage = requirement_coverage(
                selected_profiles,
                vasp_validation_root=root,
                md_baseline_root=md_baseline_root,
            )
            facet_rows.append((str(facet), next(row for row in coverage if row["模块"] == "真实 DFT 计算")))
        dft_scientific_status = "；".join(
            f"CeO₂{facet}：{row['科学就绪']}" for facet, row in facet_rows
        )
        dft_scientific_note = (
            "各晶面独立进行 VASP 收敛验证并分别批准生产计算；"
            + "；".join(f"CeO₂{facet}：{row['说明']}" for facet, row in facet_rows)
        )

    def environment_status(*categories: str) -> str:
        if not profiles:
            return "待检测"
        configured = [
            selected_profiles.get(category, {})
            for category in categories
            if str(selected_profiles.get(category, {}).get("command") or "").strip()
        ]
        if not configured:
            return "未配置"
        if len(configured) != len(categories):
            return f"部分配置（{len(configured)}/{len(categories)}）"
        engines = sorted(
            {
                str(profile.get("engine") or "外部求解器").strip()
                for profile in configured
            }
        )
        return f"已配置（{'/'.join(engines)}）"

    return (
        {
            "模块": "候选配方数据库",
            "实现状态": "已实现",
            "运行环境": "已就绪",
            "科学就绪": "代理数据可用",
            "说明": "候选生成、配方指纹、候选库版本、来源追踪和 MySQL 持久化已实现；仍需真实实验持续扩充与校准",
        },
        {
            "模块": "量子化学代理预测",
            "实现状态": "已实现",
            "运行环境": "已就绪",
            "科学就绪": "趋势代理可用",
            "说明": "覆盖晶面、氧空位、羟基化和 PDA 作用趋势，不等同真实 DFT",
        },
        {
            "模块": "真实 DFT 计算",
            "实现状态": "已实现",
            "运行环境": environment_status("dft"),
            "科学就绪": dft_scientific_status,
            "说明": dft_scientific_note,
        },
        {
            "模块": "树脂 MD 与宽温域评价",
            "实现状态": "已实现",
            "运行环境": environment_status("bulk_md"),
            "科学就绪": bulk_md_scientific_status,
            "说明": bulk_md_note,
        },
        {
            "模块": "界面与粗粒化动力学",
            "实现状态": "已实现",
            "运行环境": environment_status("interface_md", "coarse_grained"),
            "科学就绪": interface_scientific_status,
            "说明": interface_note,
        },
        {
            "模块": "多尺度任务自动编排",
            "实现状态": "已实现",
            "运行环境": environment_status("dft", "bulk_md", "interface_md", "coarse_grained"),
            "科学就绪": "流程已就绪",
            "说明": "支持前置检查、最多 16 个并行后台任务、依赖推进、终止、实时状态、汇总回写和模型更新；生产任务仍受 DFT/MD 科学批准锁定",
        },
        {
            "模块": "外部结果回写与身份校验",
            "实现状态": "已实现",
            "运行环境": "已就绪",
            "科学就绪": "待真实结果积累",
            "说明": "计算与实验结果以候选编号、配方指纹和候选库版本校验后写回；保留历史结果，并按已批准生产计算、已验证输入、未批准手动任务确定优先级",
        },
        {
            "模块": "候选结果汇总与综合分析",
            "实现状态": "已实现",
            "运行环境": "已就绪",
            "科学就绪": "混合数据可用",
            "说明": "绑定当前多尺度候选，融合实验、真实 DFT、树脂 MD、界面 MD 与代理数据；缺失指标明确保留代理来源标记",
        },
        {
            "模块": "回归/分类筛选",
            "实现状态": "已实现",
            "运行环境": "已就绪",
            "科学就绪": "代理基线可用",
            "说明": "五输出、多目标排序、等级分类和不确定性推荐已实现；需用真实计算与实验数据验证泛化能力",
        },
        {
            "模块": "实验闭环",
            "实现状态": "已实现",
            "运行环境": "已就绪",
            "科学就绪": "待真实数据积累",
            "说明": "历史数据、带必填候选编号和配方指纹的单条/批量反馈、自动更新、模型归档和再推荐流程已实现",
        },
    )
