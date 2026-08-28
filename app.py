"""Idle Time Reduction in HEMM - Streamlit entry point and page router.

Run locally with:

    streamlit run app.py

All page content lives in ``app_pages/``. This file only sets the shared page
config and defines the navigation, grouped by what a reviewer would actually
want to do: analyse, plan, and export.
"""
from __future__ import annotations

import streamlit as st

from utils import ui

ui.configure_app()

pages = {
    "": [
        st.Page(
            "app_pages/overview.py", title="Overview",
            icon=":material/dashboard:", default=True,
        ),
    ],
    "Analyse": [
        st.Page(
            "app_pages/idle_breakdown.py", title="Idle breakdown",
            icon=":material/donut_small:",
        ),
        st.Page(
            "app_pages/fleet_performance.py", title="Fleet & risk ranking",
            icon=":material/leaderboard:",
        ),
        st.Page(
            "app_pages/root_causes.py", title="Root cause explorer",
            icon=":material/troubleshoot:",
        ),
    ],
    "Plan": [
        st.Page(
            "app_pages/action_plan.py", title="Action playbook",
            icon=":material/checklist:",
        ),
        st.Page(
            "app_pages/simulation.py", title="Scenario simulator",
            icon=":material/tune:",
        ),
    ],
    "Export": [
        st.Page(
            "app_pages/reports.py", title="Reports & exports",
            icon=":material/description:",
        ),
    ],
}

page = st.navigation(pages, position="top")
page.run()

