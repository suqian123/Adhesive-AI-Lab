from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))
from adhesive_ai.candidate_library import build_candidate_library
from adhesive_ai.campaign import build_multiscale_campaign, campaign_task_frame, requirement_coverage, validate_candidate_contract, write_multiscale_campaign
from adhesive_ai.campaign_runner import (
    available_engine_profiles, campaign_environment_frame, campaign_run_frame, get_campaign_run,
    integrate_campaign_run, list_campaign_runs, load_engine_profiles,
    save_engine_profiles, start_campaign_run,
)
from adhesive_ai.mechanism import fuse_candidate_mechanism, mechanism_provenance_frame
from adhesive_ai.screening import OUTPUT_COLUMNS, recommend_next_experiments, save_model, screen_candidates
from adhesive_ai.database import DatabaseError, load_candidates, load_experiments, save_candidates, save_experiment, save_experiments, save_model_version
from adhesive_ai.jobs import JobRecord, list_jobs, parse_job_result, read_job_output, split_job_command, submit_job
from adhesive_ai.workflow import integrate_completed_job, load_connected_state

PLOTLY_CONFIG = {
    "displaylogo": False,
    "locale": "zh-CN",
    "locales": {
        "zh-CN": {
            "dictionary": {
                "Download plot as a PNG": "下载为 PNG 图片",
                "Download plot as a png": "下载为 PNG 图片",
                "Zoom": "缩放",
                "Pan": "平移",
                "Box Select": "框选",
                "Lasso Select": "套索选择",
                "Zoom in": "放大",
                "Zoom out": "缩小",
                "Autoscale": "自动缩放",
                "Reset axes": "重置坐标轴",
                "Toggle Spike Lines": "切换辅助线",
                "Show closest data on hover": "悬停显示最近数据",
                "Compare data on hover": "悬停比较数据",
                "Reset camera to default": "重置相机",
                "Reset camera to last save": "恢复相机视角",
                "Orbit rotation": "轨道旋转",
                "Orbital rotation": "轨道旋转",
                "Turntable rotation": "转盘旋转",
                "Fullscreen": "全屏",
                "Exit fullscreen": "退出全屏",
                "Full screen": "全屏",
            }
        }
    },
}

CANDIDATE_COLUMN_LABELS = {
    "candidate_id": "候选编号",
    "resin": "树脂基体",
    "resin_variant": "树脂结构类型",
    "dynamic_unit": "动态修复单元",
    "filler_pct": "PDA@CeO₂ 含量 (%)",
    "crosslink_density": "交联密度",
    "simulation_source": "计算数据来源",
    "predicted_wide_temp_adhesion_mpa": "预测宽温域黏附强度 (MPa)",
    "predicted_healing_efficiency_pct": "预测自修复效率 (%)",
    "predicted_atomic_oxygen_retention_pct": "预测抗原子氧保持率 (%)",
    "predicted_uv_retention_pct": "预测紫外保持率 (%)",
    "predicted_am_feasibility": "预测增材制造可行性 (%)",
    "predicted_multi_objective_score": "预测多目标综合评分",
    "predicted_screening_class": "预测筛选等级",
    "functional_group_type": "官能团类型",
    "blend_resin": "共混树脂",
    "blend_fraction": "共混比例",
    "cure_system": "固化体系",
    "curing_agent": "固化剂",
    "catalyst": "催化剂",
    "toughener_type": "增韧组分",
    "toughener_pct": "增韧组分含量 (%)",
    "filler_type": "仿生功能填料",
    "curing_temperature_c": "固化温度 (°C)",
    "curing_time_h": "固化时间 (h)",
    "post_cure_temperature_c": "后固化温度 (°C)",
    "post_cure_time_h": "后固化时间 (h)",
    "mixing_temperature_c": "混合温度 (°C)",
    "vacuum_degassing_min": "真空脱泡时间 (min)",
    "glass_transition_c": "玻璃化转变温度 (°C)",
    "free_volume_fraction": "自由体积分数",
    "elastic_modulus_gpa": "弹性模量 (GPa)",
    "cte_ppm_k": "热膨胀系数 (ppm/K)",
    "wide_temp_adhesion_mpa": "宽温域黏附强度 (MPa)",
    "healing_efficiency_pct": "自修复效率 (%)",
    "atomic_oxygen_retention_pct": "抗原子氧保持率 (%)",
    "uv_retention_pct": "紫外保持率 (%)",
    "am_feasibility": "增材制造可行性 (%)",
    "data_source": "数据来源",
    "acquisition_score": "实验推荐分数",
    "prediction_uncertainty": "预测不确定性",
}
CANDIDATE_VALUE_LABELS = {
    "resin": {"CE": "氰酸酯（CE）", "PN": "邻苯二甲腈（PN）", "PI": "聚酰亚胺（PI）", "Silicone": "硅橡胶", "PU": "聚氨酯（PU）"},
    "resin_variant": {
        "rigid_triazine": "刚性三嗪型", "flexible_bridge": "柔性桥联型", "high_functionality": "高官能度型",
        "rigid_linear": "刚性线型", "ether_linked": "醚键连接型", "star_like": "星形结构型",
        "rigid_imide": "刚性酰亚胺型", "flexible_imide": "柔性酰亚胺型", "fluorinated": "含氟改性型",
        "methyl_silicone": "甲基硅橡胶型", "phenyl_silicone": "苯基硅橡胶型", "hybrid_siloxane": "杂化硅氧烷型",
        "polyether_pu": "聚醚型", "polyester_pu": "聚酯型", "hard_segment": "高硬段型",
    },
    "dynamic_unit": {"None": "无动态修复单元", "Disulfide": "二硫键", "DielsAlder": "Diels-Alder 可逆键", "Boronic": "硼酸酯动态键", "Ionic": "离子/氢键簇"},
    "predicted_screening_class": {"A": "甲级", "B": "乙级", "C": "丙级", "D": "丁级"},
    "simulation_source": {"external": "外部真实计算"},
    "data_source": {"physics-informed-proxy": "物理启发代理", "hybrid-external": "外部计算与代理融合"},
    "functional_group_type": {
        "cyanate/phenolic": "氰酸酯/酚基", "phthalonitrile/nitrile-triazine": "邻苯二甲腈/腈基-三嗪",
        "imide/amine": "酰亚胺/胺基", "siloxane": "硅氧烷", "urethane": "氨基甲酸酯",
    },
    "cure_system": {"Thermal": "热固化", "Catalytic": "催化固化", "Stepwise": "阶梯固化"},
    "catalyst": {"none": "无", "imidazole": "咪唑", "amine-salt": "胺盐", "organometallic": "有机金属"},
    "toughener_type": {"none": "无", "rubber": "橡胶", "core-shell": "核壳颗粒", "thermoplastic": "热塑性增韧剂"},
    "curing_agent": {
        "phenolic": "酚类固化剂", "imidazole-activated": "咪唑活化体系", "maleimide": "马来酰亚胺",
        "aromatic diamine": "芳香族二胺", "benzoxazine": "苯并噁嗪", "dianhydride": "二酐",
        "diamine": "二胺", "amino-terminated": "氨基封端剂", "hydrosilane": "含氢硅烷",
        "alkoxy-silane": "烷氧基硅烷", "condensation": "缩合固化体系", "isocyanate": "异氰酸酯",
        "polyol-chain": "多元醇扩链剂", "blocked-isocyanate": "封闭型异氰酸酯",
    },
}

JOB_STATUS_LABELS = {
    "queued": "等待启动",
    "running": "运行中",
    "completed": "已完成",
    "failed": "失败",
}
CAMPAIGN_RUN_STATUS_LABELS = {
    "queued": "等待启动", "running": "自动计算中", "completed": "全部完成",
    "partial": "部分完成", "blocked": "前置条件阻塞", "failed": "计算失败",
}
CAMPAIGN_TASK_STATUS_LABELS = {
    "pending": "等待依赖", "queued": "等待执行", "running": "计算中",
    "completed": "已完成", "failed": "失败", "blocked": "阻塞",
}
CAMPAIGN_CATEGORY_LABELS = {
    "dft": "量子化学 DFT", "bulk_md": "树脂体相 MD",
    "interface_md": "界面 MD", "coarse_grained": "粗粒化动力学",
}
JOB_COMMAND_EXAMPLES = {
    "VASP": "vasp_std",
    "Quantum ESPRESSO": "pw.x -in scf.in",
    "CP2K": "cp2k.psmp -i cp2k.inp -o cp2k.out",
    "LAMMPS": "lmp -in in.production",
    "GROMACS": "gmx mdrun -deffnm production",
}
SINGLE_JOB_ENGINE_ORDER = ("VASP", "Quantum ESPRESSO", "CP2K", "LAMMPS", "GROMACS")
JOB_RESULT_FILE_EXAMPLES = {
    "VASP": "OUTCAR",
    "Quantum ESPRESSO": "scf.out",
    "CP2K": "cp2k.out",
    "LAMMPS": "log.lammps",
    "GROMACS": "potential.xvg",
}
CAMPAIGN_PROFILE_PRESETS = {
    "dft": {
        "VASP（直接调用）": {"engine": "VASP", "command": "vasp_std", "result_file": "OUTCAR"},
        "Quantum ESPRESSO（直接调用）": {
            "engine": "Quantum ESPRESSO", "command": "pw.x -in scf.in", "result_file": "scf.out",
        },
        "CP2K（直接调用）": {"engine": "CP2K", "command": "cp2k.psmp -i cp2k.inp -o cp2k.out", "result_file": "cp2k.out"},
    },
    "bulk_md": {
        "LAMMPS（直接调用）": {"engine": "LAMMPS", "command": "lmp -in in.production", "result_file": "log.lammps"},
        "GROMACS（直接调用）": {"engine": "GROMACS", "command": "gmx mdrun -deffnm production", "result_file": "potential.xvg"},
    },
    "interface_md": {
        "LAMMPS（直接调用）": {"engine": "LAMMPS", "command": "lmp -in in.production", "result_file": "log.lammps"},
        "GROMACS（直接调用）": {"engine": "GROMACS", "command": "gmx mdrun -deffnm production", "result_file": "potential.xvg"},
    },
    "coarse_grained": {
        "LAMMPS（直接调用）": {"engine": "LAMMPS", "command": "lmp -in in.cg", "result_file": "log.lammps"},
    },
}
CAMPAIGN_PROFILE_ENGINES = {
    "dft": ["VASP", "Quantum ESPRESSO", "CP2K"],
    "bulk_md": ["LAMMPS", "GROMACS"],
    "interface_md": ["LAMMPS", "GROMACS"],
    "coarse_grained": ["LAMMPS"],
}


def _campaign_profile_selector(
    category: str,
    current: dict[str, object],
) -> dict[str, object]:
    presets = CAMPAIGN_PROFILE_PRESETS[category]
    current_label = "使用已保存配置或 .env"
    custom_label = "自定义命令/任务包装器"
    available_presets = {
        label: profile
        for label, profile in presets.items()
        if campaign_environment_frame({category: profile}).iloc[0]["程序检查"] == "已找到可执行程序"
    }
    mode_key = f"campaign_profile_mode_{category}"
    signature_key = f"campaign_loaded_profile_signature_{category}"
    profile_signature = (
        str(current.get("engine") or ""),
        str(current.get("command") or ""),
        str(current.get("result_file") or ""),
    )
    if st.session_state.get(signature_key) != profile_signature:
        st.session_state[mode_key] = current_label
        st.session_state[signature_key] = profile_signature
    choices = [current_label, *available_presets, custom_label]
    if st.session_state.get(mode_key) not in choices:
        st.session_state[mode_key] = current_label
    choice = st.selectbox(
        "运行方式",
        choices,
        key=mode_key,
    )
    if choice == current_label:
        profile = dict(current)
        st.caption(
            f"当前：{profile.get('engine', '—')} · "
            f"{profile.get('command') or '尚未填写执行命令'}"
        )
        return profile
    if choice in available_presets:
        profile = {**current, **available_presets[choice], "category": category}
        st.code(str(profile["command"]), language=None)
        if category != "coarse_grained":
            st.caption("直接调用求解器前，任务目录必须具有经过验证的结构、拓扑、力场或 DFT 输入文件。")
        return profile

    engines = CAMPAIGN_PROFILE_ENGINES[category]
    current_engine = str(current.get("engine") or engines[0])
    engine = st.selectbox(
        "结果解析器",
        engines,
        index=engines.index(current_engine) if current_engine in engines else 0,
        key=f"campaign_custom_engine_{category}",
    )
    command = st.text_input(
        "执行命令",
        value=str(current.get("command") or ""),
        placeholder="例如：python tools/run_dft_task.py {task_file}",
        help="包含 {task_file} 时，包装器负责根据 task.json 生成并验证求解器输入。命令不会通过 shell 拼接。",
        key=f"campaign_custom_command_{category}",
    )
    result_file = st.text_input(
        "结果文件",
        value=str(current.get("result_file") or JOB_RESULT_FILE_EXAMPLES[engine]),
        key=f"campaign_custom_result_{category}",
    )
    return {**current, "category": category, "engine": engine, "command": command, "result_file": result_file}


def _job_duration(record: JobRecord) -> str:
    start = pd.Timestamp(record.started_at or record.submitted_at)
    end = pd.Timestamp(record.finished_at) if record.finished_at else pd.Timestamp.now(tz="UTC")
    seconds = max(0.0, (end - start).total_seconds())
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:d}时{minutes:02d}分{secs:02d}秒" if hours else f"{minutes:d}分{secs:02d}秒"


def _training_error_message(
    error: ValueError,
    *,
    external_rows: int,
    experiment_rows: int,
) -> str:
    """Translate model-validation failures into actionable user guidance."""
    detail = str(error).strip()
    lowered = detail.lower()
    if "duplicate labels" in lowered or ("duplicate" in lowered and "reindex" in lowered):
        return (
            "检测到同一候选存在重复训练记录，暂时无法确定应使用哪条数据。"
            "请合并或删除重复实验记录后重试。"
        )
    if external_rows <= 0 and experiment_rows <= 0:
        return (
            "当前没有已回写的外部计算或实验数据。请先在第 2 板块启动并完成计算，"
            "等待状态显示“已自动回写”后再运行 AI 筛选。"
        )
    if "不能为空" in detail or "empty" in lowered:
        return "当前候选库为空。请先在第 1 板块生成候选数据库，再运行 AI 筛选。"
    if "candidate_id" in detail:
        return f"训练数据中的候选编号无法匹配当前候选库：{detail}。请检查候选编号后重试。"
    return f"训练数据校验未通过：{detail}。请检查已回写的计算结果或实验数据后重试。"


def _localized_campaign_run_table(record: dict[str, object]) -> pd.DataFrame:
    display = campaign_run_frame(record)
    display["计算尺度"] = display["计算尺度"].map(CAMPAIGN_VALUE_LABELS["scale"]).fillna(display["计算尺度"])
    display["计算类型"] = display["计算类型"].map(CAMPAIGN_CATEGORY_LABELS).fillna(display["计算类型"])
    display["状态"] = display["状态"].map(CAMPAIGN_TASK_STATUS_LABELS).fillna(display["状态"])
    return display


def _style_program_check(frame: pd.DataFrame):
    """Highlight unavailable or invalid external launchers in red."""
    def highlight(value: object) -> str:
        if str(value) == "已找到可执行程序":
            return ""
        return "background-color: #fee2e2; color: #b91c1c; font-weight: 700;"

    return frame.style.map(highlight, subset=["程序检查"])


@st.fragment(run_every=5)
def _render_campaign_run_status(run_id: str) -> None:
    record = get_campaign_run(run_id)
    status = str(record.get("status"))
    tasks = list(record.get("tasks", []))
    counts = {name: sum(task.get("status") == name for task in tasks) for name in CAMPAIGN_TASK_STATUS_LABELS}
    st.caption(
        f"运行状态：{CAMPAIGN_RUN_STATUS_LABELS.get(status, status)} · "
        f"已完成 {counts['completed']}/{len(tasks)} · 计算中 {counts['running'] + counts['queued']} · "
        f"阻塞 {counts['blocked']} · 失败 {counts['failed']}"
    )
    st.progress(counts["completed"] / max(1, len(tasks)), text=f"多尺度任务完成度 {counts['completed']}/{len(tasks)}")
    st.dataframe(_localized_campaign_run_table(record), width="stretch", hide_index=True)
    if status in {"queued", "running"}:
        st.info("后台监督进程正在自动推进任务；本区域每 5 秒刷新一次。")
    elif status == "blocked":
        st.warning("任务包已生成，但当前没有可继续执行的任务。请根据表格中的原因补齐外部程序配置或输入文件。")
    elif status == "failed":
        st.error("本次自动计算失败。请查看任务表中的错误原因和对应外部任务日志。")
    elif status in {"completed", "partial"} and not record.get("integrated_at"):
        terminal_key = f"campaign_terminal_seen_{run_id}"
        if st.session_state.get(terminal_key) != record.get("updated_at"):
            st.session_state[terminal_key] = record.get("updated_at")
            st.rerun()
    elif record.get("integrated_at"):
        st.success(f"结果已回写，模型版本：{record.get('integrated_model_version', '—')}")


@st.fragment(run_every=5)
def _watch_standalone_external_jobs() -> None:
    """Rerun the app when a standalone job reaches a terminal state."""
    standalone_jobs = [
        job for job in list_jobs()
        if not (job.metadata or {}).get("campaign_run_id")
    ]
    terminal_signature = tuple(
        sorted(
            (job.job_id, job.status)
            for job in standalone_jobs
            if job.status in {"completed", "failed"}
        )
    )
    state_key = "standalone_external_terminal_signature"
    previous_signature = st.session_state.get(state_key)
    st.session_state[state_key] = terminal_signature
    if previous_signature is not None and previous_signature != terminal_signature:
        st.rerun()
    active_count = sum(job.status in {"queued", "running"} for job in standalone_jobs)
    if active_count:
        st.caption(f"正在自动监控 {active_count} 个单任务；每 5 秒检查状态，完成后自动回写。")

EXPERIMENT_COLUMN_LABELS = {
    "candidate_id": "候选编号",
    "wide_temp_adhesion_mpa": "宽温域黏附强度 (MPa)",
    "healing_efficiency_pct": "自修复效率 (%)",
    "atomic_oxygen_retention_pct": "抗原子氧保持率 (%)",
    "uv_retention_pct": "紫外保持率 (%)",
    "am_feasibility": "增材制造可行性 (%)",
    "measured_tg_c": "实测玻璃化转变温度 (°C)",
    "measured_free_volume": "实测自由体积分数",
    "measured_chain_mobility": "实测链段运动能力",
    "measured_cohesive_energy_density": "实测内聚能密度 (MJ/m³)",
    "measured_modulus_gpa": "实测弹性模量 (GPa)",
    "measured_cte_ppm_k": "实测热膨胀系数 (ppm/K)",
}
EXPERIMENT_COLUMN_ALIASES = {
    label: name for name, label in EXPERIMENT_COLUMN_LABELS.items()
} | {
    "candidate": "candidate_id", "候选ID": "candidate_id", "候选 id": "candidate_id",
    "adhesion_strength_mpa": "wide_temp_adhesion_mpa", "adhesion_mpa": "wide_temp_adhesion_mpa",
    "self_healing_efficiency_pct": "healing_efficiency_pct",
    "atomic_oxygen_pct": "atomic_oxygen_retention_pct", "uv_retention": "uv_retention_pct",
    "additive_manufacturing_feasibility_pct": "am_feasibility",
    "tg_c": "measured_tg_c", "glass_transition_temperature_c": "measured_tg_c",
    "measured_free_volume_fraction": "measured_free_volume",
    "elastic_modulus": "measured_modulus_gpa", "thermal_expansion_coefficient_ppm_k": "measured_cte_ppm_k",
}
EXPERIMENT_OPTIONAL_COLUMNS = (
    "measured_tg_c", "measured_free_volume", "measured_chain_mobility",
    "measured_cohesive_energy_density", "measured_modulus_gpa", "measured_cte_ppm_k",
)

EXPERIMENT_METADATA_LABELS = {
    "test_batch": "实验批次", "test_temperature_c": "测试温度 (°C)",
    "source": "实验来源", "created_at": "记录时间",
}
CAMPAIGN_COLUMN_LABELS = {
    "task_id": "任务编号", "scale": "计算尺度", "objective": "计算目标",
    "engines": "可用计算引擎", "readiness": "任务就绪状态", "expected_outputs": "预期输出",
}
CAMPAIGN_VALUE_LABELS = {
    "scale": {"quantum": "量子化学", "atomistic-md": "全原子分子动力学", "coarse-grained": "粗粒化动力学"},
    "objective": {
        "atomic-oxygen-surface-chemistry": "原子氧表面吸附与反应",
        "pda-resin-and-surface-binding": "PDA-树脂及表面结合",
        "crosslinked-resin-temperature-sweep": "交联树脂宽温域扫描",
        "resin-pda-ceo2-interface-binding": "树脂/PDA@CeO₂ 界面结合",
        "pda-ceo2-dispersion-and-reinforcement": "PDA@CeO₂ 分散与增强",
    },
    "readiness": {
        "planned-requires-atomistic-structure": "待提供并验证原子结构",
        "planned-requires-crosslinked-topology": "待提供交联拓扑与力场",
        "planned-requires-interface-topology": "待提供界面拓扑与交互参数",
        "generator-available-requires-calibration": "可生成起始模型，待标定",
    },
}
MECHANISM_SECTION_LABELS = {
    "quantum": "量子化学", "md": "树脂分子动力学", "interface": "界面与粗粒化", "performance": "性能输出",
}
MECHANISM_METRIC_LABELS = {
    "oxygen_adsorption_ev": "原子氧吸附能", "oxygen_reaction_barrier_ev": "反应路径能垒",
    "oxygen_reaction_energy_ev": "反应能", "ce3_fraction": "Ce³⁺ 分数",
    "reactive_oxygen_capture_index": "活性氧捕获能力", "pda_resin_hbond_ev": "PDA-树脂氢键结合能",
    "pda_surface_coordination_ev": "PDA-表面配位能", "pda_reaction_ev": "PDA 化学反应能",
    "glass_transition_c": "玻璃化转变温度", "free_volume_fraction": "自由体积分数",
    "cohesive_energy_density_mj_m3": "内聚能密度", "elastic_modulus_gpa": "弹性模量",
    "cte_ppm_k": "热膨胀系数", "chain_mobility": "链段运动能力",
    "adhesion_retention": "黏附保持率", "self_healing_efficiency": "自修复效率",
    "binding_energy_mj_m2": "界面结合能", "adhesion_work_mj_m2": "黏附功",
    "hydrogen_bond_density_nm2": "氢键密度", "coordination_bond_density_nm2": "配位键密度",
    "covalent_reaction_fraction": "共价反应分数", "dispersion_index": "粗粒化分散指数",
    "coarse_grained_reinforcement_index": "填料增强指数", "stability_score": "界面稳定性",
    **{name: label for name, label in EXPERIMENT_COLUMN_LABELS.items()},
}
CALCULATION_OUTPUT_LABELS = {
    "adsorption_energy_ev": "原子氧吸附能", "reaction_energy_ev": "反应能",
    "reaction_barrier_ev": "反应路径能垒", "ce3_fraction": "Ce³⁺ 分数",
    "reactive_oxygen_capture_index": "活性氧捕获能力", "pda_resin_hbond_ev": "PDA-树脂氢键结合能",
    "pda_surface_coordination_ev": "PDA-表面配位能", "pda_reaction_ev": "PDA 化学反应能",
    "glass_transition_c": "玻璃化转变温度", "free_volume_fraction": "自由体积分数",
    "cohesive_energy_density_mj_m3": "内聚能密度", "elastic_modulus_gpa": "弹性模量",
    "cte_ppm_k": "热膨胀系数", "chain_mobility": "链段运动能力",
    "interface_binding_energy_mj_m2": "界面结合能", "interface_covalent_bond_count": "界面共价键数量",
    "adhesion_retention": "黏附保持率", "self_healing_efficiency": "自修复效率",
    "dispersion_index": "粗粒化分散指数", "coarse_grained_reinforcement_index": "填料增强指数",
}


def _source_label(value: object) -> object:
    if value is None or pd.isna(value):
        return "未提供"
    text = str(value)
    if text.strip("()").lower() == "experiment":
        return "实验数据"
    if text == "physics-informed-proxy":
        return "物理启发代理"
    if text == "not-persisted":
        return "未保存真实轨迹"
    if text.startswith("external:"):
        identifier = text.removeprefix("external:").replace("+proxy-temperature-shape", "")
        suffix = " + 代理温度趋势" if "+proxy-temperature-shape" in text else ""
        return f"外部真实计算（{identifier}）{suffix}"
    if text.startswith("experiment:"):
        return f"实验数据（{text.removeprefix('experiment:')}）"
    if text.startswith("model:"):
        return f"模型预测（{text.removeprefix('model:')}）"
    return text


def _localized_candidate_table(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    display = frame.reindex(columns=columns).copy()
    for column, labels in CANDIDATE_VALUE_LABELS.items():
        if column in display:
            display[column] = display[column].map(labels).fillna(display[column])
    return display.rename(columns=CANDIDATE_COLUMN_LABELS)


def _localized_value(column: str, value: object) -> object:
    labels = CANDIDATE_VALUE_LABELS.get("resin" if column == "blend_resin" else column, {})
    try:
        return labels.get(value, value)
    except TypeError:
        return value


def _read_experiment_csv(uploaded_file: object) -> tuple[pd.DataFrame | None, list[str], list[str]]:
    """Normalize and validate uploaded experimental feedback without silent coercion."""
    if uploaded_file is None:
        return None, [], []
    try:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        frame = pd.read_csv(uploaded_file)
    except Exception as exc:
        return None, [f"CSV 无法读取：{exc}"], []
    frame.columns = [str(column).strip() for column in frame.columns]
    alias_lookup = {str(key).strip().lower(): value for key, value in EXPERIMENT_COLUMN_ALIASES.items()}
    renamed: dict[str, str] = {}
    duplicate_targets: list[str] = []
    for column in frame.columns:
        target = EXPERIMENT_COLUMN_ALIASES.get(column, alias_lookup.get(column.lower(), column))
        if target in renamed.values() and target != column:
            duplicate_targets.append(target)
        renamed[column] = target
    frame = frame.rename(columns=renamed)
    required = ("candidate_id",) + OUTPUT_COLUMNS
    errors = [f"列名重复映射：{', '.join(duplicate_targets)}"] if duplicate_targets else []
    missing = [EXPERIMENT_COLUMN_LABELS.get(name, name) for name in required if name not in frame.columns]
    if missing:
        errors.append("缺少必需列：" + "、".join(missing))
    if "candidate_id" in frame.columns:
        ids = frame["candidate_id"].astype("string").str.strip()
        if ids.isna().any() or (ids == "").any():
            errors.append("候选编号不能有空值。")
        frame["candidate_id"] = ids
    warnings: list[str] = []
    if "candidate_id" in frame.columns and frame["candidate_id"].duplicated().any():
        warnings.append("存在重复候选编号，训练时将保留最后一条记录。")
    for column in OUTPUT_COLUMNS + EXPERIMENT_OPTIONAL_COLUMNS:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        invalid = values.isna() & frame[column].notna()
        if invalid.any():
            errors.append(f"{EXPERIMENT_COLUMN_LABELS.get(column, column)} 含非数值内容。")
        if column in required and values.isna().any():
            errors.append(f"{EXPERIMENT_COLUMN_LABELS.get(column, column)} 不能有空值。")
        frame[column] = values
    for column in OUTPUT_COLUMNS[1:]:
        if column in frame.columns:
            out_of_range = frame[column].notna() & ~frame[column].between(0, 100)
            if out_of_range.any():
                errors.append(f"{EXPERIMENT_COLUMN_LABELS[column]} 应在 0 到 100 之间。")
    if "wide_temp_adhesion_mpa" in frame.columns:
        invalid = frame["wide_temp_adhesion_mpa"].notna() & (frame["wide_temp_adhesion_mpa"] < 0)
        if invalid.any():
            errors.append("宽温域黏附强度不能为负值。")
    return frame, errors, warnings

st.set_page_config(
    page_title="粘附材料多尺度模拟平台",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.html(
    """
    <script>
    (() => {
        const labels = {
            "Fullscreen": "全屏",
            "Exit fullscreen": "退出全屏",
            "Enter fullscreen": "进入全屏",
            "Close fullscreen": "退出全屏",
            "Orbital rotation": "轨道旋转",
            "Range": "范围",
            "Minimum length": "最小长度",
            "Average length": "平均长度",
            "Maximum length": "最大长度",
            "Sort ascending": "升序排序",
            "Sort descending": "降序排序",
            "Clear sort": "清除排序",
            "Reset sort": "重置排序",
            "Hide column": "隐藏列",
            "Show all columns": "显示所有列",
            "Show/hide columns": "显示/隐藏列",
            "Show columns": "显示列",
            "Hide columns": "隐藏列",
            "Statistics": "统计信息",
            "Format": "格式", 
            "Automatic": "自动",
            "Localized": "本地化",
            "Plain": "普通格式",
            "Compact": "紧凑格式",
            "Dollar": "美元",
            "Euro": "欧元",
            "Yen": "日元",
            "Percent": "百分比",
            "Scientific": "科学计数法",
            "Accounting": "会计格式",
            "Distance": "相对时间",
            "Calendar": "日历日期",
            "Search": "搜索",
            "Type to search": "输入关键词搜索",
            "Type to search...": "输入关键词搜索",
            "Select all": "全选",
            "Deselect all": "取消全选",
            "(index)": "索引",
            "Index": "索引",
            "(experiment)": "实验数据",
            "(Experiment)": "实验数据",
            "Download as CSV": "下载为 CSV",
            "Copy": "复制",
            "Copy to clipboard": "复制到剪贴板",
            "Reset columns": "重置列",
            "Autosize columns": "自动调整列宽",
            "Autosize": "自动调整大小",
            "Pin column": "固定列",
            "Unpin column": "取消固定列",
            "Column actions": "列操作",
            "Values": "值",
            "Count": "数量",
            "Mean": "平均值",
            "Std": "标准差",
            "Min": "最小值",
            "Max": "最大值",
            "Median": "中位数",
            "Sum": "总和",
            "Unique": "唯一值数量",
            "Missing": "缺失值",
            "Null": "空值",
            "Data type": "数据类型",
            "Empty": "空值",
            "Distinct": "不同值",
            "Average": "平均值",
            "Standard deviation": "标准差",
            "Minimum": "最小值",
            "Maximum": "最大值",
            "Number of values": "值数量",
            "Number of distinct values": "不同值数量",
            "Number of empty values": "空值数量",
            "Percentage of empty values": "空值占比",
            "25th percentile": "第 25 百分位数",
            "75th percentile": "第 75 百分位数",
        };
        const translate = () => {
            for (const [source, target] of Object.entries(labels)) {
                document.querySelectorAll(`[aria-label="${source}"], [title="${source}"], [data-title="${source}"], [placeholder="${source}"]`).forEach((node) => {
                    if (node.getAttribute("aria-label") === source) node.setAttribute("aria-label", target);
                    if (node.getAttribute("title") === source) node.setAttribute("title", target);
                    if (node.getAttribute("data-title") === source) node.setAttribute("data-title", target);
                    if (node.getAttribute("placeholder") === source) node.setAttribute("placeholder", target);
                });
            }
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                if (["SCRIPT", "STYLE", "NOSCRIPT"].includes(node.parentElement?.tagName)) continue;
                const source = node.nodeValue.trim();
                if (labels[source]) {
                    node.nodeValue = node.nodeValue.replace(source, labels[source]);
                    continue;
                }
                const filteredResultCount = source.match(/^(\\d+)\\s+of\\s+(\\d+)\\s+results?$/i);
                if (filteredResultCount) {
                    node.nodeValue = node.nodeValue.replace(source, `已显示 ${filteredResultCount[1]} 条，共 ${filteredResultCount[2]} 条结果`);
                    continue;
                }
                const resultCount = source.match(/^(\\d+)\\s+results?$/i);
                if (resultCount) node.nodeValue = node.nodeValue.replace(source, `${resultCount[1]} 条结果`);
            }
        };
        translate();
        new MutationObserver(translate).observe(document.body, {
            subtree: true,
            childList: true,
            attributes: true,
            characterData: true,
            attributeFilter: ["aria-label", "title", "data-title", "placeholder"],
        });
        // Streamlit widgets use the global hotkeys registry. Remove any
        // accidentally registered plain-C action in addition to blocking the
        // browser event itself. Clipboard shortcuts remain available.
        const removeCHotkeys = () => {
            try {
                const hotkeys = window.hotkeys;
                if (hotkeys?.unbind) {
                    hotkeys.unbind("c");
                    hotkeys.unbind("C");
                }
            } catch (error) {
                // The registry is optional and can be unavailable during boot.
            }
        };
        const blockCShortcut = (event) => {
            const tagName = event.target?.tagName;
            const isEditable = event.target?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(tagName);
            const isPlainC = (event.key?.toLowerCase() === "c" || event.code === "KeyC" || event.keyCode === 67 || event.which === 67)
                && !event.ctrlKey && !event.metaKey && !event.altKey;
            if (isPlainC && !isEditable) {
                event.preventDefault();
                event.stopImmediatePropagation();
            }
        };
        const bindShortcutBlocker = (target) => {
            try {
                target.addEventListener("keydown", blockCShortcut, true);
                target.addEventListener("keypress", blockCShortcut, true);
                target.addEventListener("keyup", blockCShortcut, true);
            } catch (error) {
                // The component can be isolated from the parent document.
            }
        };
        bindShortcutBlocker(document);
        bindShortcutBlocker(window);
        removeCHotkeys();
        // Hotkeys can be registered after the first render, so repeat the
        // removal briefly while Streamlit mounts and updates its widgets.
        const hotkeyCleanup = window.setInterval(removeCHotkeys, 250);
        window.setTimeout(() => window.clearInterval(hotkeyCleanup), 10000);
        try {
            bindShortcutBlocker(window.parent.document);
            bindShortcutBlocker(window.parent);
        } catch (error) {
            // Parent access is unavailable in isolated deployments.
        }

    })();
    </script>
    """,
    unsafe_allow_javascript=True,
)

st.markdown(
    """
    <style>
    .stApp { background:#fbfcfd; }
    header[data-testid="stHeader"] { background:transparent; }
    [data-testid="stToolbarActions"],
    [data-testid="stToolbarActionButton"],
    [data-testid="stAppDeployButton"],
    [data-testid="stMainMenu"],
    [data-testid="stMainMenuButton"] { display:none !important; }
    [data-testid="stDecoration"] { display:none; }
    .block-container { padding-top:1.25rem; }
    [data-testid="stSidebar"] { background:#f6f3ed; }
    .hero { padding:1rem 0 .8rem; border-bottom:1px solid #d9e0e7; margin-bottom:1rem; }
    .hero h1 { margin:.2rem 0 .35rem; font-size:2.2rem; }
    .hero p { color:#657383; }
    .metric { background:#fff; border:1px solid #d9e0e7; border-radius:8px; padding:1rem; min-height:105px; }
    .metric-label,.metric-note { color:#657383; font-size:.82rem; }
    .metric-value { font-size:1.65rem; font-weight:750; margin:.3rem 0; }
    </style>
    """, unsafe_allow_html=True,
)
st.markdown(
    '<div class="hero">'
    '<h1>粘附材料 AI 辅助多尺度分子模拟预测</h1>'
    '<p>从配方与分子结构出发，快速评估界面结合趋势、粘附功和粗粒化吸附稳定性。</p></div>',
    unsafe_allow_html=True,
)

saved_campaign_profiles = load_engine_profiles()
with st.expander("需求实现与科学就绪状态", expanded=False):
    st.dataframe(
        pd.DataFrame(requirement_coverage(saved_campaign_profiles)),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "“已实现”只表示软件功能已经具备；“已配置”表示已保存外部求解器命令，"
        "不代表生产输入、力场或科学结果已经验证。"
    )

st.divider()
st.subheader("1. 候选数据库")
candidate_count = 360
base_candidate_frame = build_candidate_library(max_records=candidate_count, seed=11)
candidate_frame = base_candidate_frame
candidate_ids = candidate_frame["candidate_id"].astype(str).tolist()

candidate_signature = ("candidate-db-v2", candidate_count, tuple(candidate_ids))
try:
    if st.session_state.get("persisted_candidate_signature") != candidate_signature:
        save_candidates(base_candidate_frame)
        st.session_state["persisted_candidate_signature"] = candidate_signature
    database_candidate_frame = load_candidates()
    database_ids = database_candidate_frame.get("candidate_id", pd.Series(dtype=str)).astype(str).tolist()
    if set(database_ids) == set(candidate_ids) and len(database_ids) == len(candidate_ids):
        database_candidate_frame["candidate_id"] = database_candidate_frame["candidate_id"].astype(str)
        database_candidate_frame = database_candidate_frame.set_index("candidate_id").reindex(candidate_ids).reset_index()
        missing_database_columns = [column for column in base_candidate_frame.columns if column not in database_candidate_frame]
        if not missing_database_columns:
            base_candidate_frame = database_candidate_frame.reindex(columns=base_candidate_frame.columns)
            candidate_frame = base_candidate_frame
    candidate_frame, experiment_history, external_payloads = load_connected_state(base_candidate_frame)
except DatabaseError as exc:
    experiment_history = pd.DataFrame()
    external_payloads = {}
    st.warning(f"MySQL 当前不可用，候选筛选将在内存中继续运行：{exc}")

integration_notices: list[str] = []
integration_errors: list[str] = []
for campaign_run_record in list_campaign_runs():
    if (
        campaign_run_record.get("status") not in {"completed", "partial"}
        or campaign_run_record.get("integrated_at")
        or str(campaign_run_record.get("candidate_id")) not in set(candidate_frame["candidate_id"].astype(str))
    ):
        continue
    try:
        integrated = integrate_campaign_run(
            str(campaign_run_record["run_id"]),
            candidate_frame,
            experiments=experiment_history,
            top_n=12,
        )
        if integrated is not None:
            candidate_frame = integrated.candidates
            external_payloads[integrated.candidate_id] = integrated.payload
            st.session_state["latest_candidate_screening"] = {
                "signature": candidate_signature,
                "shortlist": integrated.shortlist,
                "model": integrated.model,
            }
            integration_notices.append(
                f"多尺度任务 {integrated.job_id} 已汇总并回写候选 {integrated.candidate_id}，模型已重新训练。"
            )
    except Exception as exc:
        integration_errors.append(f"多尺度任务 {campaign_run_record.get('run_id')} 回写失败：{exc}")
for completed_job in list_jobs():
    job_metadata = completed_job.metadata or {}
    if (
        completed_job.status != "completed"
        or job_metadata.get("integrated_at")
        or job_metadata.get("campaign_run_id")
        or not job_metadata.get("candidate_id")
    ):
        continue
    try:
        integrated = integrate_completed_job(
            completed_job.job_id,
            candidate_frame,
            experiments=experiment_history,
            top_n=12,
        )
        if integrated is not None:
            candidate_frame = integrated.candidates
            external_payloads[integrated.candidate_id] = integrated.payload
            st.session_state["latest_candidate_screening"] = {
                "signature": candidate_signature,
                "shortlist": integrated.shortlist,
                "model": integrated.model,
            }
            integration_notices.append(f"任务 {integrated.job_id} 已回写候选 {integrated.candidate_id}，模型已重新训练。")
    except Exception as exc:
        integration_errors.append(f"任务 {completed_job.job_id} 回写失败：{exc}")
for notice in integration_notices:
    st.success(notice)
for error in integration_errors:
    st.warning(error)
feedback_notice = st.session_state.pop("feedback_notice", None)
if feedback_notice:
    st.success(feedback_notice)

external_candidate_count = len(external_payloads)
contract_report = validate_candidate_contract(candidate_frame)
if not contract_report["valid"]:
    st.warning(f"候选特征契约不完整：缺少 {contract_report['missing_columns']}，空值 {contract_report['null_counts']}")
with st.expander("候选数据库预览", expanded=True):
    composition_columns = [
        "candidate_id", "resin", "resin_variant", "functional_group_type", "blend_resin", "blend_fraction",
        "dynamic_unit", "curing_agent", "catalyst", "toughener_type", "toughener_pct", "filler_pct", "crosslink_density",
    ]
    process_columns = [
        "candidate_id", "curing_temperature_c", "curing_time_h", "post_cure_temperature_c",
        "post_cure_time_h", "mixing_temperature_c", "vacuum_degassing_min",
    ]
    performance_columns = [
        "candidate_id", "glass_transition_c", "free_volume_fraction", "elastic_modulus_gpa", "cte_ppm_k",
        "wide_temp_adhesion_mpa", "healing_efficiency_pct", "atomic_oxygen_retention_pct",
        "uv_retention_pct", "am_feasibility", "data_source",
    ]
    composition_tab, process_tab, target_tab = st.tabs(["组成与结构", "工艺条件", "性能与来源"])
    with composition_tab:
        st.dataframe(_localized_candidate_table(candidate_frame, composition_columns), width="stretch", hide_index=True)
    with process_tab:
        st.dataframe(_localized_candidate_table(candidate_frame, process_columns), width="stretch", hide_index=True)
    with target_tab:
        st.dataframe(_localized_candidate_table(candidate_frame, performance_columns), width="stretch", hide_index=True)

st.divider()
st.subheader("2. 多尺度计算方案")


def _render_multiscale_campaign_section() -> None:
    with st.expander("查看并生成计算任务矩阵", expanded=True):
        campaign_candidate_id = st.selectbox(
            "方案候选编号（必填）",
            candidate_ids,
            index=None,
            placeholder="请选择方案候选编号",
            key="campaign_candidate_id",
        )
        if campaign_candidate_id is None:
            st.info("请先选择方案候选编号，选择后才会生成计算任务矩阵和启动配置。")
            return
        campaign_row = candidate_frame.loc[candidate_frame["candidate_id"].astype(str) == campaign_candidate_id].iloc[0]
        campaign = build_multiscale_campaign(campaign_row)
        campaign_frame = campaign_task_frame(campaign)
        scale_counts = campaign_frame.groupby("scale").size().to_dict()
        st.caption(
            f"共 {len(campaign.tasks)} 个任务：量子化学 {scale_counts.get('quantum', 0)}、"
            f"原子级 MD {scale_counts.get('atomistic-md', 0)}、粗粒化 {scale_counts.get('coarse-grained', 0)}。"
        )
        campaign_display = campaign_frame.copy()
        for column, labels in CAMPAIGN_VALUE_LABELS.items():
            campaign_display[column] = campaign_display[column].map(labels).fillna(campaign_display[column])
        campaign_display["expected_outputs"] = campaign_display["expected_outputs"].apply(
            lambda value: "、".join(CALCULATION_OUTPUT_LABELS.get(name.strip(), name.strip()) for name in str(value).split(","))
        )
        campaign_display = campaign_display.rename(columns=CAMPAIGN_COLUMN_LABELS)
        st.dataframe(campaign_display, width="stretch", hide_index=True)
        if st.button("生成多尺度计算任务包", key="write_campaign"):
            try:
                written_campaign = write_multiscale_campaign(campaign)
                st.success(f"任务包已生成：{written_campaign['campaign'].parent}")
            except Exception as exc:
                st.error(f"任务包生成失败：{exc}")

    st.markdown("#### 一键启动多尺度计算")
    st.caption(f"当前计算候选：{campaign_candidate_id}")
    st.caption(
        "一次生成全部任务并按“量子化学/体相 MD → 界面 MD → 粗粒化”依赖顺序后台执行；"
        "完成后自动汇总、回写候选数据库并更新 AI 模型。"
    )
    campaign_profiles: dict[str, dict[str, object]] = {}
    with st.expander("选择计算引擎与执行方式", expanded=True):
        dft_tab, bulk_tab, interface_tab, cg_tab = st.tabs(["量子化学 DFT", "树脂体相 MD", "界面 MD", "粗粒化"])
        with dft_tab:
            campaign_profiles["dft"] = _campaign_profile_selector("dft", saved_campaign_profiles["dft"])
        with bulk_tab:
            campaign_profiles["bulk_md"] = _campaign_profile_selector("bulk_md", saved_campaign_profiles["bulk_md"])
        with interface_tab:
            campaign_profiles["interface_md"] = _campaign_profile_selector(
                "interface_md", saved_campaign_profiles["interface_md"]
            )
        with cg_tab:
            campaign_profiles["coarse_grained"] = _campaign_profile_selector(
                "coarse_grained", saved_campaign_profiles["coarse_grained"]
            )

        save_profile_col, profile_note_col = st.columns([1, 2])
        with save_profile_col:
            if st.button("保存为项目默认配置", key="save_campaign_profiles", width="stretch"):
                try:
                    saved_profile_path = save_engine_profiles(campaign_profiles)
                    st.success(f"配置已保存：{saved_profile_path}")
                except Exception as exc:
                    st.error(f"配置保存失败：{exc}")
        with profile_note_col:
            st.caption("不保存也可直接启动；保存后，下次打开页面会自动载入当前选择。")

        st.markdown("##### 启动前检查")
        environment_frame = campaign_environment_frame(campaign_profiles)
        st.dataframe(_style_program_check(environment_frame), width="stretch", hide_index=True)
        st.caption(
            "高级用法仍可通过 .env 配置命令。自定义命令可使用 {task_file}、{task_dir}、"
            "{candidate_id}、{campaign_id} 占位符；包含 {task_file} 时按任务包装器执行。"
        )

    parallel_col, launch_col = st.columns([1, 2])
    try:
        configured_parallel = int(os.getenv("ADHESIVE_CAMPAIGN_MAX_PARALLEL", "2"))
    except ValueError:
        configured_parallel = 2
    configured_parallel = min(16, max(1, configured_parallel))
    with parallel_col:
        campaign_max_parallel = st.number_input(
            "最大并行任务数", min_value=1, max_value=16, value=configured_parallel, step=1, key="campaign_max_parallel"
        )
    with launch_col:
        st.write("")
        launch_campaign = st.button(
            "一键生成并启动多尺度计算", type="primary", key="launch_multiscale_campaign", width="stretch"
        )

    if launch_campaign:
        try:
            with st.spinner("正在生成计算包、检查前置条件并提交首批任务……"):
                launched_run = start_campaign_run(
                    campaign,
                    profiles=campaign_profiles,
                    max_parallel=int(campaign_max_parallel),
                )
            st.session_state["selected_campaign_run"] = launched_run["run_id"]
            if launched_run["status"] == "blocked":
                st.warning("计算包已生成，但外部程序或输入条件尚未满足；请查看下方任务状态。")
            else:
                st.success(f"多尺度计算已在后台启动：{launched_run['run_id']}")
        except Exception as exc:
            st.error(f"一键启动失败：{exc}")

    with st.expander("高级：单独提交外部计算任务", expanded=False):
        st.caption("仅用于调试或补算单个 DFT/MD 任务；日常计算无需填写以下命令。")
        standalone_profiles = available_engine_profiles(saved_campaign_profiles)
        available_job_engines = [
            engine
            for engine in SINGLE_JOB_ENGINE_ORDER
            if any(str(profile.get("engine")) == engine for profile in standalone_profiles.values())
        ]
        hidden_job_engines = [engine for engine in SINGLE_JOB_ENGINE_ORDER if engine not in available_job_engines]
        if hidden_job_engines:
            st.caption(f"已隐藏当前不可用的引擎：{'、'.join(hidden_job_engines)}")
        engine_selection_enabled = bool(available_job_engines)
        if not engine_selection_enabled:
            st.warning("当前没有通过程序检查的外部计算引擎，请先在上方保存可用配置。")
        job_candidate_col, job_engine_col, job_dir_col = st.columns([1, 1, 1.4])
        with job_candidate_col:
            job_candidate_id = st.selectbox("绑定候选", candidate_ids)
        with job_engine_col:
            selected_job_engine = st.selectbox(
                "计算引擎",
                available_job_engines or ["未检测到可用引擎"],
                disabled=not engine_selection_enabled,
            )
            job_engine = selected_job_engine if engine_selection_enabled else "VASP"
        with job_dir_col:
            job_workdir = st.text_input("任务工作目录", value="work/external")
        dft_engines = {"VASP", "Quantum ESPRESSO", "CP2K"}
        if job_engine in dft_engines:
            calculation_kind = "dft"
            job_temperature_c = 25.0
            st.caption("计算类型：DFT 表面/吸附计算")
            facet_col, vacancy_col, hydroxyl_col = st.columns(3)
            with facet_col:
                job_facet = st.selectbox("CeO₂ 晶面", ["(111)", "(110)", "(100)"], key="job_facet")
            with vacancy_col:
                job_oxygen_vacancy = st.number_input("氧空位比例", min_value=0.0, max_value=0.30, value=0.08, step=0.01, key="job_vacancy")
            with hydroxyl_col:
                job_hydroxyl_fraction = st.number_input("羟基化比例", min_value=0.0, max_value=1.0, value=0.35, step=0.05, key="job_hydroxyl")
            reference_col, oxygen_reference_col = st.columns(2)
            with reference_col:
                surface_energy_text = st.text_input("裸表面能量 (eV，可选)", placeholder="用于计算吸附能")
            with oxygen_reference_col:
                oxygen_energy_text = st.text_input("吸附物参考能量 (eV，可选)", placeholder="需与裸表面能量同时填写")
            interface_area_nm2 = 100.0
        else:
            job_facet = None
            job_oxygen_vacancy = None
            job_hydroxyl_fraction = None
            md_kind_col, md_temperature_col = st.columns(2)
            with md_kind_col:
                calculation_kind_label = st.selectbox("计算类型", ["体相 MD 性能", "界面 MD 结合"])
            with md_temperature_col:
                job_temperature_c = st.number_input("计算温度 (°C)", min_value=-180.0, max_value=1500.0, value=25.0, step=1.0)
            calculation_kind = "bulk_md" if calculation_kind_label == "体相 MD 性能" else "interface_md"
            surface_energy_text = oxygen_energy_text = ""
            interface_area_nm2 = st.number_input("界面面积 (nm²)", min_value=0.001, value=100.0, step=1.0) if calculation_kind == "interface_md" else 100.0
        selected_job_profile = dict(standalone_profiles.get(calculation_kind, {}))
        if str(selected_job_profile.get("engine")) != job_engine:
            selected_job_profile = next(
                (
                    dict(profile)
                    for profile in standalone_profiles.values()
                    if str(profile.get("engine")) == job_engine
                ),
                {},
            )
        saved_job_command = str(selected_job_profile.get("command") or "")
        saved_job_result_file = str(
            selected_job_profile.get("result_file") or JOB_RESULT_FILE_EXAMPLES[job_engine]
        )
        standalone_profile_signature = (
            job_engine,
            calculation_kind,
            saved_job_command,
            saved_job_result_file,
        )
        if st.session_state.get("standalone_loaded_profile_signature") != standalone_profile_signature:
            st.session_state["standalone_job_command"] = saved_job_command
            st.session_state["standalone_job_result_file"] = saved_job_result_file
            st.session_state["standalone_loaded_profile_signature"] = standalone_profile_signature
        if saved_job_command:
            st.caption(f"已自动载入项目配置：{job_engine} · {calculation_kind}")
        job_command_text = st.text_input(
            "任务命令",
            placeholder=JOB_COMMAND_EXAMPLES[job_engine],
            key="standalone_job_command",
            help="命令按参数列表直接执行，不经过 shell；请不要使用管道、重定向或 &&。带空格的路径请用双引号包裹。",
        )
        job_result_file = st.text_input(
            "结果文件（相对于任务目录）",
            key="standalone_job_result_file",
            help="优先解析该文件；文件不存在时回退到标准输出。",
        )
        if st.button("提交外部计算任务", disabled=not engine_selection_enabled):
            if not job_command_text.strip():
                st.error("请输入任务命令。")
            else:
                try:
                    if bool(surface_energy_text.strip()) != bool(oxygen_energy_text.strip()):
                        raise ValueError("裸表面能量和吸附物参考能量必须同时填写或同时留空。")
                    selected_candidate_row = candidate_frame.loc[candidate_frame["candidate_id"].astype(str) == job_candidate_id].iloc[0]
                    job_metadata: dict[str, object] = {
                        "candidate_id": job_candidate_id,
                        "calculation_kind": calculation_kind,
                        "area_nm2": float(interface_area_nm2),
                        "filler_ratio": float(selected_candidate_row.get("filler_pct", 0.0)) / 100.0,
                        "polar_fraction": float(selected_candidate_row.get("resin_polarity", 0.5)),
                        "compatibility_index": float(selected_candidate_row.get("low_temp_toughness_index", 0.5)),
                        "temperature_c": float(job_temperature_c),
                        "temperature_unit": "K",
                        "energy_unit": "kcal/mol" if job_engine == "LAMMPS" else "kJ/mol",
                        "result_file": job_result_file.strip(),
                    }
                    if surface_energy_text.strip():
                        job_metadata["surface_energy_ev"] = float(surface_energy_text)
                        job_metadata["oxygen_energy_ev"] = float(oxygen_energy_text)
                    if calculation_kind == "dft":
                        job_metadata.update(
                            facet=job_facet,
                            oxygen_vacancy_fraction=float(job_oxygen_vacancy),
                            hydroxyl_fraction=float(job_hydroxyl_fraction),
                        )
                    submitted_job = submit_job(
                        job_engine,
                        split_job_command(job_command_text),
                        workdir=job_workdir,
                        metadata=job_metadata,
                    )
                    st.session_state["last_job_id"] = submitted_job.job_id
                    st.success(f"任务已提交：{submitted_job.job_id}，已绑定 {job_candidate_id}")
                except Exception as exc:
                    st.error(f"任务提交失败：{exc}")
    candidate_campaign_runs = list_campaign_runs(candidate_id=campaign_candidate_id)
    if candidate_campaign_runs:
        campaign_run_ids = [str(record["run_id"]) for record in candidate_campaign_runs]
        selected_campaign_run = st.session_state.get("selected_campaign_run")
        if selected_campaign_run not in campaign_run_ids:
            selected_campaign_run = campaign_run_ids[0]
        selected_campaign_run = st.selectbox(
            "查看自动计算记录",
            campaign_run_ids,
            index=campaign_run_ids.index(selected_campaign_run),
            format_func=lambda run_id: (
                f"{run_id} · "
                f"{CAMPAIGN_RUN_STATUS_LABELS.get(str(get_campaign_run(run_id).get('status')), '未知状态')}"
            ),
            key=f"campaign_run_selector_{campaign_candidate_id}",
        )
        st.session_state["selected_campaign_run"] = selected_campaign_run
        selected_run_record = get_campaign_run(selected_campaign_run)
        st.caption(f"任务包目录：{selected_run_record.get('package_directory', '—')}")
        _render_campaign_run_status(selected_campaign_run)
    else:
        st.info("当前候选还没有一键自动计算记录。")


_render_multiscale_campaign_section()

st.divider()
st.subheader("3. 外部计算任务与结果回写")
st.caption(
    "推荐直接使用上方“一键启动多尺度计算”。任务完成后，系统自动解析结果、按候选编号回写数据库、"
    "重训模型并更新；无需再点击单独的“回写”按钮。"
)
_watch_standalone_external_jobs()
standalone_job_snapshot = [
    item for item in list_jobs()
    if not (item.metadata or {}).get("campaign_run_id")
]
running_standalone = sum(item.status in {"queued", "running"} for item in standalone_job_snapshot)
pending_writeback = sum(
    item.status == "completed"
    and bool((item.metadata or {}).get("candidate_id"))
    and not bool((item.metadata or {}).get("integrated_at"))
    for item in standalone_job_snapshot
)
integrated_standalone = sum(bool((item.metadata or {}).get("integrated_at")) for item in standalone_job_snapshot)
failed_standalone = sum(item.status == "failed" for item in standalone_job_snapshot)
job_metric_columns = st.columns(4)
for column, label, value in zip(
    job_metric_columns,
    ("运行中", "待自动回写", "已自动回写", "失败"),
    (running_standalone, pending_writeback, integrated_standalone, failed_standalone),
):
    column.metric(label, value)
jobs = [
    item for item in list_jobs()
    if not (item.metadata or {}).get("campaign_run_id")
]
if jobs:
    running_count = sum(item.status in {"queued", "running"} for item in jobs)
    failed_count = sum(item.status == "failed" for item in jobs)
    st.caption(
        f"单任务记录 {len(jobs)} 条 · {running_count} 个运行中/等待中 · {failed_count} 个失败 · 状态自动刷新"
    )
else:
    st.caption("尚未提交单独的外部计算任务；多尺度任务状态请在上方查看。")
if jobs:
    job_table = pd.DataFrame([{
        "任务编号": item.job_id,
        "候选编号": (item.metadata or {}).get("candidate_id", "—"),
        "引擎": item.engine,
        "状态": JOB_STATUS_LABELS.get(item.status, item.status),
        "闭环状态": "已回写" if (item.metadata or {}).get("integrated_at") else ("待回写" if (item.metadata or {}).get("candidate_id") else "未绑定"),
        "耗时": _job_duration(item),
        "退出码": item.return_code if item.return_code is not None else "—",
        "提交时间": pd.Timestamp(item.submitted_at).tz_convert("Asia/Shanghai").strftime("%m-%d %H:%M:%S"),
        "工作目录": item.workdir,
    } for item in jobs[:20]])
    st.dataframe(job_table, width="stretch", hide_index=True)
    selected_job = st.session_state.get("last_job_id", jobs[0].job_id)
    jobs_by_id = {item.job_id: item for item in jobs}
    selected_job = st.selectbox(
        "查看任务输出",
        [item.job_id for item in jobs],
        index=next((i for i, item in enumerate(jobs) if item.job_id == selected_job), 0),
        format_func=lambda job_id: f"{jobs_by_id[job_id].engine} · {JOB_STATUS_LABELS.get(jobs_by_id[job_id].status, jobs_by_id[job_id].status)} · {job_id}",
    )
    selected_record = jobs_by_id[selected_job]
    output = read_job_output(selected_job, tail_chars=12000)
    st.caption(
        f"状态：{JOB_STATUS_LABELS.get(str(output['status']), str(output['status']))} · "
        f"耗时：{_job_duration(selected_record)} · 退出码：{output['return_code'] if output['return_code'] is not None else '—'}"
    )
    if output["status"] == "failed":
        st.error(f"任务失败：{output['error'] or '请查看标准错误日志。'}")
    if output["status"] == "completed":
        try:
            parsed_result = parse_job_result(selected_job)
            st.json({"解析结果": str(parsed_result)})
        except Exception as exc:
            st.warning(f"任务输出暂不能自动解析：{exc}")
    with st.expander("标准输出 / 错误输出", expanded=False):
        st.caption("为保持页面流畅，仅显示每份日志末尾 12,000 个字符。")
        st.code(str(output["stdout"]) or "（暂无标准输出）")
        if output["stderr"]:
            st.code(str(output["stderr"]), language="text")

st.divider()
st.subheader("4. AI 筛选与候选排序")
st.caption("融合已回写的多尺度计算结果和历史实验数据，预测五项输出并生成候选优先级。")
calculation_training_ready = external_candidate_count > 0
if not calculation_training_ready:
    st.warning(
        "尚未发现已自动回写的外部计算结果，正式 AI 训练与模型归档暂不可用。"
        "请先完成至少一个 VASP/LAMMPS 计算任务。"
    )
    previous_screening = st.session_state.get("latest_candidate_screening")
    previous_model = previous_screening.get("model") if isinstance(previous_screening, dict) else None
    previous_provenance = getattr(previous_model, "data_provenance", {}) if previous_model is not None else {}
    if int(previous_provenance.get("external_rows", 0)) <= 0:
        st.session_state.pop("latest_candidate_screening", None)
run_ai_screening = st.button(
    "运行 AI 筛选",
    type="primary",
    width="stretch",
    disabled=not calculation_training_ready,
    help=(
        "至少一个外部计算候选完成自动回写后才能训练和归档模型。"
        if not calculation_training_ready else None
    ),
)
if run_ai_screening:
    with st.spinner("正在生成候选数据库并训练回归/分类模型..."):
        try:
            shortlist, closed_loop = screen_candidates(
                candidate_frame,
                experiments=experiment_history if not experiment_history.empty else None,
                top_n=12,
                minimum_class="C",
                version="external-v1",
            )
        except ValueError as exc:
            st.error(
                _training_error_message(
                    exc,
                    external_rows=external_candidate_count,
                    experiment_rows=len(experiment_history),
                )
            )
            st.stop()
    try:
        artifact_path = save_model(closed_loop, Path("work/models") / f"{closed_loop.version}.npz")
        save_model_version(closed_loop, str(artifact_path))
        st.success(f"模型版本 {closed_loop.version} 已归档。")
    except Exception as exc:
        st.warning(f"筛选已完成，但模型归档失败：{exc}")
    st.session_state["latest_candidate_screening"] = {
        "signature": candidate_signature,
        "shortlist": shortlist,
        "model": closed_loop,
    }

latest_screening = st.session_state.get("latest_candidate_screening")
if latest_screening and latest_screening.get("signature") == candidate_signature:
    shortlist = latest_screening["shortlist"]
    closed_loop = latest_screening["model"]
    feedback_note = f"，融合 {closed_loop.experimental_rows} 个实验覆盖候选" if closed_loop.experimental_rows else ""
    st.success(f"模型 {closed_loop.version} 已完成 {closed_loop.training_rows} 条候选训练{feedback_note}，返回 {len(shortlist)} 个优先配方。")
    provenance = closed_loop.data_provenance
    st.caption(
        f"训练来源：代理候选 {provenance.get('proxy_rows', 0)} · 外部计算候选 {provenance.get('external_rows', 0)} · "
        f"实验覆盖候选 {provenance.get('experimental_rows', 0)}"
    )
    display_columns = [
        "candidate_id", "resin", "resin_variant", "dynamic_unit", "filler_pct", "crosslink_density", "simulation_source",
        "predicted_wide_temp_adhesion_mpa", "predicted_healing_efficiency_pct",
        "predicted_atomic_oxygen_retention_pct", "predicted_uv_retention_pct",
        "predicted_am_feasibility", "predicted_multi_objective_score", "predicted_screening_class",
    ]
    candidate_display = shortlist.reindex(columns=display_columns).copy()
    for column, labels in CANDIDATE_VALUE_LABELS.items():
        if column in candidate_display:
            candidate_display[column] = candidate_display[column].map(labels).fillna(candidate_display[column])
    candidate_display = candidate_display.rename(columns=CANDIDATE_COLUMN_LABELS)
    st.dataframe(candidate_display, width="stretch", hide_index=True)
    if closed_loop.experimental_rows:
        tested_ids = experiment_history["candidate_id"].astype(str).tolist() if "candidate_id" in experiment_history else []
        next_batch = recommend_next_experiments(closed_loop, candidate_frame, tested_ids=tested_ids, batch_size=8)
        st.markdown("#### 下一轮实验推荐")
        next_batch_display = next_batch[["candidate_id", "acquisition_score", "predicted_multi_objective_score", "prediction_uncertainty"]].rename(columns=CANDIDATE_COLUMN_LABELS)
        st.dataframe(next_batch_display, width="stretch", hide_index=True)

st.markdown("### 候选机理与真实数据融合")
st.caption("按候选编号直接融合实验、外部 DFT/MD/界面结果和代理数据；优先级为实验 > 外部计算 > 代理。")
mechanism_candidate_id = st.selectbox(
    "探索候选编号",
    candidate_ids,
    key="mechanism_candidate_id",
    help="真实计算和实验数据通过候选编号自动关联。",
)
mechanism_candidate = candidate_frame.loc[
    candidate_frame["candidate_id"].astype(str) == mechanism_candidate_id
].iloc[0]
mechanism_payload = external_payloads.get(mechanism_candidate_id, {})
recorded_dft = mechanism_payload.get("dft", {}) if isinstance(mechanism_payload, dict) else {}
condition_col1, condition_col2, condition_col3, condition_col4 = st.columns(4)
with condition_col1:
    mechanism_facet = st.selectbox(
        "代理补全晶面",
        ["(111)", "(110)", "(100)"],
        index=["(111)", "(110)", "(100)"].index(recorded_dft.get("facet")) if recorded_dft.get("facet") in {"(111)", "(110)", "(100)"} else 0,
        key="mechanism_facet",
        help="已有真实 DFT 时采用任务记录的晶面；否则用于补全缺失指标。",
    )
with condition_col2:
    mechanism_vacancy = st.number_input(
        "代理补全氧空位",
        min_value=0.0,
        max_value=0.30,
        value=float(recorded_dft.get("oxygen_vacancy_fraction") if recorded_dft.get("oxygen_vacancy_fraction") is not None else 0.08),
        step=0.01,
        key="mechanism_vacancy",
    )
with condition_col3:
    mechanism_hydroxyl = st.number_input(
        "代理补全羟基化",
        min_value=0.0,
        max_value=1.0,
        value=float(recorded_dft.get("hydroxyl_fraction") if recorded_dft.get("hydroxyl_fraction") is not None else 0.35),
        step=0.05,
        key="mechanism_hydroxyl",
    )
with condition_col4:
    mechanism_particle_size = st.number_input(
        "CeO₂ 粒径 (nm)",
        min_value=8.0,
        max_value=200.0,
        value=35.0,
        step=1.0,
        key="mechanism_particle_size",
    )

mechanism_result = fuse_candidate_mechanism(
    mechanism_candidate,
    calculations=mechanism_payload,
    experiments=experiment_history,
    facet=mechanism_facet,
    oxygen_vacancy_fraction=mechanism_vacancy,
    hydroxyl_fraction=mechanism_hydroxyl,
    particle_size_nm=mechanism_particle_size,
)
readiness = mechanism_result["readiness"]
readiness_cols = st.columns(4)
for column, label, key in zip(
    readiness_cols,
    ("真实 DFT", "真实树脂 MD", "真实界面 MD", "实验数据"),
    ("dft", "md", "interface", "experiment"),
):
    column.metric(label, "已融合" if readiness[key] else "待接入")
if not any(readiness.values()):
    st.info("该候选尚无真实计算或实验记录，当前所有指标均为明确标记的代理结果。")
elif not all(readiness.values()):
    missing_sources = [label for label, key in zip(
        ("DFT", "树脂 MD", "界面 MD", "实验"), ("dft", "md", "interface", "experiment")
    )
        if not readiness[key]
    ]
    st.info(f"当前为混合数据结果；尚缺：{'、'.join(missing_sources)}。缺失字段继续使用代理值。")
else:
    st.success("该候选已融合 DFT、树脂 MD、界面 MD 和实验数据。")

summary = mechanism_result["source_summary"]
summary_cols = st.columns(5)
for column, label, key in zip(
    summary_cols,
    ("实验指标", "真实计算指标", "混合曲线", "模型预测", "代理指标"),
    ("experimental", "external", "hybrid", "model", "proxy"),
):
    column.metric(label, int(summary[key]))

source_tab, quantum_tab, md_tab, interface_tab, performance_tab = st.tabs([
    "组成与数据来源", "量子化学", "树脂分子动力学", "界面与粗粒化", "性能与实验",
])
with source_tab:
    composition_fields = (
        "candidate_id", "resin", "resin_variant", "blend_resin", "blend_fraction",
        "dynamic_unit", "cure_system", "curing_agent", "catalyst", "toughener_type",
        "toughener_pct", "filler_type", "filler_pct", "crosslink_density",
    )
    composition_table = pd.DataFrame([
        {
            "字段": CANDIDATE_COLUMN_LABELS.get(name, name),
            "值": str(_localized_value(name, mechanism_result["candidate"].get(name))) if mechanism_result["candidate"].get(name) is not None else "—",
        }
        for name in composition_fields
    ])
    st.dataframe(composition_table, width="stretch", hide_index=True)
    provenance_table = mechanism_provenance_frame(mechanism_result)
    provenance_display = provenance_table.copy()
    provenance_display["section"] = provenance_display["section"].map(MECHANISM_SECTION_LABELS).fillna(provenance_display["section"])
    provenance_display["metric"] = provenance_display["metric"].map(MECHANISM_METRIC_LABELS).fillna(provenance_display["metric"])
    provenance_display["value"] = provenance_display["value"].astype(str).str.replace(r"^(\d+)-point profile$", r"\1 点曲线", regex=True)
    provenance_display["source"] = provenance_display["source"].map(_source_label)
    provenance_display = provenance_display.rename(columns={"section": "数据类别", "metric": "指标", "value": "数值", "source": "数据来源"})
    st.dataframe(provenance_display, width="stretch", hide_index=True)
    st.download_button(
        "下载逐指标来源 CSV",
        provenance_table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{mechanism_candidate_id}-mechanism-provenance.csv",
        mime="text/csv",
    )

with quantum_tab:
    quantum_labels = {
        "oxygen_adsorption_ev": ("原子氧吸附能", "eV"),
        "oxygen_reaction_barrier_ev": ("反应路径能垒", "eV"),
        "oxygen_reaction_energy_ev": ("反应能", "eV"),
        "ce3_fraction": ("Ce³⁺ 分数", "fraction"),
        "reactive_oxygen_capture_index": ("活性氧捕获能力", "index"),
        "pda_resin_hbond_ev": ("PDA-树脂氢键结合能", "eV"),
        "pda_surface_coordination_ev": ("PDA-表面配位能", "eV"),
        "pda_reaction_ev": ("PDA 化学反应能", "eV"),
    }
    quantum_table = pd.DataFrame([
        {
            "指标": label,
            "数值": mechanism_result["quantum"].get(name),
            "单位": unit,
            "来源": _source_label(mechanism_result["provenance"].get(f"quantum.{name}")),
        }
        for name, (label, unit) in quantum_labels.items()
    ])
    st.dataframe(quantum_table, width="stretch", hide_index=True)
    st.caption(f"采用晶面 {mechanism_result['conditions']['facet']}；真实输出只覆盖实际解析成功的字段。")

with md_tab:
    md_result = mechanism_result["md"]
    st.metric(
        "玻璃化转变温度 Tg",
        f"{float(md_result['glass_transition_c']):.1f} °C",
        help=str(_source_label(mechanism_result["provenance"].get("md.glass_transition_c"))),
    )
    md_table = pd.DataFrame({
        "温度 (°C)": md_result["temperatures_c"],
        "自由体积分数": md_result["free_volume_fraction"],
        "内聚能密度 (MJ/m³)": md_result["cohesive_energy_density_mj_m3"],
        "弹性模量 (GPa)": md_result["elastic_modulus_gpa"],
        "热膨胀系数 (ppm/K)": md_result["cte_ppm_k"],
        "黏附保持率": md_result["adhesion_retention"],
        "自修复效率": md_result["self_healing_efficiency"],
    })
    st.dataframe(md_table, width="stretch", hide_index=True)
    st.caption(
        "标量真实 MD/实验值用于锚定 25°C，温度变化形状仍来自代理模型；"
        "只有完整温度数组才标记为纯真实计算曲线。"
    )
    md_fig = go.Figure()
    md_fig.add_scatter(x=md_result["temperatures_c"], y=md_result["adhesion_retention"], name="黏附保持率")
    md_fig.add_scatter(x=md_result["temperatures_c"], y=md_result["self_healing_efficiency"], name="自修复效率")
    md_fig.update_layout(height=320, xaxis_title="温度 (°C)", yaxis_title="归一化指标", margin={"l": 10, "r": 10, "t": 15, "b": 10})
    st.plotly_chart(md_fig, width="stretch", config=PLOTLY_CONFIG)

with interface_tab:
    interface_labels = {
        "binding_energy_mj_m2": ("界面结合能", "mJ/m²"),
        "adhesion_work_mj_m2": ("黏附功", "mJ/m²"),
        "hydrogen_bond_density_nm2": ("氢键密度", "nm⁻²"),
        "coordination_bond_density_nm2": ("配位键密度", "nm⁻²"),
        "covalent_reaction_fraction": ("共价反应分数", "fraction"),
        "dispersion_index": ("粗粒化分散指数", "index"),
        "coarse_grained_reinforcement_index": ("填料增强指数", "index"),
        "stability_score": ("界面稳定性", "index"),
    }
    interface_table = pd.DataFrame([
        {
            "指标": label,
            "数值": mechanism_result["interface"].get(name),
            "单位": unit,
            "来源": _source_label(mechanism_result["provenance"].get(f"interface.{name}")),
        }
        for name, (label, unit) in interface_labels.items()
        if mechanism_result["interface"].get(name) is not None
    ])
    st.dataframe(interface_table, width="stretch", hide_index=True)
    trajectory = mechanism_result["trajectory"]
    if trajectory["available"]:
        trajectory_fig = go.Figure(go.Scatter(
            x=trajectory["steps"], y=trajectory["energy"], name="势能",
            line={"color": "#0f766e", "width": 2},
        ))
        trajectory_fig.update_layout(height=320, xaxis_title="模拟步", yaxis_title="势能", margin={"l": 10, "r": 10, "t": 15, "b": 10})
        st.plotly_chart(trajectory_fig, width="stretch", config=PLOTLY_CONFIG)
        st.caption(f"轨迹来源：{trajectory['source']}")
    else:
        st.info("已有旧版界面计算时可融合汇总值；新提交的界面任务会额外保存真实能量轨迹。")

with performance_tab:
    performance_table = pd.DataFrame([
        {
            "输出指标": EXPERIMENT_COLUMN_LABELS.get(name, name),
            "数值": mechanism_result["performance"].get(name),
            "来源": _source_label(mechanism_result["provenance"].get(f"performance.{name}")),
        }
        for name in OUTPUT_COLUMNS
    ])
    st.dataframe(performance_table, width="stretch", hide_index=True)
    experiment_records = pd.DataFrame(mechanism_result["experiment_records"])
    if experiment_records.empty:
        st.info("该候选暂无实验记录。")
    else:
        st.markdown("#### 已融合实验记录")
        experiment_display = experiment_records.rename(columns={**EXPERIMENT_COLUMN_LABELS, **EXPERIMENT_METADATA_LABELS})
        st.dataframe(experiment_display, width="stretch", hide_index=True)


st.divider()
st.subheader("5. 实验验证、模型更新与再筛选")
st.caption("可填写单条记录或批量导入 CSV；保存后，全部历史实验会参与模型更新和下一轮筛选。")
experiment_upload = st.file_uploader("批量实验数据 CSV", type=["csv"], help="必须包含候选编号和五项输出指标；支持中文或英文列名。", key="closed_loop_csv")
uploaded_experiments, upload_errors, upload_warnings = _read_experiment_csv(experiment_upload)
if uploaded_experiments is not None:
    unmatched_ids = sorted(set(uploaded_experiments.get("candidate_id", pd.Series(dtype=str)).astype(str)) - set(candidate_ids))
    if unmatched_ids:
        upload_errors.append(f"以下候选编号不在当前候选库：{', '.join(unmatched_ids[:10])}")
    for warning in upload_warnings:
        st.warning(warning)
    for error in upload_errors:
        st.error(error)
    if not upload_errors:
        upload_display = uploaded_experiments.head(20).rename(columns={**EXPERIMENT_COLUMN_LABELS, **EXPERIMENT_METADATA_LABELS})
        st.dataframe(upload_display, width="stretch", hide_index=True)
import_csv_feedback = st.button(
    "导入 CSV 并重新训练" if calculation_training_ready else "导入 CSV（计算完成后训练）",
    disabled=uploaded_experiments is None or bool(upload_errors),
    width="stretch",
    key="closed_loop_csv_submit",
)
include_material_data = st.checkbox("填写可选热力学与力学数据", value=False, key="closed_loop_optional")
optional_feedback: dict[str, float] = {}
with st.form("closed_loop_experiment_form", clear_on_submit=False):
    feedback_candidate_id = st.selectbox("候选编号", candidate_ids, help="候选编号来自当前候选库。", key="feedback_candidate")
    batch_col, test_temp_col = st.columns(2)
    with batch_col:
        feedback_batch = st.text_input("实验批次", value="manual-01", help="用于区分重复实验和不同批次。", key="feedback_batch")
    with test_temp_col:
        feedback_test_temperature = st.number_input("测试温度 (°C)", min_value=-180.0, max_value=150.0, value=25.0, step=1.0, key="feedback_temperature")
    adhesion_col, healing_col = st.columns(2)
    with adhesion_col:
        feedback_adhesion = st.number_input("宽温域黏附强度 (MPa)", min_value=0.0, max_value=200.0, value=25.0, step=0.1, key="feedback_adhesion")
    with healing_col:
        feedback_healing = st.number_input("自修复效率 (%)", min_value=0.0, max_value=100.0, value=70.0, step=0.5, key="feedback_healing")
    oxygen_col, uv_col, am_col = st.columns(3)
    with oxygen_col:
        feedback_oxygen = st.number_input("抗原子氧保持率 (%)", min_value=0.0, max_value=100.0, value=85.0, step=0.5, key="feedback_oxygen")
    with uv_col:
        feedback_uv = st.number_input("紫外保持率 (%)", min_value=0.0, max_value=100.0, value=85.0, step=0.5, key="feedback_uv")
    with am_col:
        feedback_am = st.number_input("增材制造可行性 (%)", min_value=0.0, max_value=100.0, value=70.0, step=0.5, key="feedback_am")
    if include_material_data:
        with st.expander("热性能与力学性能", expanded=True):
            tg_col, fv_col, mobility_col = st.columns(3)
            with tg_col:
                optional_feedback["measured_tg_c"] = st.number_input("玻璃化转变温度 (°C)", value=180.0, step=1.0, key="feedback_tg")
            with fv_col:
                optional_feedback["measured_free_volume"] = st.number_input("自由体积分数", min_value=0.0, max_value=1.0, value=0.08, step=0.005, format="%.3f", key="feedback_free_volume")
            with mobility_col:
                optional_feedback["measured_chain_mobility"] = st.number_input("链段运动能力", min_value=0.0, max_value=1.0, value=0.25, step=0.01, format="%.3f", key="feedback_mobility")
            ced_col, modulus_col, cte_col = st.columns(3)
            with ced_col:
                optional_feedback["measured_cohesive_energy_density"] = st.number_input("内聚能密度 (MJ/m³)", min_value=0.0, value=350.0, step=1.0, key="feedback_ced")
            with modulus_col:
                optional_feedback["measured_modulus_gpa"] = st.number_input("弹性模量 (GPa)", min_value=0.0, value=2.5, step=0.1, key="feedback_modulus")
            with cte_col:
                optional_feedback["measured_cte_ppm_k"] = st.number_input("热膨胀系数 (ppm/K)", min_value=0.0, value=55.0, step=1.0, key="feedback_cte")
    submit_feedback = st.form_submit_button(
        "回写实验数据并重新筛选" if calculation_training_ready else "保存实验数据（计算完成后训练）",
        type="primary",
        width="stretch",
    )

experiment_frame = pd.DataFrame([{
    "candidate_id": feedback_candidate_id,
    "wide_temp_adhesion_mpa": feedback_adhesion,
    "healing_efficiency_pct": feedback_healing,
    "atomic_oxygen_retention_pct": feedback_oxygen,
    "uv_retention_pct": feedback_uv,
    "am_feasibility": feedback_am,
    "test_batch": feedback_batch,
    "test_temperature_c": feedback_test_temperature,
    **optional_feedback,
}])
if submit_feedback or import_csv_feedback:
    current_feedback = experiment_frame if submit_feedback else uploaded_experiments
    if submit_feedback:
        try:
            properties = {key: value for key, value in experiment_frame.iloc[0].to_dict().items() if key not in {"candidate_id", "test_batch", "test_temperature_c"}}
            save_experiment(feedback_candidate_id, properties, test_batch=feedback_batch, temperature_c=feedback_test_temperature, source="streamlit-form")
            experiment_history = load_experiments(candidate_ids)
        except Exception as exc:
            st.warning(f"实验数据持久化失败，本次仍会用于内存训练：{exc}")
            experiment_history = pd.concat([experiment_history, current_feedback], ignore_index=True)
    elif uploaded_experiments is not None:
        try:
            imported_rows = save_experiments(uploaded_experiments, default_source="csv-upload")
            experiment_history = load_experiments(candidate_ids)
            st.session_state["feedback_notice"] = f"已导入 {imported_rows} 条实验记录并更新模型。"
        except Exception as exc:
            st.warning(f"批量实验持久化失败，本批数据仍会用于内存训练：{exc}")
            experiment_history = pd.concat([experiment_history, uploaded_experiments], ignore_index=True)
    if not calculation_training_ready:
        st.session_state["feedback_notice"] = (
            "实验数据已保存；当前尚无已回写的外部计算结果，因此未训练或归档模型。"
            "请先完成计算，回写后再运行 AI 筛选。"
        )
        st.rerun()
    with st.spinner("正在融合实验数据并重新训练..."):
        try:
            shortlist, closed_loop = screen_candidates(
                candidate_frame,
                experiments=experiment_history if not experiment_history.empty else None,
                top_n=12,
                minimum_class="C",
                version="external-v1",
            )
        except ValueError as exc:
            st.error(
                _training_error_message(
                    exc,
                    external_rows=external_candidate_count,
                    experiment_rows=len(experiment_history),
                )
            )
            st.stop()
    try:
        artifact_path = save_model(closed_loop, Path("work/models") / f"{closed_loop.version}.npz")
        save_model_version(closed_loop, str(artifact_path))
    except Exception as exc:
        st.warning(f"模型已更新，但归档失败：{exc}")
    st.session_state["latest_candidate_screening"] = {
        "signature": candidate_signature,
        "shortlist": shortlist,
        "model": closed_loop,
    }
    if submit_feedback:
        st.session_state["feedback_notice"] = f"候选 {feedback_candidate_id} 的实验数据已回写，模型已更新。"
    st.rerun()
