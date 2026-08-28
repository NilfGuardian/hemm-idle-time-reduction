"""Root Causes: why the fleet is idle, ranked and classified by controllability.

Every delay event in the FMS carries a status code. Those codes are grouped into
four classes so the conversation moves from "we lose a lot of time" to "this much
of it is a scheduling decision we can change".
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
import data_utils as du
from utils import charts, ui
from utils.helpers import format_inr, format_number, safe_shift_label

ui.apply_theme()


@st.cache_data(show_spinner=False)
def reason_master(delay_events: pd.DataFrame) -> pd.DataFrame:
    """Rebuild the reason ranking for the filtered subset."""
    if delay_events is None or delay_events.empty:
        return pd.DataFrame()
    return du.build_reason_master(delay_events)


def main() -> None:
    """Render the root-cause page."""
    if not ui.data_is_ready():
        return

    tables = ui.load_processed()
    filters = ui.sidebar_filters(tables["shifts"])
    ui.sidebar_model_panel()

    delay_events = filters.apply(tables["delay_events"])
    dumper_delays = (
        delay_events[delay_events["Equipment_Class"] == "Dumper"]
        if not delay_events.empty else delay_events
    )
    reasons = reason_master(delay_events)

    ui.hero("Root cause explorer", "What the fleet is waiting for, and who can change it")

    if reasons.empty:
        st.warning("No reason-coded delay events match the current filters.")
        return

    addressable = reasons[reasons["Addressable"]]
    organisational = reasons[reasons["Reason_Class"] == "Organisational"]
    total_hours = float(reasons["Hours"].sum())

    cards = st.columns(4)
    with cards[0]:
        ui.kpi_card("Reason-coded delay", f"{format_number(total_hours)} h",
                    f"{format_number(len(dumper_delays))} logged events")
    with cards[1]:
        share = float(organisational["Hours"].sum()) / total_hours * 100 if total_hours else 0
        ui.kpi_card("Organisational", f"{share:.0f}%",
                    f"{format_number(organisational['Hours'].sum())} h of scheduling and manning",
                    tone="accent")
    with cards[2]:
        ui.kpi_card("Addressable total", f"{format_number(addressable['Hours'].sum())} h",
                    format_inr(float(addressable["Hours"].sum()) * filters.idle_cost_per_hour),
                    tone="good")
    with cards[3]:
        mechanical = reasons[reasons["Reason_Class"] == "Mechanical"]["Hours"].sum()
        ui.kpi_card("Mechanical", f"{format_number(mechanical)} h",
                    "breakdown and planned maintenance")

    st.markdown("")

    tab_pareto, tab_timing, tab_detail, tab_method = st.tabs(
        ["Ranked causes", "Timing fingerprint", "Event detail", "How causes are classified"]
    )

    with tab_pareto:
        left, right = st.columns([3, 2], gap="large")
        with left:
            st.plotly_chart(charts.reason_pareto(reasons))
        with right:
            st.plotly_chart(charts.reason_class_donut(reasons))

        st.markdown("#### Full reason table")
        display = reasons.copy()
        display["Cost"] = display["Hours"] * filters.idle_cost_per_hour
        columns = ["Reason", "Reason_Class", "Addressable", "Hours", "Share_Pct",
                   "Events", "Mean_Min", "Dumpers", "Cost", "Lever"]
        if "Hours_Cross_Check" in display.columns:
            columns.insert(5, "Hours_Cross_Check")
        st.dataframe(
            display[columns], hide_index=True,
            column_config={
                "Reason_Class": "Class",
                "Addressable": st.column_config.CheckboxColumn("Addressable"),
                "Hours": st.column_config.NumberColumn(format="%.0f"),
                "Hours_Cross_Check": st.column_config.NumberColumn(
                    "Cross-check (h)", format="%.0f",
                    help="Same reason from the independent Status and Sub-Status report.",
                ),
                "Share_Pct": st.column_config.ProgressColumn(
                    "Share", format="%.1f%%", min_value=0, max_value=60
                ),
                "Events": st.column_config.NumberColumn(format="%d"),
                "Mean_Min": st.column_config.NumberColumn("Mean (min)", format="%.1f"),
                "Cost": st.column_config.NumberColumn(format="₹%.0f"),
            },
        )

        top_reason = reasons.iloc[0]
        second = reasons[reasons["Addressable"]].iloc[0] if not addressable.empty else None
        insight = (
            f"The largest single cause is <b>{top_reason['Reason']}</b> at "
            f"{format_number(top_reason['Hours'])} hours "
            f"({top_reason['Share_Pct']:.0f}% of all delay)."
        )
        if second is not None:
            insight += (
                f" The largest <b>addressable</b> cause is <b>{second['Reason']}</b> at "
                f"{format_number(second['Hours'])} hours across {int(second['Dumpers'])} "
                f"dumpers, averaging {second['Mean_Min']:.0f} minutes per event."
            )
        ui.note(insight)

    with tab_timing:
        if dumper_delays.empty:
            st.info("No dumper delay events to profile.")
        else:
            choices = list(reasons["Reason"].head(10))
            selected = st.multiselect(
                "Reasons to profile", list(reasons["Reason"]),
                default=[r for r in choices[:5]],
            )
            if selected:
                st.plotly_chart(
                    charts.reason_hour_heatmap(dumper_delays, selected)
                )

            st.markdown("#### Reason mix by shift")
            mix = (
                dumper_delays.pivot_table(
                    index="Shift", columns="Reason_Class", values="Delay_Min", aggfunc="sum"
                ).div(60)
            )
            mix.index = [safe_shift_label(i) for i in mix.index]
            st.dataframe(
                mix.round(0),
                column_config={c: st.column_config.NumberColumn(format="%.0f") for c in mix.columns},
            )

            ui.note(
                "Reasons that spike sharply at 06:00, 14:00 and 22:00 are changeover losses. "
                "Reasons spread flat across the shift are process losses. The two need "
                "completely different fixes, which is why the timing view matters."
            )

    with tab_detail:
        if dumper_delays.empty:
            st.info("No dumper delay events in this selection.")
        else:
            reason_filter = st.selectbox(
                "Reason", ["All"] + list(reasons["Reason"]), index=0
            )
            scope = dumper_delays
            if reason_filter != "All":
                scope = scope[scope["Reason"] == reason_filter]

            st.caption(f"{len(scope):,} events · {scope['Delay_Min'].sum() / 60:,.0f} hours")
            st.dataframe(
                scope.nlargest(300, "Delay_Min")[
                    ["Shift_Date", "Shift", "Equipment_ID", "Reason", "Reason_Class",
                     "Start_Timestamp", "Delay_Min"]
                ],
                hide_index=True, height=460,
                column_config={
                    "Shift_Date": st.column_config.DateColumn("Date", format="DD MMM"),
                    "Equipment_ID": "Dumper",
                    "Reason_Class": "Class",
                    "Start_Timestamp": st.column_config.DatetimeColumn(
                        "Started", format="DD MMM HH:mm"
                    ),
                    "Delay_Min": st.column_config.NumberColumn("Minutes", format="%.1f"),
                },
            )

    with tab_method:
        st.markdown(
            """
            #### The four classes

            | Class | Meaning | Can the mine change it? |
            | --- | --- | --- |
            | **Organisational** | Shift change, staggering of breaks, operator relief, marching between faces | Yes, by rescheduling |
            | **Operational** | Hopper congestion, refuelling, road conditions, queueing | Yes, by dispatch and maintenance of roads |
            | **Mechanical** | Breakdowns, planned daily service, inspections | Not by scheduling |
            | **External** | Blasting, weather, statutory stoppages | No |

            Classification is rule-based and lives in `config.REASON_RULES`, so every
            assignment is auditable and can be corrected by editing one list. The rules are
            ordered from specific to general, which is why *Daily Service* is classed as
            planned maintenance rather than being swept up by the generic *service* rule.
            """
        )
        st.info(
            "**A deliberate choice about breaks.** Tea, breakfast and toilet breaks appear as "
            "addressable, but the lever attached to them is *staggering*, never shortening. "
            "The loss being targeted is the whole fleet stopping at the same moment, which "
            "leaves shovels with nothing to load. Operators keep their full entitlement."
        )

        unclassified = reasons[reasons["Reason_Class"] == "Unclassified"]
        if not unclassified.empty:
            st.markdown("#### Reasons not yet classified")
            st.caption(
                "Add a matching rule to `config.REASON_RULES` to fold these into a class."
            )
            st.dataframe(
                unclassified[["Reason", "Hours", "Events"]], hide_index=True,
                column_config={"Hours": st.column_config.NumberColumn(format="%.0f")},
            )
        else:
            st.success("Every reason in the current selection is classified.")


main()
