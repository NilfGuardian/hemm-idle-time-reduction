"""Simulation: what happens if each idle driver is reduced.

Every assumption is a slider. Nothing is hidden, so a reviewer can dispute any
number and immediately see how much the conclusion depends on it.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
import data_utils as du
from utils import charts, ui
from utils.helpers import format_inr, format_number

ui.apply_theme()


@st.cache_data(show_spinner=False)
def reason_master(delay_events: pd.DataFrame) -> pd.DataFrame:
    """Rebuild the reason ranking for the filtered subset."""
    if delay_events is None or delay_events.empty:
        return pd.DataFrame()
    return du.build_reason_master(delay_events)


def main() -> None:
    """Render the simulation page."""
    if not ui.data_is_ready():
        return

    tables = ui.load_processed()
    filters = ui.sidebar_filters(tables["shifts"])
    ui.sidebar_model_panel()

    shifts = filters.apply(tables["shifts"])
    delay_events = filters.apply(tables["delay_events"])
    reasons = reason_master(delay_events)

    ui.hero("Scenario simulator", "Set your own assumptions and see what the fleet recovers")

    if shifts.empty or reasons.empty:
        st.warning("No data matches the current filters.")
        return

    summary = ui.summarise_idle(shifts, filters)
    days = max(summary.days, 1)

    st.markdown("#### 1. Choose how far each driver can realistically be reduced")
    st.caption(
        "Defaults come from `config.IDLE_LEVERS` and are conservative. Break time is "
        "staggered, never shortened."
    )

    addressable = reasons[reasons["Addressable"] & (reasons["Hours"] > 1)].head(8)
    if addressable.empty:
        st.info("No addressable reasons available to simulate.")
        return

    rows: list[dict[str, object]] = []
    columns = st.columns(2, gap="large")
    for index, (_, reason) in enumerate(addressable.iterrows()):
        lever = config.IDLE_LEVERS.get(str(reason["Reason"]), {})
        default = int(str(lever.get("realistic_reduction", "20%")).rstrip("%"))
        with columns[index % 2]:
            with st.container(border=True):
                st.markdown(
                    f"**{reason['Reason']}** &nbsp;·&nbsp; "
                    f"{format_number(reason['Hours'])} h lost"
                )
                if lever.get("lever"):
                    st.caption(f"Lever: {lever['lever']}")
                reduction = st.slider(
                    "Reduction", 0, 80, default, 5,
                    key=f"reduce_{reason['Reason']}",
                    format="%d%%", label_visibility="collapsed",
                )
        hours_saved = float(reason["Hours"]) * reduction / 100
        rows.append(
            {
                "Reason": reason["Reason"],
                "Lever": lever.get("lever", "Local scheduling change"),
                "Hours lost": float(reason["Hours"]),
                "Reduction %": reduction,
                "Hours saved": hours_saved,
            }
        )

    plan = pd.DataFrame(rows)
    plan = plan[plan["Hours saved"] > 0]

    st.divider()
    st.markdown("#### 2. Result")

    if plan.empty:
        st.info("Set at least one reduction above zero to see the impact.")
        return

    hours_saved = float(plan["Hours saved"].sum())
    value = hours_saved * filters.idle_cost_per_hour
    fuel_saved = hours_saved * filters.idle_fuel_burn
    fuel_value = fuel_saved * filters.diesel_price
    extra_tonnes = hours_saved * summary.tonnes_per_operating_hour
    new_total = summary.total_idle_hours - hours_saved
    per_shift_before = summary.idle_hours_per_dumper_shift
    per_shift_after = (
        (summary.total_idle_hours - hours_saved) / summary.dumper_shifts
        if summary.dumper_shifts else 0
    )

    cards = st.columns(5)
    with cards[0]:
        ui.kpi_card("Idle recovered", f"{format_number(hours_saved)} h",
                    f"{hours_saved / summary.total_idle_hours * 100:.1f}% of current idle",
                    tone="good")
    with cards[1]:
        ui.kpi_card("Value in period", format_inr(value),
                    f"{format_inr(value / days)} per day", tone="good")
    with cards[2]:
        ui.kpi_card("Annualised", format_inr(value * 365 / days),
                    "extrapolated from this period", tone="good")
    with cards[3]:
        ui.kpi_card("Diesel saved", f"{format_number(fuel_saved)} L",
                    f"{format_inr(fuel_value)} of fuel")
    with cards[4]:
        ui.kpi_card("Extra material", f"{format_number(extra_tonnes)} t",
                    f"at {summary.tonnes_per_operating_hour:,.0f} t per operating hour")

    st.markdown("")
    st.plotly_chart(
        charts.savings_waterfall(plan.to_dict("records"), summary.total_idle_hours),
        use_container_width=True,
    )

    left, right = st.columns([3, 2], gap="large")
    with left:
        display = plan.copy()
        display["Value"] = display["Hours saved"] * filters.idle_cost_per_hour
        display["Hours saved per day"] = display["Hours saved"] / days
        st.dataframe(
            display[["Reason", "Lever", "Hours lost", "Reduction %", "Hours saved",
                     "Hours saved per day", "Value"]],
            hide_index=True,
            column_config={
                "Hours lost": st.column_config.NumberColumn(format="%.0f"),
                "Reduction %": st.column_config.NumberColumn(format="%d%%"),
                "Hours saved": st.column_config.NumberColumn(format="%.0f"),
                "Hours saved per day": st.column_config.NumberColumn(format="%.1f"),
                "Value": st.column_config.NumberColumn(format="₹%.0f"),
            },
        )
    with right:
        st.markdown("**Before and after, per dumper-shift**")
        comparison = pd.DataFrame(
            {
                "State": ["Now", "After changes"],
                "Idle hours per dumper-shift": [per_shift_before, per_shift_after],
                "Productive share of shift": [
                    (config.SHIFT_LENGTH_HOURS - per_shift_before)
                    / config.SHIFT_LENGTH_HOURS * 100,
                    (config.SHIFT_LENGTH_HOURS - per_shift_after)
                    / config.SHIFT_LENGTH_HOURS * 100,
                ],
            }
        )
        st.dataframe(
            comparison, hide_index=True,
            column_config={
                "Idle hours per dumper-shift": st.column_config.NumberColumn(format="%.2f"),
                "Productive share of shift": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
        st.metric(
            "Total fleet idle after changes",
            f"{new_total:,.0f} h",
            f"-{hours_saved:,.0f} h",
            delta_color="inverse",
        )

    st.divider()
    st.markdown("#### 3. Sensitivity to the cost assumption")
    st.caption(
        "The idle cost per hour is the one number in this analysis that does not come from "
        "the FMS. This table shows the plan's value across a plausible range."
    )
    rates = [2500, 5000, 7500, 10000, 12500]
    sensitivity = pd.DataFrame(
        {
            "Idle cost (₹/h)": rates,
            "Value in period": [hours_saved * rate for rate in rates],
            "Annualised value": [hours_saved * rate * 365 / days for rate in rates],
        }
    )
    st.dataframe(
        sensitivity, hide_index=True,
        column_config={
            "Idle cost (₹/h)": st.column_config.NumberColumn(format="₹%d"),
            "Value in period": st.column_config.NumberColumn(format="₹%.0f"),
            "Annualised value": st.column_config.NumberColumn(format="₹%.0f"),
        },
    )

    ui.note(
        "The fuel saving and the extra tonnes are derived from measured FMS data, so they "
        "hold regardless of the cost-per-hour assumption. Only the rupee figure moves."
    )

    st.session_state["simulation_plan"] = plan
    st.session_state["simulation_totals"] = {
        "hours_saved": hours_saved,
        "value": value,
        "annualised": value * 365 / days,
        "fuel_litres": fuel_saved,
        "extra_tonnes": extra_tonnes,
    }

    st.divider()
    st.markdown("#### 4. Save and compare scenarios")
    col_save, col_clear = st.columns(2)
    with col_save:
        if st.button("Save as baseline", use_container_width=True):
            st.session_state["baseline_plan"] = plan.copy()
            st.session_state["baseline_totals"] = dict(st.session_state["simulation_totals"])
            st.session_state["baseline_filters"] = {
                "start": filters.start,
                "end": filters.end,
                "sites": list(filters.sites),
            }
            st.success("Baseline saved! Adjust the sliders above and compare.")
    with col_clear:
        if st.button("Clear baseline", use_container_width=True):
            st.session_state.pop("baseline_plan", None)
            st.session_state.pop("baseline_totals", None)
            st.session_state.pop("baseline_filters", None)
            st.rerun()

    baseline = st.session_state.get("baseline_plan")
    baseline_totals = st.session_state.get("baseline_totals")
    if baseline is not None and baseline_totals is not None:
        st.markdown("##### Baseline vs current scenario")
        b_hours = baseline_totals["hours_saved"]
        b_value = baseline_totals["value"]
        b_fuel = baseline_totals["fuel_litres"]
        b_tonnes = baseline_totals["extra_tonnes"]

        comp = pd.DataFrame({
            "Metric": [
                "Idle recovered (h)", "Value (₹)", "Annualised (₹)",
                "Diesel saved (L)", "Extra tonnes",
                "Idle h per dumper-shift (after)",
            ],
            "Baseline": [
                f"{b_hours:,.0f}", format_inr(b_value), format_inr(baseline_totals["annualised"]),
                f"{b_fuel:,.0f}", f"{b_tonnes:,.0f}",
                f"{(summary.total_idle_hours - b_hours) / summary.dumper_shifts:.2f}",
            ],
            "Current": [
                f"{hours_saved:,.0f}", format_inr(value), format_inr(value * 365 / days),
                f"{fuel_saved:,.0f}", f"{extra_tonnes:,.0f}",
                f"{per_shift_after:.2f}",
            ],
            "Delta": [
                f"{hours_saved - b_hours:+,.0f}",
                f"₹{value - b_value:+,.0f}",
                f"₹{(value * 365 / days) - baseline_totals['annualised']:+,.0f}",
                f"{fuel_saved - b_fuel:+,.0f}",
                f"{extra_tonnes - b_tonnes:+,.0f}",
                f"{per_shift_after - ((summary.total_idle_hours - b_hours) / summary.dumper_shifts):+.2f}",
            ],
        })
        st.dataframe(comp, hide_index=True)

        merged = baseline[["Reason", "Hours saved"]].rename(
            columns={"Hours saved": "Baseline h"}
        ).merge(
            plan[["Reason", "Hours saved"]].rename(columns={"Hours saved": "Current h"}),
            on="Reason", how="outer",
        ).fillna(0)
        merged["Delta h"] = merged["Current h"] - merged["Baseline h"]
        st.dataframe(
            merged, hide_index=True,
            column_config={
                "Baseline h": st.column_config.NumberColumn(format="%.0f"),
                "Current h": st.column_config.NumberColumn(format="%.0f"),
                "Delta h": st.column_config.NumberColumn(format="%.0f"),
            },
        )


main()
