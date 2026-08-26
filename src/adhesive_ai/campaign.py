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


def campaign_task_frame(campaign: MultiscaleCampaign) -> pd.DataFrame:
    """Return a UI-friendly task table."""
    return pd.DataFrame([{
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


def requirement_coverage() -> tuple[dict[str, str], ...]:
    """Expose an honest implementation/readiness matrix for the UI."""
    return (
        {"模块": "候选配方数据库", "状态": "已实现", "说明": "组成、结构、工艺、五类性能及来源元数据可生成并持久化"},
        {"模块": "量子化学代理预测", "状态": "已实现（代理）", "说明": "覆盖晶面、氧空位、羟基化和PDA作用趋势，不等同真实DFT"},
        {"模块": "真实DFT计算", "状态": "部分实现", "说明": "已有任务矩阵、一键调度、输入/输出适配和回写；原子结构及DFT(+U)参数需外部验证"},
        {"模块": "树脂MD与宽温域评价", "状态": "部分实现", "说明": "一键调度、代理温度扫描与外部日志回写已实现；交联拓扑和力场需标定"},
        {"模块": "界面与粗粒化动力学", "状态": "部分实现", "说明": "依赖调度和CG起始模型已实现；定量使用前需由DFT、PMF和实验标定"},
        {"模块": "多尺度任务自动编排", "状态": "已实现（需外部环境）", "说明": "支持前置检查、并行后台执行、依赖推进、实时状态、汇总回写和模型更新"},
        {"模块": "回归/分类筛选", "状态": "已实现", "说明": "五输出、多目标排序、等级分类和不确定性推荐"},
        {"模块": "实验闭环", "状态": "已实现", "说明": "支持历史数据加载、单条/批量反馈、重训、版本归档和再推荐"},
    )
