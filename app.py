"""Application entry point and explicit page navigation."""

from __future__ import annotations

from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent
navigation = st.navigation(
    [
        st.Page(
            ROOT / "platform_page.py",
            title="多尺度模拟平台",
            icon=":material/account_tree:",
            default=True,
        ),
        st.Page(
            ROOT / "pages" / "1_外部计算可视化.py",
            title="外部计算可视化",
            icon=":material/monitoring:",
        ),
    ],
    position="sidebar",
)
navigation.run()
