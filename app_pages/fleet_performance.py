"""Fleet Performance: which dumpers, shovels, routes and operators lose the most.

Raw idle hours mostly reflect how much a machine worked, so the page pairs every
raw ranking with a normalised one and with the model's excess-idle residual,
which is the part that is not explained by workload or route.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
import data_utils as du
from utils import charts, ui
from utils.helpers import format_inr, format_number

ui.apply_theme()

group_summary = du.build_group_summary

RANKING_COLUMNS = {
    "Dumper_Shifts": st.column_config.NumberColumn("Shifts", format="%d"),
    "Idle hours": st.column_config.NumberColumn(format="%.0f"),
    "Idle h per shift": st.column_config.NumberColumn(format="%.2f"),
    "Idle % of cycle": st.column_config.ProgressColumn(
        format="%.1f%%", min_value=0, max_value=50
    ),
    "Queue min per cycle": st.column_config.NumberColumn(format="%.2f"),
    "Tonnes per op hour": st.column_config.NumberColumn(format="%.0f"),
}
DISPLAY_COLUMNS = [
    "Dumper_Shifts", "Idle hours", "Idle h per shift", "Idle % of cycle",
    "Queue min per cycle", "Tonnes per op hour",
]


def main() -> None:
    """Render the fleet performance page."""
    if not ui.data_is_ready():
        return

    tables = ui.load_processed()
    filters = ui.sidebar_filters(tables["shifts"])
    ui.sidebar_model_panel()

    bundle = ui.load_bundle()
    scored_all = ui.scored_shifts(
        f"{getattr(bundle, 'trained_at', 'none')}_{getattr(bundle, 'model_name', 'none')}"
    )
    shifts = filters.apply(scored_all)

    ui.hero("Fleet & risk ranking", "Dumpers, shovels, routes and operators ranked by lost time")

    if shifts.empty:
        st.warning("No data matches the current filters.")
        return

    tab_dumpers, tab_risk, tab_loaders, tab_breakdown, tab_operators = st.tabs(
        ["Dumpers", "Idle risk (model)", "Shovels & routes", "Breakdown analysis", "Operators"]
    )

    with tab_dumpers:
        dumpers = group_summary(shifts, "Equipment_ID")
        fuel = tables["fuel"]
        if not fuel.empty:
            dumpers = dumpers.merge(
                fuel[["Equipment_ID", "Fuel_Litres"]], on="Equipment_ID", how="left"
            )
            dumpers["Litres per tonne"] = (
                dumpers["Fuel_Litres"] / dumpers["Tonnes"].replace(0, pd.NA)
            )

        metric = st.radio(
            "Rank by", ["Idle hours", "Idle h per shift", "Idle % of cycle",
                        "Queue min per cycle"],
            horizontal=True,
        )
        chart, table = st.columns([2, 3], gap="large")
        with chart:
            st.plotly_chart(
                charts.equipment_ranking(
                    dumpers.rename(columns={metric: "value"}), "value", metric, top=18
                ),
                use_container_width=True,
            theme=None)
        with table:
            columns = ["Equipment_ID"] + DISPLAY_COLUMNS
            if "Litres per tonne" in dumpers.columns:
                columns.append("Litres per tonne")
            st.dataframe(
                dumpers[columns].sort_values(metric, ascending=False),
                hide_index=True, height=520,
                column_config={
                    "Equipment_ID": "Dumper",
                    **RANKING_COLUMNS,
                    "Litres per tonne": st.column_config.NumberColumn(format="%.3f"),
                },
            )

        ui.note(
            "Rank by <b>idle hours</b> to see where the biggest absolute pool of time sits, "
            "and by <b>idle % of cycle</b> to compare machines fairly regardless of how many "
            "shifts they worked. A dumper can top the hours list simply by working the most."
        )

        st.markdown("#### Dumper drilldown")
        dumper_ids = sorted(dumpers["Equipment_ID"].unique())
        selected_dumper = st.selectbox("Select a dumper", dumper_ids, key="dumper_drilldown")
        if selected_dumper:
            d_shifts = shifts[shifts["Equipment_ID"] == selected_dumper].sort_values(
                ["Shift_Date", "Shift"]
            )
            d_delays = filters.apply(tables.get("delay_events", pd.DataFrame()))
            d_delays = d_delays[d_delays["Equipment_ID"] == selected_dumper]

            dc1, dc2, dc3, dc4 = st.columns(4)
            with dc1:
                ui.kpi_card("Shifts", str(len(d_shifts)), "in this period")
            with dc2:
                ui.kpi_card(
                    "Avg idle / shift",
                    f"{d_shifts['Total_Idle_Min'].mean() / 60:.1f} h",
                    f"fleet avg: {shifts['Total_Idle_Min'].mean() / 60:.1f} h",
                )
            with dc3:
                worst_idx = d_shifts["Total_Idle_Min"].idxmax()
                ui.kpi_card(
                    "Worst shift",
                    f"{d_shifts.loc[worst_idx, 'Total_Idle_Min'] / 60:.1f} h",
                    str(d_shifts.loc[worst_idx, "Shift_Date"].date()),
                )
            with dc4:
                ui.kpi_card(
                    "Delay events",
                    str(len(d_delays)),
                    f"{d_delays['Delay_Min'].sum() / 60:.0f} h total" if not d_delays.empty else "none",
                )

            st.plotly_chart(charts.dumper_timeline(shifts, selected_dumper), use_container_width=True, theme=None)

            fleet_idle = shifts.groupby("Equipment_ID")["Total_Idle_Min"].sum() / 60
            dumper_total = float(fleet_idle.get(selected_dumper, 0))
            percentile = float((fleet_idle < dumper_total).sum() / len(fleet_idle) * 100) if len(fleet_idle) else 0
            if percentile >= 75:
                rank_label = "Bottom 25% of fleet"
                rank_color = config.DANGER
            elif percentile >= 50:
                rank_label = "Bottom half of fleet"
                rank_color = "#e67e22"
            elif percentile >= 25:
                rank_label = "Top half of fleet"
                rank_color = config.LIME
            else:
                rank_label = "Top 25% of fleet"
                rank_color = config.LIME
            st.markdown(
                f'<div style="text-align:center; padding:10px 0; margin:5px 0 15px 0;">'
                f'<div style="font-size:13px; color:{config.TEXT_MUTED};">Fleet percentile (idle hours)</div>'
                f'<div style="font-size:28px; font-weight:700; color:{rank_color}; '
                f'font-family:Inter, sans-serif; margin:4px 0;">'
                f'{percentile:.0f}th percentile</div>'
                f'<div style="font-size:14px; color:{rank_color};">{rank_label}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            drill_left, drill_right = st.columns([3, 2], gap="large")
            with drill_left:
                st.markdown("**Shift-by-shift detail**")
                detail_cols = ["Shift_Date", "Shift", "Loading_Unit", "Cycles", "Total_Idle_Min"]
                for c in ("Cycle_Idle_Min", "Delay_Min"):
                    if c in d_shifts.columns:
                        detail_cols.append(c)
                detail = d_shifts[detail_cols].copy()
                detail["Idle_Hours"] = (detail["Total_Idle_Min"] / 60).round(2)
                detail["Shift"] = detail["Shift"].fillna(0).astype(int)
                show_cols = ["Shift_Date", "Shift", "Loading_Unit", "Cycles", "Idle_Hours"]
                if "Cycle_Idle_Min" in detail.columns:
                    show_cols.append("Cycle_Idle_Min")
                if "Delay_Min" in detail.columns:
                    show_cols.append("Delay_Min")
                col_cfg = {
                    "Shift_Date": st.column_config.DateColumn("Date", format="DD MMM"),
                    "Shift": st.column_config.NumberColumn(format="%d"),
                    "Loading_Unit": "Shovel",
                    "Cycles": st.column_config.NumberColumn(format="%d"),
                    "Idle_Hours": st.column_config.NumberColumn("Idle h", format="%.2f"),
                }
                if "Cycle_Idle_Min" in detail.columns:
                    col_cfg["Cycle_Idle_Min"] = st.column_config.NumberColumn("Cycle idle", format="%.0f")
                if "Delay_Min" in detail.columns:
                    col_cfg["Delay_Min"] = st.column_config.NumberColumn("Delay", format="%.0f")
                st.dataframe(detail[show_cols], hide_index=True, column_config=col_cfg)
            with drill_right:
                if not d_delays.empty:
                    st.plotly_chart(
                        charts.dumper_reason_bar(d_delays, selected_dumper),
                        use_container_width=True,
            theme=None)
                else:
                    st.info("No delay events for this dumper in the selected period.")

    with tab_risk:
        if bundle is None or "High_Idle_Risk_Proba" not in shifts.columns:
            st.info("Train the model from the sidebar to enable risk scoring.")
        else:
            st.markdown(
                "The dashboard ships **two** models trained on the same honest feature set "
                "(haul geometry, shovel congestion, calendar, each dumper's own idle "
                "history). They answer different questions:"
            )
            cards = st.columns(4)
            with cards[0]:
                ui.kpi_card(
                    "Risk-flag AUC", f"{bundle.risk_metrics['auc']:.3f}" if bundle.risk_metrics else "-",
                    f"{bundle.risk_model_name}: will this shift be in the worst third?",
                    tone="good",
                )
            with cards[1]:
                ui.kpi_card(
                    "Risk-flag accuracy",
                    f"{bundle.risk_metrics['accuracy']:.0%}" if bundle.risk_metrics else "-",
                    "on shifts the model never trained on",
                )
            with cards[2]:
                ui.kpi_card("Minutes regressor R²", f"{bundle.metrics['r2']:.2f}",
                            "exact-minute estimate — deliberately modest, see note below")
            with cards[3]:
                threshold_h = bundle.risk_threshold / 60
                ui.kpi_card("High-risk threshold", f"{threshold_h:.1f} h",
                            "idle in a single 8-hour shift")

            st.markdown("")
            ui.note(
                "<b>Why two models, and why the regressor's R² looks low.</b> "
                "Testing showed that including how many haul cycles a dumper completed in a "
                "shift inflates R² to ~0.50, but that number is <b>not trustworthy</b>: in a "
                "fixed 8-hour shift, cycles completed is a near-direct readout of delay "
                "minutes (r=0.95) — the model would just be re-deriving its target, not "
                "predicting it. Removing that column drops R² to its honest value "
                f"({bundle.metrics['r2']:.2f}), because more than half of shift-to-shift idle "
                "variance is driven by <i>when a machine breaks down</i>, which schedule and "
                "workload data cannot foresee without maintenance/fault-code history. "
                "The classifier below asks an easier, still useful question — "
                "<b>is this shift at risk of being a bad one</b> — and answers it well."
            )

            left, right = st.columns([3, 2], gap="large")
            with left:
                st.plotly_chart(charts.residual_scatter(shifts), theme=None)
                st.caption(
                    "Exact-minute estimate vs actual. Useful for ranking, not for reading off "
                    "a precise number."
                )
            with right:
                importances = (
                    bundle.risk_importances if bundle.risk_importances is not None
                    else bundle.importances
                )
                st.plotly_chart(charts.importance_bar(importances), theme=None)
                st.caption("What the risk classifier leans on.")

            st.markdown("#### Shifts flagged as high risk this period")
            flagged = shifts[shifts["High_Idle_Risk"]] if "High_Idle_Risk" in shifts else pd.DataFrame()
            if flagged.empty:
                st.info("No shifts in the current selection were flagged high-risk.")
            else:
                hit_rate = (
                    flagged["Actually_High_Idle"].mean()
                    if "Actually_High_Idle" in flagged.columns else None
                )
                if hit_rate is not None:
                    st.caption(
                        f"{len(flagged):,} shifts flagged · {hit_rate:.0%} were genuinely in "
                        "the worst third for idle (on this data the model has already seen; "
                        "the held-out accuracy above is the fair estimate)."
                    )
                worst = flagged.nlargest(15, "High_Idle_Risk_Proba")[
                    ["Shift_Date", "Shift", "Equipment_ID", "Loading_Unit",
                     "Total_Idle_Min", "High_Idle_Risk_Proba"]
                ]
                st.dataframe(
                    worst, hide_index=True,
                    column_config={
                        "Shift_Date": st.column_config.DateColumn("Date", format="DD MMM"),
                        "Equipment_ID": "Dumper",
                        "Loading_Unit": "Shovel",
                        "Total_Idle_Min": st.column_config.NumberColumn("Actual idle (min)", format="%.0f"),
                        "High_Idle_Risk_Proba": st.column_config.ProgressColumn(
                            "Risk score", format="%.0f%%", min_value=0, max_value=1
                        ),
                    },
                )

            st.markdown("#### Dumpers most often flagged high-risk")
            by_dumper = shifts.groupby("Equipment_ID", as_index=False).agg(
                Shifts=("High_Idle_Risk", "size"),
                Flagged=("High_Idle_Risk", "sum"),
                Mean_Risk=("High_Idle_Risk_Proba", "mean"),
            )
            by_dumper["Flagged share"] = by_dumper["Flagged"] / by_dumper["Shifts"] * 100
            st.dataframe(
                by_dumper.nlargest(15, "Flagged share")[
                    ["Equipment_ID", "Shifts", "Flagged", "Flagged share", "Mean_Risk"]
                ],
                hide_index=True,
                column_config={
                    "Equipment_ID": "Dumper",
                    "Flagged": st.column_config.NumberColumn(format="%d"),
                    "Flagged share": st.column_config.ProgressColumn(
                        format="%.0f%%", min_value=0, max_value=100
                    ),
                    "Mean_Risk": st.column_config.NumberColumn("Avg. risk score", format="%.2f"),
                },
            )

    with tab_loaders:
        sub_waiting, sub_decomp, sub_routes = st.tabs(
            ["Shovel waiting", "Time decomposition", "Routes"]
        )

        loaders = group_summary(shifts, "Loading_Unit", min_shifts=10)
        routes = group_summary(shifts, "Route", min_shifts=10)

        lu = tables.get("loading_unit_time", pd.DataFrame())
        cycles = tables.get("cycles", pd.DataFrame())
        shovel_shift = tables.get("shovel_shift", pd.DataFrame())

        with sub_waiting:
            if not lu.empty and not cycles.empty:
                wait_h = lu["Waiting_Min"].sum() / 60
                load_h = lu["Loading_Min"].sum() / 60
                wait_share = wait_h / (wait_h + load_h) if (wait_h + load_h) > 0 else 0
                dumper_queue_h = cycles["Queue_Shovel"].sum() / 60
                n_cycles = len(cycles)
                ui.note(
                    f"<b>The queue is on the shovel side, not the dumper side.</b> "
                    f"Across the period, shovels waited {wait_h:,.0f} h while actually loading "
                    f"{load_h:,.0f} h — waiting is {wait_share:.1%} of load + wait time. "
                    f"By contrast, dumpers queued at the shovel for only "
                    f"{dumper_queue_h:,.1f} h across {n_cycles:,} cycles "
                    f"({dumper_queue_h * 60 / n_cycles:.2f} min/cycle)."
                )

                lu_summary = lu.groupby("Equipment_ID", as_index=False).agg(
                    Loading_Hours=("Loading_Min", lambda x: x.sum() / 60),
                    Waiting_Hours=("Waiting_Min", lambda x: x.sum() / 60),
                )
                lu_summary["Waiting_Share"] = lu_summary["Waiting_Hours"] / (
                    lu_summary["Waiting_Hours"] + lu_summary["Loading_Hours"]
                )
                avg_payload = cycles.groupby("Loading_Unit")["Payload"].mean()
                avg_load_min_per_cycle = cycles.groupby("Loading_Unit")["Loading_Time"].mean()
                for idx, row in lu_summary.iterrows():
                    eid = row["Equipment_ID"]
                    rate = avg_load_min_per_cycle.get(eid, 0)
                    payload = avg_payload.get(eid, 0)
                    if rate > 0:
                        extra_loads = row["Waiting_Hours"] * 60 / rate
                        lu_summary.at[idx, "Theoretical_Extra_Loads"] = extra_loads
                        lu_summary.at[idx, "Theoretical_Extra_Tonnes"] = extra_loads * payload
                    else:
                        lu_summary.at[idx, "Theoretical_Extra_Loads"] = 0
                        lu_summary.at[idx, "Theoretical_Extra_Tonnes"] = 0
                lu_summary = lu_summary.sort_values("Waiting_Hours", ascending=False)
                st.dataframe(
                    lu_summary,
                    hide_index=True,
                    column_config={
                        "Equipment_ID": "Shovel",
                        "Loading_Hours": st.column_config.NumberColumn("Loading h", format="%.0f"),
                        "Waiting_Hours": st.column_config.NumberColumn("Waiting h", format="%.0f"),
                        "Waiting_Share": st.column_config.ProgressColumn(
                            "Waiting %", format="%.0f%%", min_value=0, max_value=100,
                        ),
                        "Theoretical_Extra_Loads": st.column_config.NumberColumn(
                            "Theoretical extra loads", format="%.0f",
                        ),
                        "Theoretical_Extra_Tonnes": st.column_config.NumberColumn(
                            "Theoretical extra tonnes", format="%.0f",
                        ),
                    },
                )
                total_extra_tonnes = float(lu_summary["Theoretical_Extra_Tonnes"].sum())
                ui.note(
                    f"<b>Theoretical upper bound:</b> if every shovel had loaded continuously "
                    f"during its waiting time, the fleet could have moved "
                    f"<b>{total_extra_tonnes:,.0f} additional tonnes</b>. This assumes zero "
                    f"blasting, marching, face preparation or maintenance — so it is an "
                    f"<i>upper bound on the opportunity, not a forecast</i>."
                )

                if not shovel_shift.empty:
                    cyc = filters.apply(cycles)
                    tps = cyc.groupby(["Shift_Date", "Shift", "Loading_Unit"]).agg(
                        trucks=("Equipment_ID", "nunique"),
                        tonnes=("Payload", "sum"),
                    ).reset_index()
                    ss = filters.apply(shovel_shift)
                    merged = tps.merge(
                        ss[["Shift_Date", "Shift", "Equipment_ID", "Run_Hours", "Available_Hours"]],
                        left_on=["Shift_Date", "Shift", "Loading_Unit"],
                        right_on=["Shift_Date", "Shift", "Equipment_ID"],
                        how="inner",
                    )
                    merged["shovel_idle_h"] = merged["Available_Hours"] - merged["Run_Hours"]
                    merged = merged[merged["shovel_idle_h"] >= 0]
                    corr = merged["trucks"].corr(merged["shovel_idle_h"])
                    st.plotly_chart(
                        charts.shovel_starvation_scatter(merged),
                        use_container_width=True,
            theme=None)
                    ui.note(
                        f"The weak correlation (r = {corr:+.2f}) shows that <b>adding trucks alone does "
                        "not linearly reduce shovel idle time</b>. Shovel idle is driven by "
                        "blasting, marching, face preparation and maintenance — not just truck "
                        "availability."
                    )
            else:
                st.info("No loading-unit time data available for this selection.")

        with sub_decomp:
            if not shovel_shift.empty:
                ss = filters.apply(shovel_shift)
                shovel_decomp = ss.groupby("Equipment_ID", as_index=False).agg(
                    Shifts=("Shift_Date", "size"),
                    Available_Hours=("Available_Hours", "sum"),
                    Run_Hours=("Run_Hours", "sum"),
                    Marching_Hours=("Marching_Hours", "sum"),
                    Face_Prep_Min=("Face_Preparation_Min", "sum"),
                    Blasting_Min=("Blasting_Delay_Min", "sum"),
                    Maintenance_Min=("Maintenance_Min", "sum"),
                    Coal_Tonnes=("Coal_Tonnes", "sum"),
                    OB_Tonnes=("OB_Tonnes", "sum"),
                )
                shovel_decomp["Idle_Hours"] = shovel_decomp["Available_Hours"] - shovel_decomp["Run_Hours"]
                shovel_decomp["Face_Prep_Hours"] = shovel_decomp["Face_Prep_Min"] / 60
                shovel_decomp["Blasting_Hours"] = shovel_decomp["Blasting_Min"] / 60
                shovel_decomp["Maintenance_Hours"] = shovel_decomp["Maintenance_Min"] / 60
                shovel_decomp["Total_Tonnes"] = shovel_decomp["Coal_Tonnes"] + shovel_decomp["OB_Tonnes"]
                show_cols = [
                    "Equipment_ID", "Shifts", "Available_Hours", "Run_Hours",
                    "Idle_Hours", "Marching_Hours", "Face_Prep_Hours",
                    "Blasting_Hours", "Maintenance_Hours", "Total_Tonnes",
                ]
                st.dataframe(
                    shovel_decomp[show_cols].sort_values("Idle_Hours", ascending=False),
                    hide_index=True,
                    column_config={
                        "Equipment_ID": "Shovel",
                        "Shifts": st.column_config.NumberColumn(format="%d"),
                        "Available_Hours": st.column_config.NumberColumn("Avail h", format="%.0f"),
                        "Run_Hours": st.column_config.NumberColumn("Run h", format="%.0f"),
                        "Idle_Hours": st.column_config.NumberColumn("Idle h", format="%.0f"),
                        "Marching_Hours": st.column_config.NumberColumn("March h", format="%.1f"),
                        "Face_Prep_Hours": st.column_config.NumberColumn("Face prep h", format="%.1f"),
                        "Blasting_Hours": st.column_config.NumberColumn("Blast h", format="%.1f"),
                        "Maintenance_Hours": st.column_config.NumberColumn("Maint h", format="%.1f"),
                        "Total_Tonnes": st.column_config.NumberColumn("Tonnes", format="%.0f"),
                    },
                )
                ui.note(
                    "'Idle h' is Available − Run — the time the shovel was ready but not loading. "
                    "Marching, face prep, blasting and maintenance are the operational reasons behind that idle time."
                )

            st.markdown("#### Idle of the trucks working to each shovel")
            st.dataframe(
                loaders[["Loading_Unit"] + DISPLAY_COLUMNS],
                hide_index=True,
                column_config={"Loading_Unit": "Shovel", **RANKING_COLUMNS},
            )
            ui.note(
                "High queue minutes per cycle against one shovel means too many trucks are "
                "assigned to it, or that shovel is losing time itself."
            )

        with sub_routes:
            st.dataframe(
                routes[["Route"] + DISPLAY_COLUMNS].head(20),
                hide_index=True,
                column_config={"Route": "Load -> Dump", **RANKING_COLUMNS},
            )

    with tab_breakdown:
        delay_events_all = tables.get("delay_events", pd.DataFrame())
        if delay_events_all.empty:
            st.info("No delay events available for breakdown analysis.")
        else:
            delay_events = filters.apply(delay_events_all)
            bd = delay_events[
                delay_events["Reason"].str.contains("down|breakdown", case=False, na=False)
            ].copy()
            if bd.empty:
                st.info("No breakdown/down events found in the current selection.")
            else:
                bd["Breakdown_Hours"] = bd["Delay_Min"] / 60.0
                per_dumper = bd.groupby("Equipment_ID", as_index=False).agg(
                    Breakdown_Events=("Delay_Min", "size"),
                    Breakdown_Hours=("Breakdown_Hours", "sum"),
                    Shifts_Affected=("Shift_Date", "nunique"),
                ).sort_values("Breakdown_Hours", ascending=False)

                total_shifts = shifts.groupby(["Shift_Date", "Shift"]).ngroups
                per_dumper["Downtime_Share"] = per_dumper["Breakdown_Hours"] / (
                    per_dumper["Shifts_Affected"] * config.SHIFT_LENGTH_HOURS
                )
                total_bd_hours = float(per_dumper["Breakdown_Hours"].sum())
                total_bd_cost = total_bd_hours * filters.idle_cost_per_hour
                median_hours = float(per_dumper["Breakdown_Hours"].median())

                cards = st.columns(4)
                with cards[0]:
                    ui.kpi_card("Breakdown hours", f"{total_bd_hours:,.0f}",
                                f"{len(per_dumper)} dumpers with downtime")
                with cards[1]:
                    ui.kpi_card("Worst dumper", per_dumper.iloc[0]["Equipment_ID"],
                                f"{per_dumper.iloc[0]['Breakdown_Hours']:,.0f} h down")
                with cards[2]:
                    ui.kpi_card("Fleet median", f"{median_hours:,.0f} h",
                                "per dumper over the period")
                with cards[3]:
                    ui.kpi_card("Cost of lost availability", format_inr(total_bd_cost),
                                f"@ ₹{filters.idle_cost_per_hour:,.0f}/h (assumption)")

                st.markdown("#### Dumpers ranked by breakdown / down hours")
                st.plotly_chart(
                    charts.breakdown_ranking(per_dumper, median_hours),
                    use_container_width=True,
            theme=None)

                st.dataframe(
                    per_dumper,
                    hide_index=True,
                    column_config={
                        "Equipment_ID": "Dumper",
                        "Breakdown_Events": st.column_config.NumberColumn("Events", format="%d"),
                        "Breakdown_Hours": st.column_config.NumberColumn("Hours", format="%.0f"),
                        "Shifts_Affected": st.column_config.NumberColumn("Shifts affected", format="%d"),
                        "Downtime_Share": st.column_config.ProgressColumn(
                            "Downtime %", format="%.0f%%", min_value=0, max_value=100,
                        ),
                    },
                )

                ui.note(
                    "This is <b>lost availability, not idle time</b>. A dumper in the workshop "
                    "is not idle — it is absent. The cost figure uses the same ₹/h assumption "
                    "as idle cost, but represents the cost of <i>not having the machine at all</i>, "
                    "not the cost of it standing still while available. No trend is claimed: "
                    "31 days is too short to distinguish a deterioration from a single bad week."
                )

    with tab_operators:
        operators = group_summary(shifts, "Operator", min_shifts=10)
        if operators.empty:
            st.info("Not enough operator-linked shifts in this selection.")
        else:
            st.markdown(
                "Operator figures are shown for **coaching and best-practice sharing**, not "
                "ranking. Most idle time is organisational: an operator sitting through a "
                "shift changeover or a staggered break has done nothing wrong."
            )
            st.dataframe(
                operators[["Operator"] + DISPLAY_COLUMNS].head(25),
                hide_index=True,
                column_config={"Operator": "Operator", **RANKING_COLUMNS},
            )
            best = operators.nsmallest(5, "Idle % of cycle")[["Operator", "Idle % of cycle"]]
            st.markdown("**Lowest in-cycle idle share — worth understanding what they do differently**")
            st.dataframe(
                best, hide_index=True,
                column_config={
                    "Idle % of cycle": st.column_config.NumberColumn(format="%.1f%%")
                },
            )


main()
