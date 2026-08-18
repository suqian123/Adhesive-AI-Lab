from __future__ import annotations

import sys
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

st.set_page_config(page_title="Adhesive AI Lab", page_icon="🧪", layout="wide")
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
    })();
    </script>
    """,
    unsafe_allow_javascript=True,
)

st.markdown(
    """
    <style>
    .stApp { background:#fbfcfd; }
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
    '<div class="hero"><div style="color:#0f766e;font-weight:700;letter-spacing:.08em">MOLECULAR SCREENING WORKSPACE</div>'
    '<h1>粘附材料 AI 辅助分子模拟预测</h1>'
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
    temperature = st.slider("温度 (°C)", -20, 100, 25)
    humidity = st.slider("相对湿度 (%)", 0, 100, 45)
    steps = st.slider("模拟步数", 250, 1400, 650, 50)
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
        )

result = st.session_state.result
combined, simulation = result["combined"], result["simulation"]
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

tab1, tab2, tab3 = st.tabs(["分子特征", "模拟快照", "结果说明"])
with tab1:
    molecule_table = pd.DataFrame.from_dict(result["molecules"], orient="index")
    molecule_table.index = molecule_table.index.map(MOLECULE_KIND_LABELS)
    molecule_table = molecule_table.rename(columns=MOLECULE_FEATURE_LABELS)
    molecule_table.index.name = "组分"
    st.dataframe(molecule_table, width="stretch")
with tab2:
    points = simulation.final_positions
    fig = go.Figure(go.Scatter3d(
        x=points[:, 0], y=points[:, 1], z=points[:, 2], mode="markers",
        marker={"size": 4, "color": points[:, 2], "colorscale": "Teal"},
    ))
    fig.update_layout(height=480, margin={"l":0,"r":0,"t":0,"b":0}, scene={"xaxis_title":"X","yaxis_title":"Y","zaxis_title":"距基底高度"})
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)
with tab3:
    st.info("综合结果由随机森林代理模型与粗粒化界面吸附模拟加权得到，适合配方排序和实验优先级建议。用于发表或工程定标时，请接入真实实验数据、RDKit 与 LAMMPS/GROMACS。")
    st.json({"compatibility_index": combined["compatibility_index"], "temperature_c": temperature, "humidity_pct": humidity, "simulation_steps": steps})
