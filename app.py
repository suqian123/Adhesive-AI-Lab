from __future__ import annotations

import sys
import shlex
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))
from adhesive_ai.features import (
    FORMULATION_FEATURE_LABELS,
    MOLECULE_FEATURE_LABELS,
    MOLECULE_KIND_LABELS,
)
from adhesive_ai.pipeline import run_screening
from adhesive_ai.candidate_library import build_candidate_library
from adhesive_ai.screening import OUTPUT_COLUMNS, predict_screening, recommend_next_experiments, save_model, screen_candidates, train_screening_models, update_with_experiments
from adhesive_ai.database import save_experiment, save_model_version
from adhesive_ai.jobs import get_job_status, list_jobs, parse_job_result, read_job_output, submit_job

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
    "predicted_wide_temp_adhesion_mpa": "预测宽温域黏附强度 (MPa)",
    "predicted_healing_efficiency_pct": "预测自修复效率 (%)",
    "predicted_atomic_oxygen_retention_pct": "预测抗原子氧保持率 (%)",
    "predicted_uv_retention_pct": "预测紫外保持率 (%)",
    "predicted_am_feasibility": "预测增材制造可行性 (%)",
    "predicted_multi_objective_score": "预测多目标综合评分",
    "predicted_screening_class": "预测筛选等级",
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
}

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
                document.querySelectorAll(`[aria-label="${source}"], [title="${source}"], [data-title="${source}"]`).forEach((node) => {
                    if (node.getAttribute("aria-label") === source) node.setAttribute("aria-label", target);
                    if (node.getAttribute("title") === source) node.setAttribute("title", target);
                    if (node.getAttribute("data-title") === source) node.setAttribute("data-title", target);
                });
            }
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                if (["SCRIPT", "STYLE", "NOSCRIPT"].includes(node.parentElement?.tagName)) continue;
                const source = node.nodeValue.trim();
                if (labels[source]) node.nodeValue = node.nodeValue.replace(source, labels[source]);
            }
        };
        translate();
        new MutationObserver(translate).observe(document.body, {
            subtree: true,
            childList: true,
            attributes: true,
            characterData: true,
            attributeFilter: ["aria-label", "title", "data-title"],
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

with st.sidebar:
    st.markdown("### 配方输入")
    resin = st.text_input("树脂 / 聚合物 SMILES", "CC(C)C(=O)O")
    tackifier = st.text_input("增粘剂 SMILES", "c1ccccc1O")
    filler = st.text_input("填料 / 助剂 SMILES", "O=[Si](O)O")
    st.markdown("#### 质量配比")
    resin_ratio = st.number_input("树脂", 0.0, 100.0, 65.0, 1.0)
    tackifier_ratio = st.number_input("增粘剂", 0.0, 100.0, 25.0, 1.0)
    filler_ratio = st.number_input("填料", 0.0, 100.0, 10.0, 1.0)
    st.markdown("#### 环境与模拟")
    temperature = st.slider("温度 (°C)", -180, 150, 25)
    humidity = st.slider("相对湿度 (%)", 0, 100, 45)
    steps = st.slider("模拟步数", 250, 1400, 650, 50)
    st.markdown("#### PDA@CeO₂ 多尺度参数")
    facet = st.selectbox("CeO₂ 晶面", ["(111)", "(110)", "(100)"])
    oxygen_vacancy = st.slider("氧空位比例", 0.0, 0.30, 0.08, 0.01)
    hydroxyl_fraction = st.slider("表面羟基化比例", 0.0, 1.0, 0.35, 0.05)
    crosslink_density = st.slider("树脂交联密度", 0.15, 1.0, 0.65, 0.05)
    dynamic_healing = st.slider("动态键修复活性", 0.0, 1.0, 0.55, 0.05)
    dynamic_mobility = st.slider("链段运动因子", 0.0, 1.0, 0.25, 0.05)
    particle_size = st.number_input("CeO₂ 粒径 (nm)", 8.0, 200.0, 35.0, 1.0)
    run = st.button("运行 AI + 模拟", type="primary", width="stretch")

if "result" not in st.session_state:
    run = True
if run:
    if resin_ratio + tackifier_ratio + filler_ratio <= 0:
        st.error("请至少输入一种非零配方比例。")
        st.stop()
    with st.spinner("正在提取分子特征、训练代理模型并运行界面吸附模拟..."):
        st.session_state.result = run_screening(
            resin_smiles=resin, tackifier_smiles=tackifier, filler_smiles=filler,
            resin_ratio=resin_ratio, tackifier_ratio=tackifier_ratio, filler_ratio=filler_ratio,
            temperature_c=temperature, humidity_pct=humidity, simulation_steps=steps,
            facet=facet, oxygen_vacancy_fraction=oxygen_vacancy, hydroxyl_fraction=hydroxyl_fraction,
            crosslink_density=crosslink_density, dynamic_healing=dynamic_healing,
            dynamic_mobility=dynamic_mobility, particle_size_nm=particle_size,
        )

result = st.session_state.result
combined, simulation = result["combined"], result["simulation"]
simulation_is_external = simulation.status == "parsed"
calculation_label = "真实 LAMMPS/GROMACS 结果" if simulation_is_external else "代理预测（未接入已完成外部 MD 输出）"
st.info(f"当前结果来源：{calculation_label}；量子化学、树脂 MD 和界面 CG 默认仍为前筛代理模型。")
metrics = [
    ("综合粘附功", f'{combined["adhesion_work_mj_m2"]:.1f}', "mJ/m²"),
    ("界面结合能", f'{combined["interface_energy_mj_m2"]:.1f}', "mJ/m²"),
    ("密度估计", f'{combined["density_g_cm3"]:.3f}', "g/cm³"),
    ("表面覆盖率", f'{combined["surface_coverage"]*100:.1f}', "%"),
    ("稳定性", f'{combined["stability_score"]*100:.1f}', "%"),
]
cols = st.columns(5)
for col, (label, value, unit) in zip(cols, metrics):
    with col:
        st.markdown(f'<div class="metric"><div class="metric-label">{label}</div><div class="metric-value">{value}</div><div class="metric-note">{unit}</div></div>', unsafe_allow_html=True)

left, right = st.columns([1.3, 1])
with left:
    st.subheader("界面吸附轨迹")
    fig = go.Figure(go.Scatter(x=simulation.steps, y=simulation.energy, name="势能", line={"color": "#0f766e", "width": 2}))
    fig.update_layout(height=330, margin={"l":10,"r":10,"t":15,"b":10}, xaxis_title="模拟步", yaxis_title="相对势能")
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)
with right:
    st.subheader("模型关注的特征")
    importance = result["importance"].copy()
    importance["feature"] = importance["feature"].map(FORMULATION_FEATURE_LABELS).fillna(importance["feature"])
    fig = go.Figure(go.Bar(x=importance["importance"][::-1], y=importance["feature"][::-1], orientation="h", marker_color="#d97706"))
    fig.update_layout(height=330, margin={"l":10,"r":10,"t":15,"b":10}, xaxis_title="相对重要性", yaxis_title="")
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)

tab1, tab2, tab3, tab4 = st.tabs(["分子特征", "量子化学", "树脂分子动力学", "界面与说明"])
with tab1:
    molecule_table = pd.DataFrame.from_dict(result["molecules"], orient="index")
    molecule_table.index = molecule_table.index.map(MOLECULE_KIND_LABELS)
    molecule_table = molecule_table.rename(columns=MOLECULE_FEATURE_LABELS)
    molecule_table.index.name = "组分"
    st.dataframe(molecule_table, width="stretch")
with tab2:
    q = result["quantum"]
    qdata = pd.DataFrame({"指标": ["原子氧吸附能", "反应路径能垒", "反应能", "Ce³⁺ 分数", "活性氧捕获", "PDA-树脂氢键", "PDA-表面配位键", "PDA 反应结合能"], "数值": [q.oxygen_adsorption_ev, q.oxygen_reaction_barrier_ev, q.oxygen_reaction_energy_ev, q.ce3_fraction, q.reactive_oxygen_capture_index, q.pda_resin_hbond_ev, q.pda_surface_coordination_ev, q.pda_reaction_ev], "单位": ["eV", "eV", "eV", "fraction", "index", "eV", "eV", "eV"]})
    st.dataframe(qdata, width="stretch", hide_index=True)
    st.caption(f"代理预测；晶面 {facet}。仅当外部 DFT 任务完成并回写后，才可作为真实计算结果使用。负值表示放热结合或反应。")
with tab3:
    md = result["md"]
    st.metric("代理预测 Tg", f"{md.glass_transition_c:.1f} °C")
    mdtable = pd.DataFrame({"温度 (°C)": md.temperatures_c, "自由体积分数": md.free_volume_fraction, "内聚能密度": md.cohesive_energy_density_mj_m3, "弹性模量 (GPa)": md.elastic_modulus_gpa, "热膨胀系数 (ppm/K)": md.cte_ppm_k, "黏附保持率": md.adhesion_retention, "自修复效率": md.self_healing_efficiency})
    st.dataframe(mdtable, width="stretch", hide_index=True)
    fig = go.Figure()
    fig.add_scatter(x=md.temperatures_c, y=md.adhesion_retention, name="黏附保持率")
    fig.add_scatter(x=md.temperatures_c, y=md.self_healing_efficiency, name="自修复效率")
    fig.update_layout(height=300, xaxis_title="温度 (°C)", yaxis_title="归一化指标", margin={"l": 10, "r": 10, "t": 15, "b": 10})
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)
with tab4:
    points = simulation.final_positions
    fig = go.Figure(go.Scatter3d(
        x=points[:, 0], y=points[:, 1], z=points[:, 2], mode="markers",
        marker={"size": 4, "color": points[:, 2], "colorscale": "Teal"},
    ))
    fig.update_layout(height=480, margin={"l":0,"r":0,"t":0,"b":0}, scene={"xaxis_title":"X","yaxis_title":"Y","zaxis_title":"距基底高度"})
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)
    i = result["interface"]
    st.metric("代理预测树脂/PDA@CeO₂ 界面结合能", f"{i.binding_energy_mj_m2:.1f} mJ/m²")
    st.write({"氢键密度 (nm⁻²)": i.hydrogen_bond_density_nm2, "配位键密度 (nm⁻²)": i.coordination_bond_density_nm2, "化学反应分数": i.covalent_reaction_fraction, "粗粒化分散指数": i.dispersion_index, "填料增强指数": i.coarse_grained_reinforcement_index})
    st.info("结果由透明的物理启发代理模型、粗粒化界面吸附和配方 AI 共同组成，适合筛选与实验优先级排序；发表或工程定标仍需接入真实 DFT、LAMMPS/GROMACS 与实验数据。")
    st.json({"compatibility_index": combined["compatibility_index"], "temperature_c": temperature, "humidity_pct": humidity, "simulation_steps": steps})

st.divider()
st.subheader("高温树脂 / PDA@CeO₂ 候选配方筛选")
st.caption("候选库以树脂基体、仿生填料和界面特征为输入，回归五项性能并按多目标分数排序。")
candidate_count = st.slider("候选库规模", 24, 360, 72, 24)
candidate_frame = build_candidate_library(max_records=candidate_count, seed=11)
candidate_ids = candidate_frame["candidate_id"].astype(str).tolist()
st.markdown("#### 实验数据回写")
st.caption("直接填写一条候选配方的实测结果，提交后立即用于模型更新和下一轮筛选。")
include_material_data = st.checkbox("填写可选热力学与力学数据", value=False)
optional_feedback: dict[str, float] = {}
with st.form("experiment_feedback_form", clear_on_submit=False):
    feedback_candidate_id = st.selectbox("候选编号", candidate_ids, help="候选编号来自当前候选库。")
    batch_col, test_temp_col = st.columns(2)
    with batch_col:
        feedback_batch = st.text_input("实验批次", value="manual-01", help="用于区分重复实验和不同批次。")
    with test_temp_col:
        feedback_test_temperature = st.number_input("测试温度 (°C)", min_value=-180.0, max_value=150.0, value=25.0, step=1.0)
    adhesion_col, healing_col = st.columns(2)
    with adhesion_col:
        feedback_adhesion = st.number_input("宽温域黏附强度 (MPa)", min_value=0.0, max_value=200.0, value=25.0, step=0.1)
    with healing_col:
        feedback_healing = st.number_input("自修复效率 (%)", min_value=0.0, max_value=100.0, value=70.0, step=0.5)
    oxygen_col, uv_col, am_col = st.columns(3)
    with oxygen_col:
        feedback_oxygen = st.number_input("抗原子氧保持率 (%)", min_value=0.0, max_value=100.0, value=85.0, step=0.5)
    with uv_col:
        feedback_uv = st.number_input("紫外保持率 (%)", min_value=0.0, max_value=100.0, value=85.0, step=0.5)
    with am_col:
        feedback_am = st.number_input("增材制造可行性 (%)", min_value=0.0, max_value=100.0, value=70.0, step=0.5)
    if include_material_data:
        with st.expander("热性能与力学性能", expanded=True):
            tg_col, fv_col, mobility_col = st.columns(3)
            with tg_col:
                optional_feedback["measured_tg_c"] = st.number_input("玻璃化转变温度 (°C)", value=180.0, step=1.0)
            with fv_col:
                optional_feedback["measured_free_volume"] = st.number_input("自由体积分数", min_value=0.0, max_value=1.0, value=0.08, step=0.005, format="%.3f")
            with mobility_col:
                optional_feedback["measured_chain_mobility"] = st.number_input("链段运动能力", min_value=0.0, max_value=1.0, value=0.25, step=0.01, format="%.3f")
            ced_col, modulus_col, cte_col = st.columns(3)
            with ced_col:
                optional_feedback["measured_cohesive_energy_density"] = st.number_input("内聚能密度 (MJ/m³)", min_value=0.0, value=350.0, step=1.0)
            with modulus_col:
                optional_feedback["measured_modulus_gpa"] = st.number_input("弹性模量 (GPa)", min_value=0.0, value=2.5, step=0.1)
            with cte_col:
                optional_feedback["measured_cte_ppm_k"] = st.number_input("热膨胀系数 (ppm/K)", min_value=0.0, value=55.0, step=1.0)
    submit_feedback = st.form_submit_button("回写实验数据并重新筛选", type="primary", width="stretch")

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
run_screening_button = st.button("生成并筛选候选配方")
if submit_feedback or run_screening_button:
    with st.spinner("正在生成候选数据库并训练回归/分类模型..."):
        try:
            experiments = experiment_frame if submit_feedback else None
            shortlist, closed_loop = screen_candidates(candidate_frame, experiments=experiments, top_n=12, minimum_class="C")
        except ValueError as exc:
            st.error(f"实验数据无法回灌：{exc}")
            st.stop()
    if submit_feedback:
        try:
            properties = {key: value for key, value in experiment_frame.iloc[0].to_dict().items() if key not in {"candidate_id", "test_batch", "test_temperature_c"}}
            save_experiment(feedback_candidate_id, properties, test_batch=feedback_batch, temperature_c=feedback_test_temperature, source="streamlit-form")
            artifact_path = save_model(closed_loop, Path("work/models") / f"{closed_loop.version}.npz")
            save_model_version(closed_loop, str(artifact_path))
            st.success(f"实验数据已保存，模型版本 {closed_loop.version} 已归档。")
        except Exception as exc:
            st.warning(f"模型已完成本次内存更新，但持久化失败：{exc}")
    feedback_note = f"，融合 {closed_loop.experimental_rows} 条实验记录" if closed_loop.experimental_rows else ""
    st.success(f"模型 {closed_loop.version} 已完成 {closed_loop.training_rows} 条候选训练{feedback_note}，返回 {len(shortlist)} 个优先配方。")
    display_columns = [
        "candidate_id", "resin", "resin_variant", "dynamic_unit", "filler_pct", "crosslink_density",
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
    if submit_feedback:
        next_batch = recommend_next_experiments(closed_loop, candidate_frame, tested_ids=[feedback_candidate_id], batch_size=8)
        st.markdown("#### 下一轮实验推荐")
        st.dataframe(next_batch[["candidate_id", "acquisition_score", "predicted_multi_objective_score", "prediction_uncertainty"]], width="stretch", hide_index=True)

st.divider()
st.subheader("外部计算任务")
st.caption("可提交已安装的 VASP、Quantum ESPRESSO、CP2K、LAMMPS 或 GROMACS 命令；代理结果不会被标记为真实计算结果。")
job_engine_col, job_dir_col = st.columns(2)
with job_engine_col:
    job_engine = st.selectbox("计算引擎", ["VASP", "Quantum ESPRESSO", "CP2K", "LAMMPS", "GROMACS"])
with job_dir_col:
    job_workdir = st.text_input("任务工作目录", value="work/external")
job_command_text = st.text_input("任务命令", placeholder="例如：vasp_std 或 lmp -in in.production")
if st.button("提交外部计算任务"):
    if not job_command_text.strip():
        st.error("请输入任务命令。")
    else:
        try:
            submitted_job = submit_job(job_engine, shlex.split(job_command_text, posix=False), workdir=job_workdir)
            st.session_state["last_job_id"] = submitted_job.job_id
            st.success(f"任务已提交：{submitted_job.job_id}")
        except Exception as exc:
            st.error(f"任务提交失败：{exc}")
jobs = list_jobs()
if jobs:
    job_table = pd.DataFrame([{
        "任务编号": item.job_id, "引擎": item.engine, "状态": item.status,
        "提交时间": item.submitted_at, "工作目录": item.workdir,
    } for item in jobs[:10]])
    st.dataframe(job_table, width="stretch", hide_index=True)
    selected_job = st.session_state.get("last_job_id", jobs[0].job_id)
    selected_job = st.selectbox("查看任务输出", [item.job_id for item in jobs], index=next((i for i, item in enumerate(jobs) if item.job_id == selected_job), 0))
    output = read_job_output(selected_job)
    st.caption(f"任务状态：{output['status']}")
    if output["status"] == "completed":
        try:
            parsed_result = parse_job_result(selected_job)
            st.json({"解析结果": str(parsed_result)})
        except Exception as exc:
            st.warning(f"任务输出暂不能自动解析：{exc}")
    with st.expander("标准输出 / 错误输出", expanded=False):
        st.code(output["stdout"][-12000:] or "（暂无标准输出）")
        if output["stderr"]:
            st.code(output["stderr"][-12000:], language="text")
