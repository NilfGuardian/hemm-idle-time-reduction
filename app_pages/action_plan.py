"""Action playbook: scenario-specific fixes, priced from the live filtered data.

Every card below is generated from the current reason ranking and the
sidebar's cost assumptions, not a static slide. Change the date range,
site or cost inputs and every number, ranking and recommended dumper/shovel
updates immediately -- this is the page meant to walk an evaluator from
"here is the insight" to "here is exactly what to do about it".
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
import data_utils as du
from utils import ui
from utils.helpers import format_inr, format_number

ui.apply_theme()

# Step-by-step implementation checklists per addressable reason. Kept
# separate from config.IDLE_LEVERS (which drives the cost model) so the
# operational detail can be edited without touching the numbers.
CHECKLISTS: dict[str, list[str]] = {
    "Shift Change": [
        "Define the handover point as the equipment itself, not the change house.",
        "Stage the incoming crew at the pit boundary 10 minutes before shift end.",
        "Brief supervisors: the outgoing operator keeps hauling until relief is "
        "physically on the machine, then walks out.",
        "Pilot on one fleet for one week; compare Shift Change hours before/after "
        "on the Idle breakdown page before rolling out further.",
    ],
    "Tea/ Breakfast / Snacks": [
        "Split the fleet into 2-3 break groups per shift, published on the roster.",
        "Sequence groups so at least one is always presenting trucks to every "
        "active shovel.",
        "Confirm with operators/reps that no one's break entitlement is shortened "
        "-- only staggered.",
        "Track this reason weekly on Root cause explorer to confirm the stagger "
        "is holding and not drifting back to a simultaneous stop.",
    ],
    "Marching": [
        "Align face-change timing with the next scheduled break or shovel move.",
        "Give dispatch a 15-30 minute lead-time alert before a shovel relocates.",
        "Move trucks in the same rotation as the shovel, not as a full-fleet march.",
    ],
    "Change Operators": [
        "Apply the same hot-seat protocol used for shift change to mid-shift relief.",
        "Log relief timing as its own event so it does not hide inside the general "
        "Shift Change total.",
    ],
    "Delay At Hopper": [
        "Chart hopper arrivals in 15-minute windows to find the peak congestion slot.",
        "Cap simultaneous trucks routed to the hopper in that window; overflow to "
        "an alternate dump point if one exists.",
        "Review with the hopper crew whether a second unloading lane is feasible.",
    ],
    "Fuel Filling": [
        "Move refuelling into the daily-service window already booked per dumper.",
        "If mid-shift fuel is unavoidable, schedule it inside a staggered break "
        "rather than as a standalone stop.",
    ],
}

GENERAL_RECOMMENDATIONS = [
    (
        "Run this dashboard as a daily habit",
        "Review Fleet & risk ranking -> Idle risk before each shift starts, not "
        "after it ends. A prediction only has value if someone acts on it before "
        "the shift, not after the report is filed.",
    ),
    (
        "Retrain the model weekly",
        "Use the Retrain model button in the sidebar as new shifts land, so the "
        "risk flags reflect current fleet condition rather than a July snapshot.",
    ),
    (
        "Close the data gaps that limit the model",
        "Maintenance/fault-code history, the dispatch/roster plan, and rainfall "
        "records would all sharpen both the risk classifier and root-cause "
        "attribution -- see the Reports page for the full list.",
    ),
    (
        "Measure every pilot, don't assume the saving",
        "Whatever is piloted first, compare before/after numbers on this page and "
        "on Root cause explorer, then feed the observed reduction back into the "
        "Scenario simulator instead of trusting the assumed percentage forever.",
    ),
]


@st.cache_data(show_spinner=False)
def _reason_table(delay_events: pd.DataFrame) -> pd.DataFrame:
    if delay_events is None or delay_events.empty:
        return pd.DataFrame()
    return du.build_reason_master(delay_events)


def _lever_card(row: pd.Series, cost_per_hour: float, days: int = 31) -> None:
    """Render one scenario-specific action card with live numbers."""
    lever = config.IDLE_LEVERS.get(str(row["Reason"]))
    if lever is None:
        return
    hours = float(row["Hours"])
    share = float(str(lever["realistic_reduction"]).rstrip("%")) / 100
    recovered_h = hours * share
    value = recovered_h * cost_per_hour

    with st.container(border=True):
        head, m1, m2 = st.columns([3, 1, 1])
        with head:
            st.markdown(f"### {row['Reason']}")
            st.caption(f"Lever: **{lever['lever']}**")
        with m1:
            st.metric("Current loss", f"{format_number(hours)} h")
        with m2:
            st.metric(
                "Recoverable", f"{format_number(recovered_h)} h",
                f"{lever['realistic_reduction']} assumed", delta_color="off",
            )
        st.write(lever["detail"])
        st.caption(f"Annualised value if sustained: **{format_inr(value * 365 / max(days, 1))}**")
        checklist = CHECKLISTS.get(str(row["Reason"]))
        if checklist:
            with st.expander("Implementation checklist"):
                for step in checklist:
                    st.checkbox(step, key=f"chk_{row['Reason']}_{step[:24]}")


def main() -> None:
    """Render the action playbook."""
    if not ui.data_is_ready():
        return

    tables = ui.load_processed()
    filters = ui.sidebar_filters(tables["shifts"])
    ui.sidebar_model_panel()

    shifts = filters.apply(tables["shifts"])
    delay_events = filters.apply(tables["delay_events"])
    reasons = _reason_table(delay_events)

    ui.hero(
        "Action playbook",
        "Scenario-specific fixes and general recommendations, priced from the "
        "current filter selection",
    )

    if shifts.empty or reasons.empty:
        st.warning("No data matches the current filters.")
        return

    addressable = reasons[reasons["Reason"].isin(config.IDLE_LEVERS.keys())]
    addressable = addressable.sort_values("Hours", ascending=False)
    mechanical = reasons[reasons["Reason_Class"] == "Mechanical"]
    total_addressable_h = float(addressable["Hours"].sum())
    total_recoverable_h = sum(
        float(r["Hours"]) * float(str(config.IDLE_LEVERS[r["Reason"]]["realistic_reduction"]).rstrip("%")) / 100
        for _, r in addressable.iterrows()
    )
    total_value = total_recoverable_h * filters.idle_cost_per_hour

    cards = st.columns(3)
    with cards[0]:
        ui.kpi_card(
            "Scenario-specific opportunity", f"{format_number(total_addressable_h)} h",
            "hours currently lost to the reasons covered below",
            tooltip="<strong>What:</strong> Total addressable idle hours for the reasons shown on this page. <strong>Calculation:</strong> <code>sum(Hours)</code> where <code>Addressable = True</code> for reasons in the current filter. <strong>Scope:</strong> Only scheduling-addressable reasons — excludes mechanical breakdowns.",
        )
    with cards[1]:
        ui.kpi_card(
            "Recoverable with these levers", f"{format_number(total_recoverable_h)} h",
            "sum of each lever's realistic reduction", tone="good",
            tooltip="<strong>What:</strong> Realistically recoverable hours using known levers. <strong>Calculation:</strong> For each reason: <code>Hours × realistic_reduction%</code> from <code>IDLE_LEVERS</code> config. <strong>Not the full addressable pool</strong> — only what known interventions can plausibly recover.",
        )
    with cards[2]:
        ui.kpi_card(
            "Annualised value", format_inr(total_value * 365 / max(int(shifts["Shift_Date"].nunique()), 1)),
            f"{format_inr(total_value)} in the selected period", tone="good",
            tooltip="<strong>What:</strong> Annualised rupee value of recoverable savings. <strong>Calculation:</strong> <code>total_value × 365 / days</code> where days = unique shift dates in the period. <strong>Assumption:</strong> The period is representative of the full year. Monsoon or seasonal variation may differ.",
        )

    st.markdown("")
    tab_scenarios, tab_reliability, tab_dispatch, tab_general = st.tabs(
        ["Scenario fixes", "Reliability program", "Dispatch smoothing", "General recommendations"]
    )

    with tab_scenarios:
        st.caption(
            "Ranked by hours currently lost in the selected period. Each card's "
            "numbers update live with the sidebar filters and cost assumptions."
        )
        if addressable.empty:
            st.info("No scheduling-addressable reasons in the current selection.")
        for _, row in addressable.iterrows():
            _lever_card(row, filters.idle_cost_per_hour, int(shifts["Shift_Date"].nunique()))

    with tab_reliability:
        st.markdown(
            "**Mechanical breakdown is not fixable by rescheduling** — it needs a "
            "reliability program. It is also the single largest bucket of lost time, "
            "so it deserves first priority even though it has no quick scheduling lever."
        )
        mech_hours = float(mechanical["Hours"].sum())
        m1, m2 = st.columns(2)
        with m1:
            ui.kpi_card(
                "Mechanical hours lost", f"{format_number(mech_hours)} h",
                format_inr(mech_hours * filters.idle_cost_per_hour), tone="accent",
                tooltip="<strong>What:</strong> Total hours lost to mechanical delays (breakdowns, planned maintenance). <strong>Calculation:</strong> <code>sum(Hours)</code> where <code>Reason_Class = Mechanical</code>. <strong>Cost:</strong> Hours × idle_cost_per_hour. <strong>Not addressable</strong> by scheduling.",
            )
        with m2:
            dumper_dt = delay_events[
                (delay_events["Equipment_Class"] == "Dumper")
                & (delay_events["Reason_Class"] == "Mechanical")
            ]
            n_dumpers = dumper_dt["Equipment_ID"].nunique() if not dumper_dt.empty else 0
            ui.kpi_card("Dumpers affected", f"{n_dumpers}", "with at least one mechanical event",
                        tooltip="<strong>What:</strong> Number of unique dumpers with at least one mechanical delay event. <strong>Calculation:</strong> <code>nunique(Equipment_ID)</code> in filtered mechanical delay events. <strong>Use:</strong> Shows how widespread mechanical issues are across the fleet.")

        if not dumper_dt.empty:
            worst = (
                dumper_dt.groupby("Equipment_ID", as_index=False)
                .agg(Hours=("Delay_Min", lambda s: s.sum() / 60), Events=("Delay_Min", "size"))
                .nlargest(10, "Hours")
            )
            st.markdown("#### Dumpers to prioritise for inspection")
            st.dataframe(
                worst, hide_index=True,
                column_config={
                    "Equipment_ID": "Dumper",
                    "Hours": st.column_config.NumberColumn(format="%.1f"),
                    "Events": st.column_config.NumberColumn(format="%d"),
                },
            )
            ui.note(
                "These are candidates for a targeted maintenance review, not a "
                "performance conversation with the operator. Cross-reference against "
                "workshop job cards for the repeat failure mode."
            )
        with st.expander("Implementation checklist"):
            for step in [
                "Pull workshop job cards for the dumpers listed above and look for "
                "a repeat failure mode (tyres, hydraulics, electrical, engine).",
                "Push for fault-code capture in the FMS so 'Down' events carry a "
                "cause, not just a duration.",
                "Shift high-mechanical-loss dumpers to shorter, usage-based service "
                "intervals instead of calendar-based ones.",
            ]:
                st.checkbox(step, key=f"chk_reliability_{step[:24]}")

    with tab_dispatch:
        st.markdown(
            "**Shovel congestion** shows up as queueing, which the model's risk "
            "classifier leans on heavily (`Dumpers_Per_Loader`, `Loader_Delay_Min`). "
            "The fix is a dispatch decision, not a truck-side one."
        )
        shovels = du.build_group_summary(shifts, "Loading_Unit", min_shifts=10)
        if shovels.empty:
            st.info("Not enough shovel-linked shifts in this selection.")
        else:
            worst_shovels = shovels.nlargest(8, "Queue min per cycle")
            st.markdown("#### Shovels with the most queueing per cycle")
            st.dataframe(
                worst_shovels[["Loading_Unit", "Idle hours", "Queue min per cycle", "Dumper_Shifts"]],
                hide_index=True,
                column_config={
                    "Loading_Unit": "Shovel",
                    "Idle hours": st.column_config.NumberColumn(format="%.0f"),
                    "Queue min per cycle": st.column_config.NumberColumn(format="%.2f"),
                    "Dumper_Shifts": st.column_config.NumberColumn("Dumper-shifts", format="%d"),
                },
            )
        with st.expander("Implementation checklist"):
            for step in [
                "Cap the number of trucks assigned per shovel per 15-30 minute window "
                "using the queue pattern above.",
                "When a shovel logs its own delay (breakdown, relocation), redirect "
                "its assigned trucks immediately rather than letting them queue.",
                "Review the shovels flagged here weekly -- persistent congestion at "
                "the same shovel points to an allocation problem, not a one-off.",
            ]:
                st.checkbox(step, key=f"chk_dispatch_{step[:24]}")

    with tab_general:
        st.caption("Not tied to a single reason code -- process and data changes that compound over time.")
        for title, detail in GENERAL_RECOMMENDATIONS:
            with st.container(border=True):
                st.markdown(f"**{title}**")
                st.write(detail)

        ui.note(
            "Suggested rollout order: pilot the cheapest, highest-impact scheduling "
            "fix first (usually Shift Change), measure it for 1-2 weeks, then move to "
            "the reliability program in parallel since it takes longer to show results. "
            "Dispatch smoothing and the data-collection items compound the gains from "
            "both."
        )


main()
