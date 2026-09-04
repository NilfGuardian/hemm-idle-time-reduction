"""Overview: headline idle KPIs, cost, and the top recommended actions.

The landing page answers the three questions a mine manager asks first:
how much time are we losing, what is it costing, and what do we do on Monday.
"""
from __future__ import annotations

import pandas as pd
import numpy as np
import streamlit as st

import config
import data_utils as du
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


def _auto_insights(
    shifts: pd.DataFrame,
    reasons: pd.DataFrame,
    summary: ui.IdleSummary,
    filters: ui.Filters,
    actions: pd.DataFrame = None,
) -> list[str]:
    """Generate plain-English findings from the data, auto-discovered."""
    insights: list[str] = []
    if shifts.empty:
        return insights

    dumper_summary = du.build_group_summary(shifts, "Equipment_ID")
    if not dumper_summary.empty:
        worst = dumper_summary.iloc[0]
        median_idle = float(dumper_summary["Idle h per shift"].median())
        ratio = worst["Idle h per shift"] / median_idle if median_idle else 0
        insights.append(
            f"Dumper **{worst['Equipment_ID']}** lost **{worst['Idle h per shift']:.1f} h per shift** "
            f"— {ratio:.1f}x the fleet median of {median_idle:.1f} h"
        )
        best = dumper_summary.iloc[-1]
        insights.append(
            f"Best performer: **{best['Equipment_ID']}** at **{best['Idle h per shift']:.1f} h per shift** "
            f"— the gap between worst and best is "
            f"{worst['Idle h per shift'] - best['Idle h per shift']:.1f} h per shift"
        )

    if not reasons.empty:
        top_reason = reasons.iloc[0]
        insights.append(
            f"Single biggest cause: **{top_reason['Reason']}** at **{top_reason['Hours']:.0f} h** "
            f"({top_reason['Share_Pct']:.1f}% of all idle time)"
        )
        addressable = reasons[reasons["Addressable"]]
        if not addressable.empty:
            addr_hours = float(addressable["Hours"].sum())
            addr_pct = addr_hours / summary.total_idle_hours * 100 if summary.total_idle_hours else 0
            recovered_h = float(actions["Hours recovered"].sum()) if not actions.empty else 0
            recovered_value = float(actions["Value"].sum()) if not actions.empty else 0
            insights.append(
                f"**{addr_pct:.0f}% of all idle time is addressable** by scheduling changes "
                f"({addr_hours:.0f} h), of which **{recovered_h:.0f} h ({format_inr(recovered_value)}) "
                f"is realistically recoverable** with known levers"
            )

    if "Loading_Unit" in shifts.columns:
        loader_summary = du.build_group_summary(shifts, "Loading_Unit")
        if not loader_summary.empty:
            worst_loader = loader_summary.iloc[0]
            insights.append(
                f"Shovel **{worst_loader['Loading_Unit']}** has the highest associated idle: "
                f"**{worst_loader['Idle h per shift']:.1f} h per dumper-shift** assigned to it"
            )

    if summary.idle_share_of_cycle > 0:
        mean_cycle = shifts["Cycle_Min"].sum() / shifts["Cycles"].replace(0, np.nan).sum()
        insights.append(
            f"Inside the haul cycle, **{summary.idle_share_of_cycle:.1f}% of cycle time is idle** "
            f"— dumpers stand still for {summary.idle_share_of_cycle / 100 * mean_cycle:.1f} minutes "
            f"of every {mean_cycle:.0f}-minute cycle"
        )

    if summary.fuel_litres_idle > 0:
        insights.append(
            f"Diesel burnt while idling: **{summary.fuel_litres_idle:,.0f} litres** "
            f"({format_inr(summary.fuel_cost_idle)}) — the engine runs in every idle event"
        )

    return insights


@st.cache_data(show_spinner=False)
def _reason_table(delay_events: pd.DataFrame) -> pd.DataFrame:
    """Rebuild the reason ranking for the filtered subset."""
    if delay_events is None or delay_events.empty:
        return pd.DataFrame()
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
    reasons = _reason_table(reasons_scope)

    actions = top_actions(reasons, filters.idle_cost_per_hour)
    if not actions.empty:
        recovered_h = float(actions["Hours recovered"].sum())
        hero_value = float(actions["Value"].sum())
    else:
        recovered_h = 0.0
        hero_value = 0.0
    st.markdown(
        f'<div style="text-align:center; padding:8px 0; margin:4px 0 10px 0;">'
        f'<div style="font-size:14px; color:{config.TEXT_MUTED}; letter-spacing:2px; '
        f'text-transform:uppercase;">Realistic recoverable savings</div>'
        f'<div style="font-size:42px; font-weight:700; color:{config.LIME}; '
        f'font-family:Inter, sans-serif; margin:8px 0;">'
        f'{format_inr(hero_value)}</div>'
        f'<div style="font-size:13px; color:{config.TEXT_MUTED};">'
        f'{recovered_h / summary.dumper_shifts:.1f} h per dumper-shift · '
        f'{recovered_h:,.0f} h recoverable from {summary.addressable_hours:,.0f} h addressable · '
        f'annualised: {format_inr(hero_value * 365 / max(summary.days, 1))}'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    ui.headline_kpis(summary, filters)

    ui.note(
        f"<b>Idle time</b> is every minute a dumper was available and manned but not moving "
        f"material: queueing at the shovel or dump, standing still mid-trip, and every "
        f"reason-coded delay logged against the machine. Over the selected period the fleet "
        f"of {summary.dumpers} dumpers lost <b>{format_number(summary.total_idle_hours)} hours</b> "
        f"across {format_number(summary.dumper_shifts)} dumper-shifts, an average of "
        f"<b>{summary.idle_hours_per_dumper_shift:.1f} hours in every {config.SHIFT_LENGTH_HOURS:.0f}-hour shift</b>. "
        f"Includes shifts where the truck completed no cycles because it was down the full {config.SHIFT_LENGTH_HOURS:.0f} hours."
    )

    insights = _auto_insights(shifts, reasons, summary, filters, actions)
    if insights:
        st.markdown("#### Key findings")
        for insight in insights[:3]:
            st.markdown(f"- {insight}")
        if len(insights) > 3:
            with st.expander("View all findings"):
                for insight in insights[3:]:
                    st.markdown(f"- {insight}")

    left, right = st.columns([3, 2], gap="large")
    with left:
        ui.chart_tooltip("Cycle time breakdown", "<strong>Chart:</strong> Stacked bar showing how each dumper-shift's cycle time is decomposed. <strong>Legends:</strong> Loading, Travel (loaded + empty), Queue (at shovel/dump), Stopped (mid-trip), Spotting. <strong>Calculation:</strong> Each segment is the mean minutes per cycle from the FMS cycle report. <strong>Use:</strong> Identify which time components dominate the haul cycle.")
        st.plotly_chart(charts.cycle_breakdown_bar(shifts), theme=None)
    with right:
        if not reasons.empty:
            ui.chart_tooltip("Reason class donut", "<strong>Chart:</strong> Donut chart showing the share of delay hours by reason class. <strong>Legends:</strong> Organisational (scheduling, breaks, queue), Mechanical (breakdowns, maintenance), Operational (dispatch, routing). <strong>Calculation:</strong> <code>sum(Hours)</code> grouped by <code>Reason_Class</code>, shown as percentage of total.")
            st.plotly_chart(charts.reason_class_donut(reasons), theme=None)

    avg_km_per_cycle = float(shifts['Km_Per_Cycle'].mean()) if 'Km_Per_Cycle' in shifts.columns else 0
    total_cycles = float(shifts['Cycles'].sum()) if 'Cycles' in shifts.columns else 0
    loaded_speed = (shifts['Travel_Loaded'].sum() / 60) / (total_cycles * avg_km_per_cycle / 2) if total_cycles > 0 and avg_km_per_cycle > 0 else 0
    empty_speed = (shifts['Travel_Empty'].sum() / 60) / (total_cycles * avg_km_per_cycle / 2) if total_cycles > 0 and avg_km_per_cycle > 0 else 0
    ui.note(
        f"<b>Why is loaded travel time longer than empty?</b> The haul cycle is a "
        f"round trip on the same road, so the distance is the same both ways "
        f"(~{avg_km_per_cycle:.1f} km/cycle). A loaded dumper is slower — <b>{loaded_speed:.1f} km/h loaded</b> vs "
        f"<b>{empty_speed:.1f} km/h empty</b> — so the loaded leg takes more time. The FMS "
        f"column names are swapped (it labels loaded travel as 'EMPTY_STOPPED') "
        f"and we correct this in the mapping."
    )

    st.divider()

    if actions.empty:
        st.info("No addressable reasons found in the current selection.")
    else:
        recovered = float(actions["Hours recovered"].sum())
        value = float(actions["Value"].sum())
        st.markdown(
            f"#### Recommended actions — {format_inr(value * 365 / max(summary.days, 1))} annualised value"
        )
        st.caption(f"{format_number(recovered)} hours recoverable from {len(actions)} scheduling changes")

        for rank, row in actions.head(5).iterrows():
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

    with st.expander("Trends & hourly profile"):
        trend, profile = st.columns(2, gap="large")
        with trend:
            st.plotly_chart(charts.idle_trend(shifts), theme=None)
        with profile:
            if not hourly.empty:
                st.plotly_chart(charts.hour_of_day_profile(hourly), theme=None)
            else:
                st.info("No hourly idle data in the current selection.")

    st.caption(
        "Use the navigation above for the cycle breakdown, fleet league table, "
        "root-cause analysis, scenario simulation and exports."
    )


main()
