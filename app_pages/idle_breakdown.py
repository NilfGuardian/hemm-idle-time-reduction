"""Idle Analytics: where the idle time actually goes.

Decomposes the haul cycle, then slices idle by shift, day and hour so the
structural patterns become visible.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
from utils import charts, ui
from utils.helpers import format_number

ui.apply_theme()


def bucket_table(shifts: pd.DataFrame) -> pd.DataFrame:
    """Minutes and share per cycle-time bucket for the current selection."""
    cycles = float(shifts["Cycles"].sum()) or 1.0
    total_min = float(shifts["Cycle_Min"].sum()) or 1.0
    rows = []
    for column, label in config.BUCKET_LABELS.items():
        if column not in shifts.columns:
            continue
        minutes = float(shifts[column].sum())
        if column in config.PRODUCTIVE_BUCKETS:
            group = "Productive"
        elif column in config.IDLE_BUCKETS:
            group = "Idle"
        else:
            group = "Semi-productive"
        rows.append(
            {
                "Bucket": label,
                "Type": group,
                "Min per cycle": minutes / cycles,
                "Total hours": minutes / 60,
                "Share of cycle": minutes / total_min * 100,
            }
        )
    frame = pd.DataFrame(rows)
    return frame.sort_values("Total hours", ascending=False).reset_index(drop=True)


def main() -> None:
    """Render the analytics page."""
    if not ui.data_is_ready():
        return

    tables = ui.load_processed()
    filters = ui.sidebar_filters(tables["shifts"])
    ui.sidebar_model_panel()

    shifts = filters.apply(tables["shifts"])
    hourly = filters.apply(tables["hourly"])
    idle_events = filters.apply(tables["idle_events"])

    ui.hero("Idle breakdown", "How a haul cycle is spent, and when idle time happens")

    if shifts.empty:
        st.warning("No data matches the current filters.")
        return

    summary = ui.summarise_idle(shifts, filters)

    tab_cycle, tab_when, tab_events, tab_quality = st.tabs(
        ["Cycle breakdown", "When it happens", "Idle events", "Definitions & data quality"]
    )

    with tab_cycle:
        left, right = st.columns([3, 2], gap="large")
        with left:
            ui.chart_tooltip("Cycle time breakdown", "<strong>Chart:</strong> Stacked bar showing how each dumper-shift's cycle time is decomposed. <strong>Legends:</strong> Loading, Travel (loaded + empty), Queue (at shovel/dump), Stopped (mid-trip), Spotting. <strong>Calculation:</strong> Each segment is the mean minutes per cycle from the FMS cycle report. <strong>Use:</strong> Identify which time components dominate the haul cycle.")
            st.plotly_chart(charts.cycle_breakdown_bar(shifts), theme=None)
        with right:
            frame = bucket_table(shifts)
            st.dataframe(
                frame, hide_index=True, height=420,
                column_config={
                    "Min per cycle": st.column_config.NumberColumn(format="%.2f"),
                    "Total hours": st.column_config.NumberColumn(format="%.0f"),
                    "Share of cycle": st.column_config.ProgressColumn(
                        format="%.1f%%", min_value=0, max_value=50
                    ),
                },
            )

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

        queue = float(shifts["Queue_Min"].sum()) / 60
        stopped = float(shifts["Stopped_Min"].sum()) / 60
        ui.note(
            f"Of the in-cycle idle, <b>{format_number(stopped)} hours</b> is a truck stopped "
            f"mid-trip and <b>{format_number(queue)} hours</b> is queueing at a shovel or dump. "
            "Stopped-in-trip time is the larger pool and is driven by congestion, road "
            "conditions and dispatch decisions rather than by operators."
        )

    with tab_when:
        top = st.columns(2, gap="large")
        with top[0]:
            ui.chart_tooltip("Idle by shift number", "<strong>Chart:</strong> Bar chart comparing idle hours across shift numbers (Shift 1, 2, 3). <strong>Calculation:</strong> <code>sum(Total_Idle_Min) / 60</code> grouped by <code>Shift</code> number. <strong>Use:</strong> Shows whether certain shifts consistently have more idle time (e.g. night shift changeover).")
            st.plotly_chart(charts.idle_by_shift(shifts), theme=None)
        with top[1]:
            ui.chart_tooltip("Idle trend over time", "<strong>Chart:</strong> Line chart of daily idle hours over the selected period. <strong>X-axis:</strong> Date. <strong>Y-axis:</strong> Total idle hours across the fleet. <strong>Use:</strong> Spot trends, spikes (breakdown days), or improvement over time.")
            st.plotly_chart(charts.idle_trend(shifts), theme=None)

        ui.chart_tooltip("Idle heatmap", "<strong>Chart:</strong> Heatmap of idle hours by day × shift number. <strong>X-axis:</strong> Shift number. <strong>Y-axis:</strong> Date. <strong>Colour:</strong> Idle hours (darker = more idle). <strong>Use:</strong> Identify patterns — are certain days or shifts consistently worse?")
        st.plotly_chart(charts.idle_heatmap(shifts), theme=None)

        with st.expander("Hour-of-day profile & weekday breakdown"):
            if not hourly.empty:
                ui.chart_tooltip("Hour-of-day profile", "<strong>Chart:</strong> Bar chart of idle hours by hour of day (0–23). <strong>Calculation:</strong> <code>sum(Idle_Min) / 60</code> grouped by hour from the idle events table. <strong>Use:</strong> Identifies peak idle hours — shift changeovers (06:00, 14:00, 22:00) often stand out.")
                st.plotly_chart(charts.hour_of_day_profile(hourly), theme=None)
                peak = (
                    hourly.groupby("Hour")["Idle_Min"].sum().div(60).sort_values(ascending=False)
                )
                changeover = [h for h in (6, 14, 22) if h in peak.head(6).index]
                if changeover:
                    ui.note(
                        f"The three shift-changeover hours (06:00, 14:00, 22:00) include "
                        f"{len(changeover)} of the six worst hours of the day. That is the "
                        "signature of the whole fleet stopping together rather than of "
                        "individual machines failing."
                    )

            weekday = (
                shifts.groupby("Day_Of_Week", as_index=False)["Total_Idle_Min"].mean()
            )
            order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            weekday["Day_Of_Week"] = pd.Categorical(weekday["Day_Of_Week"], order, ordered=True)
            weekday = weekday.sort_values("Day_Of_Week")
            weekday["Idle hours per dumper-shift"] = weekday["Total_Idle_Min"] / 60
            st.dataframe(
                weekday[["Day_Of_Week", "Idle hours per dumper-shift"]],
                hide_index=True,
                column_config={
                    "Day_Of_Week": "Day of week",
                    "Idle hours per dumper-shift": st.column_config.NumberColumn(format="%.2f"),
                },
            )

    with tab_events:
        if idle_events.empty:
            st.info("The Dumper Idle Time report is not loaded for this selection.")
        else:
            cards = st.columns(4)
            with cards[0]:
                ui.kpi_card("Idle events", format_number(len(idle_events)),
                            "each one a logged stand-still",
                            tooltip="<strong>What:</strong> Count of individual idle events from the Dumper Idle Time report. <strong>Calculation:</strong> Each row is one continuous stand-still period logged by the FMS with start time, duration, and engine status.")
            with cards[1]:
                ui.kpi_card("Mean event length",
                            f"{idle_events['Idle_Min'].mean():.0f} min", "per event",
                            tooltip="<strong>What:</strong> Average duration of a single idle event. <strong>Calculation:</strong> <code>mean(Idle_Min)</code> across all idle events in the filtered period. <strong>Use:</strong> Short mean = frequent stop-start (congestion); long mean = extended downtime (breakdowns, shift change).")
            with cards[2]:
                ui.kpi_card("Longest single event",
                            f"{idle_events['Idle_Min'].max() / 60:.1f} h", "in the period",
                            tone="accent",
                            tooltip="<strong>What:</strong> The longest continuous idle event recorded. <strong>Calculation:</strong> <code>max(Idle_Min) / 60</code> converted to hours. <strong>Use:</strong> An event lasting multiple hours likely indicates a breakdown or major incident, not normal queueing.")
            with cards[3]:
                engine_on = float(idle_events["Engine_Running"].mean() * 100)
                ui.kpi_card("Engine running", f"{engine_on:.0f}%",
                            "of idle time burns diesel", tone="accent",
                            tooltip="<strong>What:</strong> Percentage of idle time where the engine was running (burning diesel). <strong>Calculation:</strong> <code>mean(Engine_Running) × 100</code> across all idle events. <strong>Impact:</strong> Engine-on idle burns ~8 L/h of diesel. Engine-off idle (rare) does not.")

            st.markdown("#### Distribution of idle event length")
            buckets = pd.cut(
                idle_events["Idle_Min"],
                bins=[0, 5, 15, 30, 60, 120, 1e9],
                labels=["under 5 min", "5-15 min", "15-30 min", "30-60 min",
                        "1-2 h", "over 2 h"],
            )
            distribution = (
                idle_events.assign(Bucket=buckets)
                .groupby("Bucket", observed=True)
                .agg(Events=("Idle_Min", "size"), Hours=("Idle_Min", lambda s: s.sum() / 60))
                .reset_index()
            )
            distribution["Share of idle hours"] = (
                distribution["Hours"] / distribution["Hours"].sum() * 100
            )
            st.dataframe(
                distribution, hide_index=True,
                column_config={
                    "Bucket": "Event length",
                    "Events": st.column_config.NumberColumn(format="%d"),
                    "Hours": st.column_config.NumberColumn(format="%.0f"),
                    "Share of idle hours": st.column_config.ProgressColumn(
                        format="%.1f%%", min_value=0, max_value=60
                    ),
                },
            )
            ui.note(
                "Long events dominate the total. Chasing many short stops is far less "
                "valuable than removing the handful of long, scheduled stoppages."
            )

            st.markdown("#### Longest idle events")
            worst = idle_events.nlargest(15, "Idle_Min")[
                ["Equipment_ID", "Shift_Date", "Shift", "Start_Timestamp", "Idle_Min"]
            ].copy()
            worst["Idle hours"] = worst["Idle_Min"] / 60
            st.dataframe(
                worst.drop(columns=["Idle_Min"]), hide_index=True,
                column_config={
                    "Equipment_ID": "Dumper",
                    "Shift_Date": st.column_config.DateColumn("Shift date", format="DD MMM"),
                    "Start_Timestamp": st.column_config.DatetimeColumn(
                        "Started", format="DD MMM HH:mm"
                    ),
                    "Idle hours": st.column_config.NumberColumn(format="%.2f"),
                },
            )

    with tab_quality:
        st.markdown("#### How idle time is defined")
        st.markdown(
            """
            | Bucket | Counted as | Source column |
            | --- | --- | --- |
            | Travel empty / loaded | Productive | `EMPTY_STOPPED_TIME_NEW`, `HAULING_STOPPED_TIME_NEW` |
            | Loading, Dumping | Productive | `LOADING_TIME`, `DUMPING_TIME` |
            | Queue at shovel / dump | **Idle** | `WAITING_TIME_LU`, `WAITING_TIME_DUMP` |
            | Stopped empty / loaded / at face | **Idle** | `EMPTY_TRAVEL`, `LOAD_HAUL_TIME`, `EMPTY_STOPPED` |
            | Spotting | Semi-productive, excluded from idle | `SPOTTING_TIME1` |
            """
        )
        st.warning(
            "**The FMS column names are misleading and were corrected.** "
            "`EMPTY_STOPPED_TIME_NEW` and `HAULING_STOPPED_TIME_NEW` actually hold *travel* "
            "time, while `EMPTY_TRAVEL` and `LOAD_HAUL_TIME` hold *stopped* time. This was "
            "established by correlating each column against the per-dumper haul and empty "
            "distances in the Productivity TKPH report across 63 dumpers: the corrected "
            "reading gives 16.2 km/h empty and 15.7 km/h loaded, while the literal reading "
            "implies 179 km/h loaded, which is impossible."
        )

        st.markdown("#### Reconciliation and coverage")
        n_zero = int((shifts.get("Zero_Cycle_Shift", pd.Series(dtype=bool))).sum())
        n_working = len(shifts) - n_zero
        delay_hours_total = float(shifts["Delay_Min"].sum()) / 60.0
        cycle_hours_total = float(shifts["Cycle_Idle_Min"].sum()) / 60.0
        full_shift_down_hours = delay_hours_total - summary.delay_hours

        recon = pd.DataFrame(
            [
                {
                    "Item": "Dumper-shifts in view",
                    "Hours / Count": format_number(len(shifts)),
                    "Note": f"{n_working} with ≥1 cycle, {n_zero} with 0 cycles (fully down)",
                },
                {
                    "Item": "Fleet count",
                    "Hours / Count": format_number(shifts["Equipment_ID"].nunique()),
                    "Note": "70 in July incl. RD2KC6, which was Down all 744 h",
                },
                {
                    "Item": "Reason-coded dumper delay",
                    "Hours / Count": format_number(delay_hours_total),
                    "Note": f"{format_number(summary.delay_hours)} joinable to cycle-shifts + "
                            f"{format_number(full_shift_down_hours)} from fully-down shifts",
                },
                {
                    "Item": "In-cycle idle",
                    "Hours / Count": format_number(cycle_hours_total),
                    "Note": "queueing and stopped-in-trip from the Dumper Cycle Time report",
                },
            ]
        )
        st.dataframe(recon, hide_index=True)
        ui.note(
            "The previous release dropped every fully-down dumper-shift because it never "
            "appeared in the cycle report. Those 1,412 missing rows represented 10,458 h of "
            "mostly unaddressable <code>Down</code> time. They are now included so the fleet "
            "view is complete, but the recoverable scheduling opportunity is still the "
            "addressable slice below."
        )

        st.markdown("#### Dumper QSE report coverage")
        ds = tables.get("dumper_shift", pd.DataFrame())
        if not ds.empty:
            ds_dates = ds["Shift_Date"].nunique() if "Shift_Date" in ds.columns else 0
            ds_units = ds["Equipment_ID"].nunique() if "Equipment_ID" in ds.columns else 0
            st.warning(
                f"**Dumper QSE report covers only {ds_dates} of 31 days and {ds_units} of 70 "
                f"dumpers** (July 1–10 only). Fields merged from it — "
                f"`Run_Hours`, `Breakdown_Hours`, `Available_Hours`, `Canteen_Break_Min`, "
                f"`First_Load_Delay_Min`, `Lead_Distance_Km` — are ~65% null in the shift "
                f"master. Treat any breakdown-hours analysis based on these fields as "
                f"indicative, not comprehensive."
            )

        st.markdown("#### Cross-validation between two independent reports")
        cross = pd.DataFrame(
            [
                {
                    "Measure": "In-cycle idle (Dumper Cycle Time report)",
                    "Hours": summary.cycle_idle_hours,
                },
                {
                    "Measure": "Reason-coded delay (Delays and Downs report)",
                    "Hours": summary.delay_hours,
                },
                {
                    "Measure": "Logged stand-still (Dumper Idle Time report)",
                    "Hours": summary.measured_idle_hours,
                },
            ]
        )
        st.dataframe(
            cross, hide_index=True,
            column_config={"Hours": st.column_config.NumberColumn(format="%.0f")},
        )
        ui.note(
            "The three reports measure overlapping but not identical things, so they are "
            "kept separate rather than added together. In-cycle idle plus reason-coded "
            "delay is the headline total; the idle log is the independent sanity check."
        )

        if not idle_events.empty:
            measured = idle_events["Idle_Min"].sum() / 60.0
            measured_mean = idle_events["Idle_Min"].mean()
            cycle_idle = float(shifts["Cycle_Idle_Min"].sum()) / 60.0
            cycle_mean = float(shifts["Cycle_Idle_Min"].sum()) / max(len(shifts), 1)
            if "Measured_Idle_Min" in shifts.columns:
                corr = shifts["Measured_Idle_Min"].corr(shifts["Cycle_Idle_Min"])
                corr_text = f"r={corr:+.2f}"
            else:
                corr_text = "weakly"
            st.warning(
                "**The two idle measures disagree.** Logged stand-still events average "
                f"{measured_mean:.0f} min per event ({measured:,.0f} h total), while the "
                f"cycle buckets record {cycle_mean:.0f} min of in-cycle idle per shift "
                f"({cycle_idle:,.0f} h total). In the shift master they correlate at {corr_text}, "
                "so they are not two noisy reads of the same physical quantity; treat them "
                "as independent reports with different definitions."
            )

        provenance = ui.load_provenance()
        if provenance:
            st.markdown("#### Source files")
            st.json(provenance.get("files_used", {}), expanded=False)
            coverage = provenance.get("coverage", {})
            st.caption(
                f"Coverage: {coverage.get('first_shift_date')} to "
                f"{coverage.get('last_shift_date')} · {coverage.get('dumpers')} dumpers · "
                f"{coverage.get('dumper_shifts', '?')} dumper-shifts "
                f"({coverage.get('zero_cycle_shifts', 0)} with 0 cycles) · "
                f"built {provenance.get('generated_at')}"
            )


main()
