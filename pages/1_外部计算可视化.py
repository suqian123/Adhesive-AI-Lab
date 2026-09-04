"""Visual inspection page for persisted external-calculation records."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from adhesive_ai.campaign_runner import list_campaign_runs
from adhesive_ai.jobs import JobRecord, list_jobs, parse_job_result, read_job_output


st.set_page_config(page_title="外部计算可视化", page_icon=":material/monitoring:", layout="wide")

st.markdown(
    """
    <style>
    header[data-testid="stHeader"] { background:transparent; }
    [data-testid="stToolbarActions"],
    [data-testid="stToolbarActionButton"],
    [data-testid="stAppDeployButton"],
    [data-testid="stMainMenu"],
    [data-testid="stMainMenuButton"] { display:none !important; }
    [data-testid="stDecoration"] { display:none; }
    </style>
    """,
    unsafe_allow_html=True,
)

STATUS_LABELS = {
    "queued": "排队中",
    "running": "计算中",
    "blocked": "已阻塞",
    "completed": "已完成",
    "partial": "部分完成",
    "failed": "失败",
    "cancelled": "已终止",
    "termination_failed": "终止未完成",
    "external_pending": "等待外部结果导入",
    "imported": "已导入并回写",
}
SCALE_LABELS = {
    "quantum": "量子化学",
    "atomistic-md": "全原子 MD",
    "coarse-grained": "粗粒化",
}
FIELD_LABELS = {
    "engine": "计算引擎",
    "total_energy_ev": "总能量 (eV)",
    "adsorption_energy_ev": "吸附能 (eV)",
    "reaction_energy_ev": "反应能 (eV)",
    "reaction_barrier_ev": "反应能垒 (eV)",
    "ce3_fraction": "Ce³⁺ 分数",
    "raw_energy_values_ev": "原始能量序列 (eV)",
    "facet": "CeO₂ 晶面",
    "oxygen_vacancy_fraction": "氧空位分数",
    "hydroxyl_fraction": "羟基化分数",
    "temperature_c": "温度 (°C)",
    "temperatures_c": "温度范围 (°C)",
    "area_nm2": "界面面积 (nm²)",
    "filler_pct": "填料比例 (%)",
    "result_file": "结果文件",
    "calculation_kind": "计算类型",
    "input_validation": "输入校验",
    "production_approved": "生产计算已批准",
    "submission_source": "提交来源",
    "candidate_library_version": "候选库版本",
    "campaign_task_id": "多尺度任务编号",
    "campaign_run_id": "多尺度运行编号",
    "Step": "模拟步",
    "Temp": "温度 (K)",
    "PotEng": "势能",
    "TotEng": "总能",
    "KinEng": "动能",
    "Press": "压力",
    "Volume": "体积",
    "Density": "密度",
}
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
                "Turntable rotation": "转盘旋转",
                "Fullscreen": "全屏",
                "Exit fullscreen": "退出全屏",
                "Full screen": "全屏",
            }
        }
    },
}
UI_TRANSLATIONS = {
    "Fullscreen": "全屏",
    "Exit fullscreen": "退出全屏",
    "Enter fullscreen": "进入全屏",
    "Close fullscreen": "退出全屏",
    "Sort ascending": "升序排序",
    "Sort descending": "降序排序",
    "Clear sort": "清除排序",
    "Reset sort": "重置排序",
    "Hide column": "隐藏列",
    "Show all columns": "显示所有列",
    "Show/hide columns": "显示/隐藏列",
    "Search": "搜索",
    "Type to search": "输入关键词搜索",
    "Type to search...": "输入关键词搜索",
    "Choose options": "选择选项",
    "Choose an option": "选择一个选项",
    "Choose an option...": "选择一个选项",
    "No options": "没有可选项",
    "No options available": "没有可选项",
    "Select all": "全选",
    "Deselect all": "取消全选",
    "Clear": "清除",
    "Clear all": "全部清除",
    "Apply": "应用",
    "Cancel": "取消",
    "Close": "关闭",
    "Loading": "加载中",
    "Loading...": "加载中...",
    "Show more": "显示更多",
    "Show less": "收起",
    "Previous": "上一页",
    "Next": "下一页",
    "Rows per page": "每页行数",
    "Page": "页",
    "Search columns": "搜索列",
    "Filter": "筛选",
    "Filters": "筛选条件",
    "Reset filters": "重置筛选",
    "Download as CSV": "下载为 CSV",
    "Download": "下载",
    "Copy": "复制",
    "Copy to clipboard": "复制到剪贴板",
    "Reset columns": "重置列",
    "Autosize columns": "自动调整列宽",
    "Autosize": "自动调整大小",
    "Pin column": "固定列",
    "Unpin column": "取消固定列",
    "Column actions": "列操作",
    "Data type": "数据类型",
    "Count": "数量",
    "Mean": "平均值",
    "Std": "标准差",
    "Min": "最小值",
    "Max": "最大值",
    "Median": "中位数",
    "Sum": "总和",
    "Missing": "缺失值",
    "Null": "空值",
}

st.html(
    "<script>(() => { const labels = "
    + json.dumps(UI_TRANSLATIONS, ensure_ascii=False)
    + r""";
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
            if (labels[source]) node.nodeValue = node.nodeValue.replace(source, labels[source]);
        }
    };
    translate();
    new MutationObserver(translate).observe(document.body, {
        subtree: true, childList: true, attributes: true, characterData: true,
        attributeFilter: ["aria-label", "title", "data-title", "placeholder"],
    });
    })();</script>""",
    unsafe_allow_javascript=True,
)


def _field_label(name: object) -> str:
    return FIELD_LABELS.get(str(name), str(name))


def _localized_payload(value: object) -> object:
    if isinstance(value, dict):
        return {_field_label(key): _localized_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_localized_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_localized_payload(item) for item in value]
    return value


def _job_index() -> dict[str, JobRecord]:
    return {job.job_id: job for job in list_jobs()}


def _task_rows(jobs_by_id: dict[str, JobRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    campaign_job_ids: set[str] = set()
    for run in list_campaign_runs():
        candidate_id = str(run.get("candidate_id") or "—")
        run_id = str(run.get("run_id") or "—")
        formulation_id = str((run.get("candidate_snapshot") or {}).get("formulation_id") or "历史记录")
        for task in run.get("tasks", []):
            job_id = str(task.get("job_id") or "")
            job = jobs_by_id.get(job_id)
            if job_id:
                campaign_job_ids.add(job_id)
            conditions = dict(task.get("conditions") or {})
            rows.append({
                "row_id": f"campaign:{run_id}:{task.get('task_id')}",
                "来源": "自动多尺度",
                "候选编号": candidate_id,
                "配方指纹": formulation_id,
                "运行编号": run_id,
                "任务编号": str(task.get("task_id") or "—"),
                "计算尺度": SCALE_LABELS.get(str(task.get("scale") or ""), str(task.get("scale") or "—")),
                "计算引擎": str(task.get("engine") or (job.engine if job else "未配置")),
                "状态": STATUS_LABELS.get(str(task.get("status") or ""), str(task.get("status") or "未知")),
                "外部任务编号": job_id or "—",
                "晶面": str(conditions.get("facet") or "—"),
                "提交时间": str(job.submitted_at if job else run.get("created_at") or "—"),
                "完成时间": str(job.finished_at if job and job.finished_at else "—"),
                "工作目录": str(task.get("workdir") or (job.workdir if job else "—")),
                "原因": str(task.get("blocker") or task.get("error") or (job.error if job else "") or "—"),
                "conditions": conditions,
                "job_id": job_id,
            })
    for job_id, job in jobs_by_id.items():
        if job_id in campaign_job_ids:
            continue
        metadata = dict(job.metadata or {})
        rows.append({
            "row_id": f"manual:{job_id}",
            "来源": "单独提交",
            "候选编号": str(metadata.get("candidate_id") or "未绑定"),
            "配方指纹": str(metadata.get("formulation_id") or "未标记"),
            "运行编号": str(metadata.get("campaign_run_id") or "—"),
            "任务编号": str(metadata.get("campaign_task_id") or job_id),
            "计算尺度": "单独任务",
            "计算引擎": job.engine,
            "状态": STATUS_LABELS.get(job.status, job.status),
            "外部任务编号": job_id,
            "晶面": str(metadata.get("facet") or "—"),
            "提交时间": job.submitted_at,
            "完成时间": job.finished_at or "—",
            "工作目录": job.workdir,
            "原因": job.error or "—",
            "conditions": {key: value for key, value in metadata.items() if key not in {"candidate_id", "formulation_id"}},
            "job_id": job_id,
        })
    return rows


def _render_result(job_id: str, jobs_by_id: dict[str, JobRecord]) -> None:
    job = jobs_by_id.get(job_id)
    if job is None:
        st.info("该自动任务尚未提交至外部求解器，因此暂无可解析结果。")
        return
    if job.status != "completed":
        st.info("外部任务完成后将在此自动显示可解析结果。")
        return
    try:
        parsed = parse_job_result(job_id)
    except (OSError, RuntimeError, ValueError) as exc:
        st.warning("任务已完成，但当前输出无法解析为结构化图表。可在下方查看原始日志。")
        st.caption("解析失败详情保留在原始日志中，供技术排查。")
        return

    if is_dataclass(parsed):
        values = asdict(parsed)
        result_frame = pd.DataFrame([{"指标": _field_label(key), "数值": value} for key, value in values.items()])
        st.dataframe(result_frame, width="stretch", hide_index=True)
        numeric = {
            _field_label(key): value
            for key, value in values.items()
            if isinstance(value, (int, float)) and value is not None
        }
        if numeric:
            figure = px.bar(
                x=list(numeric), y=list(numeric.values()), labels={"x": "DFT 指标", "y": "数值"},
                color_discrete_sequence=["#0f766e"],
            )
            figure.update_layout(height=300, margin={"l": 10, "r": 10, "t": 20, "b": 10}, showlegend=False)
            st.plotly_chart(figure, width="stretch", config=PLOTLY_CONFIG)
        return

    if isinstance(parsed, dict):
        series = {name: values for name, values in parsed.items() if hasattr(values, "size") and values.size}
        if not series:
            st.info("未从输出中识别到可绘制的 LAMMPS 热力学序列。")
            return
        step = series.get("Step")
        figure = go.Figure()
        for name, values in series.items():
            if name == "Step":
                continue
            figure.add_trace(go.Scatter(
                x=step if step is not None else list(range(len(values))), y=values,
                mode="lines", name=_field_label(name),
            ))
        figure.update_layout(
            height=380, xaxis_title="模拟步" if step is not None else "记录序号", yaxis_title="热力学量",
            margin={"l": 10, "r": 10, "t": 20, "b": 10},
        )
        st.plotly_chart(figure, width="stretch", config=PLOTLY_CONFIG)
        st.dataframe(pd.DataFrame(series).rename(columns=_field_label), width="stretch", hide_index=True)
        return

    st.info("该任务的结果格式暂不支持图表展示。")


st.title("外部计算可视化")
st.caption("集中查看自动多尺度和单独提交的外部计算任务。筛选不会改变任务状态或回写结果。")

jobs_by_id = _job_index()
rows = _task_rows(jobs_by_id)
if not rows:
    st.info("尚未找到外部计算记录。请先在主页面创建并提交多尺度或单独外部计算任务。")
    st.stop()

all_rows = pd.DataFrame(rows)
filter_col1, filter_col2, filter_col3, refresh_col = st.columns([1.2, 1.2, 1.2, 0.45])
with filter_col1:
    candidate_filter = st.selectbox("候选编号", ["全部"] + sorted(all_rows["候选编号"].unique().tolist()))
with filter_col2:
    engine_filter = st.multiselect("计算引擎", sorted(all_rows["计算引擎"].unique().tolist()), default=[])
with filter_col3:
    status_filter = st.multiselect("任务状态", sorted(all_rows["状态"].unique().tolist()), default=[])
with refresh_col:
    st.write("")
    if st.button("刷新", icon=":material/refresh:", width="stretch"):
        st.rerun()

filtered = all_rows.copy()
if candidate_filter != "全部":
    filtered = filtered.loc[filtered["候选编号"] == candidate_filter]
if engine_filter:
    filtered = filtered.loc[filtered["计算引擎"].isin(engine_filter)]
if status_filter:
    filtered = filtered.loc[filtered["状态"].isin(status_filter)]
if filtered.empty:
    st.info("当前筛选条件下没有外部计算记录。")
    st.stop()

summary = filtered["状态"].value_counts()
metric_cols = st.columns(4)
metric_cols[0].metric("当前记录", len(filtered))
metric_cols[1].metric("计算中", int(summary.get("计算中", 0) + summary.get("排队中", 0)))
metric_cols[2].metric("已完成", int(summary.get("已完成", 0) + summary.get("部分完成", 0)))
metric_cols[3].metric("异常或阻塞", int(summary.get("失败", 0) + summary.get("已阻塞", 0) + summary.get("终止未完成", 0)))

chart_col, status_col = st.columns(2)
with chart_col:
    engine_counts = filtered.groupby(["计算引擎", "状态"], dropna=False).size().reset_index(name="任务数")
    figure = px.bar(engine_counts, x="计算引擎", y="任务数", color="状态", barmode="stack")
    figure.update_layout(height=320, margin={"l": 10, "r": 10, "t": 20, "b": 10})
    st.plotly_chart(figure, width="stretch", config=PLOTLY_CONFIG)
with status_col:
    status_counts = filtered.groupby("状态", dropna=False).size().reset_index(name="任务数")
    figure = px.pie(status_counts, names="状态", values="任务数", hole=0.48)
    figure.update_layout(height=320, margin={"l": 10, "r": 10, "t": 20, "b": 10})
    st.plotly_chart(figure, width="stretch", config=PLOTLY_CONFIG)

st.subheader("任务总览")
table_columns = [
    "来源", "候选编号", "配方指纹", "运行编号", "任务编号", "计算尺度", "计算引擎",
    "状态", "外部任务编号", "晶面", "提交时间", "完成时间", "原因",
]
st.dataframe(filtered.reindex(columns=table_columns), width="stretch", hide_index=True)

st.subheader("任务详情")
if filtered.empty:
    st.info("当前筛选条件下没有任务记录。")
    st.stop()
details_by_id = {str(row["row_id"]): row for row in filtered.to_dict(orient="records")}
selected_row_id = st.selectbox(
    "选择任务",
    list(details_by_id),
    format_func=lambda row_id: (
        f"{details_by_id[row_id]['候选编号']} · {details_by_id[row_id]['任务编号']} · "
        f"{details_by_id[row_id]['状态']}"
    ),
)
selected = details_by_id[selected_row_id]
detail_col1, detail_col2, detail_col3, detail_col4 = st.columns(4)
detail_col1.metric("候选编号", selected["候选编号"])
detail_col2.metric("运行编号", selected["运行编号"])
detail_col3.metric("计算引擎", selected["计算引擎"])
detail_col4.metric("任务状态", selected["状态"])
st.caption(f"配方指纹：{selected['配方指纹']} · 工作目录：{selected['工作目录']}")
with st.expander("计算条件与任务元数据", expanded=False):
    st.json(_localized_payload(selected["conditions"]))

result_tab, log_tab = st.tabs(["结构化结果与图表", "原始日志"])
with result_tab:
    _render_result(str(selected["job_id"]), jobs_by_id)
with log_tab:
    if not selected["job_id"]:
        st.info("该任务尚未提交至外部求解器，暂无运行日志。")
    else:
        output = read_job_output(str(selected["job_id"]), tail_chars=12_000)
        st.caption("仅显示每份日志末尾 12,000 个字符。")
        st.code(str(output["stdout"]) or "（暂无标准输出）", language="text")
        if output["stderr"]:
            st.code(str(output["stderr"]), language="text")
