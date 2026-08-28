"""Overview: headline idle KPIs, cost, and the top recommended actions.

The landing page answers the three questions a mine manager asks first:
how much time are we losing, what is it costing, and what do we do on Monday.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
from utils import charts, ui
from utils.helpers import format_inr, format_number

ui.apply_theme()


def top_actions(reasons: pd.DataFrame, cost_per_hour: float) -> pd.DataFrame:
    """Build the ranked action list from the addressable reasons.

    Each row pairs a reason with the scheduling lever that targets it and the
    value of a realistic, partial reduction. Break entitlements are never cut,
    only staggered, so the saving comes from removing simultaneity.
    """
    if reasons.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for _, row in reasons[reasons["Addressable"]].iterrows():
        lever = config.IDLE_LEVERS.get(str(row["Reason"]))
        if lever is None:
            continue
        share = float(str(lever["realistic_reduction"]).rstrip("%")) / 100
        hours_saved = float(row["Hours"]) * share
        if hours_saved <= 0:
            # e.g. "Marching" exists in config but does not occur for dumpers
            continue
        rows.append(
            {
                "Reason": row["Reason"],
                "Action": lever["lever"],
                "How": lever["detail"],
                "Hours lost": float(row["Hours"]),
                "Assumed reduction": lever["realistic_reduction"],
                "Hours recovered": hours_saved,
                "Value": hours_saved * cost_per_hour,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values("Hours recovered", ascending=False).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def _reason_table(delay_events: pd.DataFrame) -> pd.DataFrame:
    """Rebuild the reason ranking for the filtered subset."""
    if delay_events is None or delay_events.empty:
        return pd.DataFrame()
    import data_utils as du

    return du.build_reason_master(delay_events)


def main() -> None:
    """Render the overview page."""
    if not ui.data_is_ready():
        return

    tables = ui.load_processed()
    shifts_all = tables["shifts"]
    filters = ui.sidebar_filters(shifts_all)
    ui.sidebar_model_panel()

    shifts = filters.apply(shifts_all)
    reasons_scope = filters.apply(tables["delay_events"])
    hourly = filters.apply(tables["hourly"])

    ui.hero(
        "Idle time overview",
        f"{config.PROJECT_SUBTITLE} &nbsp;·&nbsp; "
        f"{format_number(len(shifts))} dumper-shifts in view",
    )

    if shifts.empty:
        st.warning("No data matches the current filters. Widen the date range or sections.")
        return

    summary = ui.summarise_idle(shifts, filters)
    ui.headline_kpis(summary, filters)

    st.markdown("")
    ui.note(
        f"<b>Idle time</b> is every minute a dumper was available and manned but not moving "
        f"material: queueing at the shovel or dump, standing still mid-trip, and every "
        f"reason-coded delay logged against the machine. Over the selected period the fleet "
        f"of {summary.dumpers} dumpers lost <b>{format_number(summary.total_idle_hours)} hours</b> "
        f"across {format_number(summary.dumper_shifts)} dumper-shifts, an average of "
        f"<b>{summary.idle_hours_per_dumper_shift:.1f} hours in every 8-hour shift</b>. "
        f"Includes shifts where the truck completed no cycles because it was down the full 8 hours."
    )

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.plotly_chart(charts.cycle_breakdown_bar(shifts))
    with right:
        reasons = _reason_table(reasons_scope)
        if not reasons.empty:
            st.plotly_chart(charts.reason_class_donut(reasons))

    st.divider()
    st.subheader("What to do about it")

    reasons = _reason_table(reasons_scope)
    actions = top_actions(reasons, filters.idle_cost_per_hour)
    if actions.empty:
        st.info("No addressable reasons found in the current selection.")
    else:
        recovered = float(actions["Hours recovered"].sum())
        value = float(actions["Value"].sum())
        extra_tonnes = (recovered) * summary.tonnes_per_operating_hour

        cards = st.columns(3)
        with cards[0]:
            ui.kpi_card(
                "Recoverable idle", f"{format_number(recovered)} h",
                "sum of the realistic reductions below", tone="good",
            )
        with cards[1]:
            ui.kpi_card(
                "Annualised value", format_inr(value * 12),
                f"{format_inr(value)} in the selected period", tone="good",
            )
        with cards[2]:
            ui.kpi_card(
                "Extra material moved", f"{format_number(extra_tonnes)} t",
                f"at the observed {summary.tonnes_per_operating_hour:,.0f} t per operating hour",
                tone="good",
            )

        st.markdown("")
        for rank, row in actions.head(4).iterrows():
            with st.container(border=True):
                head, metric = st.columns([4, 1])
                with head:
                    st.markdown(
                        f"**{rank + 1}. {row['Action']}** — targets *{row['Reason']}* "
                        f"({format_number(row['Hours lost'])} h lost)"
                    )
                    st.caption(str(row["How"]))
                with metric:
                    st.metric(
                        "Recovers",
                        f"{row['Hours recovered']:,.0f} h",
                        f"{row['Assumed reduction']} of the loss",
                        delta_color="off",
                    )

        ui.note(
            "Reductions are stated as assumptions, not predictions. Break time is never "
            "removed: the saving comes from <b>staggering</b> breaks and relieving operators "
            "at the machine, so the whole fleet no longer stops at the same moment. "
            "Adjust every assumption on the <b>Scenario simulator</b> page."
        )

    st.divider()
    trend, profile = st.columns(2, gap="large")
    with trend:
        st.plotly_chart(charts.idle_trend(shifts))
    with profile:
        if not hourly.empty:
            st.plotly_chart(charts.hour_of_day_profile(hourly))
        else:
            st.info("No hourly idle data in the current selection.")

    st.caption(
        "Use the navigation above for the cycle breakdown, fleet league table, "
        "root-cause analysis, scenario simulation and exports."
    )


main()
