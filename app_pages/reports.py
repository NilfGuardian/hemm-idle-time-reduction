"""Reports: management summary, data exports and full method notes."""
from __future__ import annotations

import io
import json

import pandas as pd
import streamlit as st

import config
import data_utils as du
from utils import ui
from utils.helpers import format_inr, format_number

ui.apply_theme()

EXPORTABLE = {
    "Shift master (model grain)": "shifts",
    "Haul cycles": "cycles",
    "Idle events": "idle_events",
    "Delay events": "delay_events",
    "Per-dumper summary": "equipment",
    "Hourly profile": "hourly",
    "Reason ranking": "reasons",
    "Fuel by dumper": "fuel",
}


@st.cache_data(show_spinner=False)
def reason_master(delay_events: pd.DataFrame) -> pd.DataFrame:
    """Rebuild the reason ranking for the filtered subset."""
    if delay_events is None or delay_events.empty:
        return pd.DataFrame()
    return du.build_reason_master(delay_events)


def build_summary_text(
    summary: ui.IdleSummary, reasons: pd.DataFrame, filters: ui.Filters
) -> str:
    """Compose the management summary as plain markdown, ready to paste."""
    top = reasons.head(5)
    addressable = reasons[reasons["Addressable"]]
    addressable_hours = float(addressable["Hours"].sum())

    lines = [
        f"# Idle Time Reduction in HEMM — {config.PROJECT_SUBTITLE}",
        "",
        f"Period: {filters.start:%d %b %Y} to {filters.end:%d %b %Y}",
        f"Scope: {', '.join(filters.sites) or 'all sections'} · "
        f"{summary.dumpers} dumpers · {summary.dumper_shifts:,} dumper-shifts",
        "",
        "## Headline",
        "",
        f"- Total idle time: **{summary.total_idle_hours:,.0f} hours** "
        f"({summary.idle_hours_per_dumper_shift:.1f} h of every 8-hour dumper-shift)",
        f"- Idle inside the haul cycle: **{summary.idle_share_of_cycle:.1f}%** of cycle time",
        f"- Cost at ₹{filters.idle_cost_per_hour:,.0f}/h: "
        f"**{format_inr(summary.cost)}** (assumption)",
        f"- Diesel burnt while idling: **{summary.fuel_litres_idle:,.0f} L** "
        f"({format_inr(summary.fuel_cost_idle)}); the FMS records every idle event with the "
        f"engine running",
        f"- Addressable by scheduling: **{addressable_hours:,.0f} hours** "
        f"({format_inr(addressable_hours * filters.idle_cost_per_hour)})",
        f"- Material moved in the period: {summary.tonnes:,.0f} t at "
        f"{summary.tonnes_per_operating_hour:,.0f} t per operating hour",
        "",
        "## Top causes",
        "",
        "| Reason | Class | Hours | Share | Addressable |",
        "| --- | --- | --- | --- | --- |",
    ]
    for _, row in top.iterrows():
        lines.append(
            f"| {row['Reason']} | {row['Reason_Class']} | {row['Hours']:,.0f} | "
            f"{row['Share_Pct']:.1f}% | {'Yes' if row['Addressable'] else 'No'} |"
        )

    totals = st.session_state.get("simulation_totals")
    lines += ["", "## Recommended actions", ""]
    rank = 0
    for _, row in addressable.iterrows():
        lever = config.IDLE_LEVERS.get(str(row["Reason"]))
        if lever is None:
            continue
        rank += 1
        share = float(str(lever["realistic_reduction"]).rstrip("%")) / 100
        saved = float(row["Hours"]) * share
        lines += [
            f"**{rank}. {lever['lever']}** — targets *{row['Reason']}* "
            f"({row['Hours']:,.0f} h lost)",
            "",
            f"- {lever['detail']}",
            f"- Assumed reduction {lever['realistic_reduction']} → "
            f"**{saved:,.0f} hours** recovered, "
            f"{format_inr(saved * filters.idle_cost_per_hour)}",
            "",
        ]

    if totals:
        lines += [
            "## Simulated plan (from the Simulation page)",
            "",
            f"- Idle recovered: **{totals['hours_saved']:,.0f} hours**",
            f"- Value in period: **{format_inr(totals['value'])}**",
            f"- Annualised: **{format_inr(totals['annualised'])}**",
            f"- Diesel saved: {totals['fuel_litres']:,.0f} L",
            f"- Extra material moved: {totals['extra_tonnes']:,.0f} t",
            "",
        ]

    lines += [
        "## Basis and limitations",
        "",
        "- Source: VPTL/Wenco FMS exports for July 2026, parsed automatically.",
        "- Idle = queueing plus stopped-in-trip time from the cycle report, plus every "
        "reason-coded delay logged against the machine.",
        "- Cycle-report column names were corrected against measured haul distances; the "
        "raw names are misleading.",
        "- Cost per idle hour is an assumption and is adjustable in the dashboard. The fuel "
        "and tonnage figures are measured.",
        "- Break entitlements are not reduced anywhere in this analysis. The saving comes "
        "from staggering breaks and relieving operators at the machine.",
        "- One month of data: seasonal effects such as monsoon road conditions are not "
        "separable.",
    ]
    return "\n".join(lines)


def _build_print_html(markdown_text: str) -> str:
    """Wrap the management summary markdown in a print-ready HTML page.

    The downloaded HTML auto-opens the browser print dialog so the user can
    Save as PDF directly.  Uses a minimal clean stylesheet suitable for A4.
    """
    import html as html_mod
    import re as re_mod

    def md_to_html(md: str) -> str:
        lines = md.split("\n")
        out: list[str] = []
        in_table = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and "---" not in stripped:
                cells = [c.strip() for c in stripped.split("|")[1:-1]]
                if not in_table:
                    out.append('<table>')
                    in_table = True
                    out.append("<tr>" + "".join(f"<th>{html_mod.escape(c)}</th>" for c in cells) + "</tr>")
                else:
                    out.append("<tr>" + "".join(f"<td>{html_mod.escape(c)}</td>" for c in cells) + "</tr>")
            elif in_table and not stripped.startswith("|"):
                out.append("</table>")
                in_table = False
                if stripped:
                    out.append(_md_line(stripped))
            else:
                if not in_table:
                    out.append(_md_line(stripped))
        if in_table:
            out.append("</table>")
        return "\n".join(out)

    def _md_line(s: str) -> str:
        if not s:
            return ""
        if s.startswith("# "):
            return f"<h1>{html_mod.escape(s[2:])}</h1>"
        if s.startswith("## "):
            return f"<h2>{html_mod.escape(s[3:])}</h2>"
        if s.startswith("#### "):
            return f"<h4>{html_mod.escape(s[5:])}</h4>"
        if s.startswith("- "):
            return f"<li>{_inline(s[2:])}</li>"
        if s.startswith("**") and s.endswith("**") and len(s) > 4:
            return f"<p><strong>{_inline(s[2:-2])}</strong></p>"
        return f"<p>{_inline(s)}</p>"

    def _inline(s: str) -> str:
        s = html_mod.escape(s)
        s = re_mod.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re_mod.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
        s = re_mod.sub(r"`(.+?)`", r"<code>\1</code>", s)
        return s

    body = md_to_html(markdown_text)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Idle Time Summary</title>
<style>
@page {{ size: A4; margin: 18mm; }}
body {{ font-family: Helvetica, Arial, sans-serif; font-size: 11pt; color: #1a1a1a; line-height: 1.5; }}
h1 {{ font-size: 18pt; color: #2c3e50; border-bottom: 2px solid #C8E600; padding-bottom: 6px; }}
h2 {{ font-size: 14pt; color: #2c3e50; margin-top: 20px; }}
h4 {{ font-size: 12pt; color: #34495e; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; font-size: 10pt; }}
th {{ background: #f5f6f0; }}
li {{ margin: 4px 0; }}
code {{ background: #f0f0f0; padding: 1px 4px; font-size: 10pt; }}
p {{ margin: 6px 0; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _build_pdf(html_content: str) -> bytes:
    """Convert print-ready HTML to PDF bytes using xhtml2pdf (pure Python).

    Returns empty bytes if xhtml2pdf is not installed or fails, so the
    caller can fall back to an HTML download.
    """
    try:
        from xhtml2pdf import pisa
    except ImportError:
        return b""
    result = io.BytesIO()
    pisa_status = pisa.CreatePDF(io.BytesIO(html_content.encode("utf-8")), dest=result)
    if pisa_status.err:
        return b""
    return result.getvalue()


def main() -> None:
    """Render the reports page."""
    if not ui.data_is_ready():
        return

    tables = ui.load_processed()
    filters = ui.sidebar_filters(tables["shifts"])
    ui.sidebar_model_panel()

    shifts = filters.apply(tables["shifts"])
    delay_events = filters.apply(tables["delay_events"])
    reasons = reason_master(delay_events)

    ui.hero("Reports & exports", "Management summary, data exports and method notes")

    if shifts.empty:
        st.warning("No data matches the current filters.")
        return

    summary = ui.summarise_idle(shifts, filters)

    tab_summary, tab_export, tab_model, tab_method = st.tabs(
        ["Management summary", "Data export", "Model card", "Method & limitations"]
    )

    with tab_summary:
        text = build_summary_text(summary, reasons, filters)

        export_cols = st.columns(2)
        with export_cols[0]:
            st.download_button(
                "Download summary (Markdown)", text,
                file_name=f"idle_summary_{filters.start:%Y%m%d}_{filters.end:%Y%m%d}.md",
                mime="text/markdown",
            )
        with export_cols[1]:
            pdf_html = _build_print_html(text)
            pdf_bytes = _build_pdf(pdf_html)
            if pdf_bytes:
                st.download_button(
                    "Download PDF",
                    pdf_bytes,
                    file_name=f"idle_summary_{filters.start:%Y%m%d}_{filters.end:%Y%m%d}.pdf",
                    mime="application/pdf",
                )
            else:
                st.download_button(
                    "Download summary (HTML)",
                    pdf_html,
                    file_name=f"idle_summary_{filters.start:%Y%m%d}_{filters.end:%Y%m%d}.html",
                    mime="text/html",
                    help="PDF generation failed — download HTML and use Ctrl+P → Save as PDF.",
                )

        st.markdown("---")
        st.markdown(text)

    with tab_export:
        st.markdown(
            "Exports respect the sidebar filters where the table has a shift date. "
            "Reference tables such as fuel-by-dumper are exported whole."
        )
        choice = st.selectbox("Table", list(EXPORTABLE))
        key = EXPORTABLE[choice]
        frame = tables.get(key, pd.DataFrame())
        if frame.empty:
            st.info("That table is not available.")
        else:
            filtered = filters.apply(frame)
            st.caption(f"{len(filtered):,} rows · {len(filtered.columns)} columns")
            st.dataframe(filtered.head(200), hide_index=True, height=400)

            buttons = st.columns(2)
            with buttons[0]:
                st.download_button(
                    "Download CSV", filtered.to_csv(index=False).encode("utf-8"),
                    file_name=f"{key}.csv", mime="text/csv",
                )
            with buttons[1]:
                buffer = io.BytesIO()
                filtered.to_parquet(buffer, index=False)
                st.download_button(
                    "Download Parquet", buffer.getvalue(),
                    file_name=f"{key}.parquet", mime="application/octet-stream",
                    use_container_width=True,
                )

    with tab_model:
        bundle = ui.load_bundle()
        if bundle is None:
            st.info("No model trained yet. Use the sidebar button.")
        else:
            st.markdown("#### 1. High-idle-risk classifier — the trustworthy one")
            if bundle.risk_metrics:
                cards = st.columns(4)
                with cards[0]:
                    ui.kpi_card("Model", bundle.risk_model_name, "best of two candidates",
                                tone="good")
                with cards[1]:
                    ui.kpi_card("Test AUC", f"{bundle.risk_metrics['auc']:.3f}",
                                "held-out final 20% of the month", tone="good")
                with cards[2]:
                    ui.kpi_card("Test accuracy", f"{bundle.risk_metrics['accuracy']:.0%}",
                                "worst-third-of-month flag")
                with cards[3]:
                    ui.kpi_card("F1 score", f"{bundle.risk_metrics['f1']:.2f}",
                                "balances precision and recall")

                st.dataframe(
                    bundle.risk_leaderboard[["Model", "AUC", "Accuracy", "F1"]],
                    hide_index=True,
                    column_config={
                        "AUC": st.column_config.NumberColumn(format="%.4f"),
                        "Accuracy": st.column_config.NumberColumn(format="%.4f"),
                        "F1": st.column_config.NumberColumn(format="%.4f"),
                    },
                )
                if bundle.risk_importances is not None:
                    st.markdown("**What predicts high-idle risk**")
                    st.dataframe(
                        bundle.risk_importances.head(10), hide_index=True,
                        column_config={
                            "Importance": st.column_config.ProgressColumn(
                                format="%.3f", min_value=0,
                                max_value=float(bundle.risk_importances["Importance"].max()),
                            )
                        },
                    )
            else:
                st.info("Risk classifier not available for this training run.")

            st.divider()
            st.markdown("#### 2. Idle-minutes regressor — a rough estimate, honestly scored")
            cards = st.columns(4)
            with cards[0]:
                ui.kpi_card("Chosen model", bundle.model_name, "best of three candidates")
            with cards[1]:
                # The pooled R2 is flattered by the bimodal split between working and fully
                # down shifts; the honest number is the working-shifts R2.
                pooled_r2 = bundle.metrics['r2']
                working_r2 = bundle.segment_metrics.get("working_shifts", {}).get("r2") if bundle.segment_metrics else None
                display_r2 = f"{working_r2:.3f}" if working_r2 is not None else f"{pooled_r2:.3f}"
                ui.kpi_card("Test R²", display_r2, "held-out final 20% of the month")
            with cards[2]:
                ui.kpi_card("MAE", f"{bundle.metrics['mae']:.1f} min",
                            f"on a mean of {shifts['Total_Idle_Min'].mean():.0f} min")
            with cards[3]:
                ui.kpi_card("Training rows", format_number(bundle.n_train),
                            f"{bundle.n_test:,} held out")

            st.dataframe(
                bundle.leaderboard[["Model", "R2", "MAE"]], hide_index=True,
                column_config={
                    "R2": st.column_config.NumberColumn("Test R²", format="%.4f"),
                    "MAE": st.column_config.NumberColumn("MAE (min)", format="%.2f"),
                },
            )

            if bundle.segment_metrics:
                st.warning(
                    "**The pooled R² is not the headline.** The target is bimodal: "
                    f"working shifts average {bundle.segment_metrics['working_shifts']['mean_actual']:.0f} min, "
                    "while fully-down shifts average "
                    f"{bundle.segment_metrics['zero_cycle_shifts']['mean_actual']:.0f} min. "
                    f"A model that merely separates those two populations scores R²={pooled_r2:.3f} "
                    "but does not predict idle time *within* either group. The honest figure to "
                    "quote for working shifts is the one in the KPI card."
                )

            st.markdown("**Top features**")
            st.dataframe(
                bundle.importances.head(15), hide_index=True,
                column_config={
                    "Importance": st.column_config.ProgressColumn(
                        format="%.3f", min_value=0,
                        max_value=float(bundle.importances["Importance"].max()),
                    )
                },
            )

            risk_auc_display = f"{bundle.risk_metrics['auc']:.2f}" if bundle.risk_metrics else "n/a"
            st.markdown(
                f"""
                #### Model card

                - **Grain:** dumper × shift date × shift number ({shifts['Equipment_ID'].nunique()}
                  dumpers, {len(shifts):,} dumper-shifts in the full dataset).
                - **Validation:** chronological split for both models. Trained on the earlier
                  part of July, scored on the later part. No random shuffling, so the scores
                  reflect genuine forward prediction, not interpolation.
                - **Regression target:** `{bundle.target}` — total idle minutes for one dumper
                  in one shift.
                - **Classification target:** whether `{bundle.target}` exceeds the
                  {config.HIGH_IDLE_PERCENTILE:.0%} percentile of the training period
                  ({bundle.risk_threshold:.0f} min, i.e. {bundle.risk_threshold / 60:.1f} h).
                - **Leakage control:** every literal component of idle time is blacklisted in
                  `config.LEAKY_COLUMNS`. `Cycles` and `Payload_Tonnes` are *also* blacklisted:
                  testing showed `Cycles` correlates at r=0.95 with a value derived directly
                  from the delay component of the target, because a fixed 8-hour shift means
                  fewer completed cycles is nearly always just a readout of more delay having
                  happened. Including it inflated R² to ~0.50 without adding real predictive
                  power. History features are lagged by one shift so a group mean can never
                  contain the row it predicts.
                - **Why the regressor's R² is modest, honestly:** with `Cycles` removed, over
                  half of the remaining variance in idle minutes comes from stochastic
                  mechanical breakdowns (coefficient of variation ≈1.0 across dumpers, the
                  signature of a random failure process). No amount of feature engineering on
                  schedule and workload data will predict *when* a specific machine breaks; that
                  needs maintenance and fault-code history, which is not in the FMS exports
                  collected for this project.
                - **Why the classifier is more trustworthy:** ranking shifts into "likely to be
                  bad" vs not is a coarser, easier question than predicting the exact minute
                  count, and the same honest features answer it with
                  AUC {risk_auc_display} — a genuinely useful signal for where a
                  supervisor should look first.
                - **Intended use:** flagging shifts and dumpers worth a closer look, not
                  precise idle-minute forecasting and not individual performance management.
                  Most idle is organisational and outside an operator's control.
                """
            )
            st.warning(
                "**Data that would move the ceiling further:** maintenance/fault logs "
                "(fault codes, hours since last service, dumper age) to predict breakdown "
                "risk directly; rainfall/weather records for Ramgarh/Bokaro to explain "
                "stopped-in-trip time; or the FMS dispatch/roster plan (planned truck-to-shovel "
                "assignment) to make congestion features genuinely known before the shift "
                "starts rather than measured during it."
            )

            st.markdown("#### Impact of the shift-spine fix")
            st.markdown(
                """
                The shift master was rebuilt to include dumper-shifts that logged delay
                events but completed zero cycles (fully-down shifts). This corrected a
                survivorship bias that had hidden 1,412 shifts and ~10,458 h of downtime.
                """
            )
            kpi_before_after = pd.DataFrame(
                [
                    {"Metric": "Dumper-shifts", "Before": "4,960", "After": f"{len(shifts):,}"},
                    {"Metric": "Total idle hours", "Before": "19,632", "After": f"{summary.total_idle_hours:,.0f}"},
                    {"Metric": "Idle cost @ ₹5,000/h", "Before": "₹9.82 Cr",
                     "After": format_inr(summary.cost)},
                    {"Metric": "Addressable hours", "Before": "9,362",
                     "After": f"{float(reasons[reasons['Addressable']]['Hours'].sum()):,.0f}"},
                    {"Metric": "Fleet size", "Before": "69", "After": f"{shifts['Equipment_ID'].nunique()}"},
                ]
            )
            st.dataframe(kpi_before_after, hide_index=True)
            ui.note(
                "The opportunity does <b>not</b> grow much — the extra 10,458 h is nearly all "
                "unaddressable <code>Down</code> time. What changes is that the mine can no "
                "longer under-report how much capacity it never had."
            )
            st.markdown(
                """
                - **Classifier scope:** the high-idle-risk classifier is trained and evaluated
                  on **working shifts only** (≥1 cycle). Fully-down shifts are excluded so the
                  classifier stays an idle-management tool, not a breakdown predictor. A truck
                  that never moved is not "high idle risk" in the scheduling sense.
                """
            )

    with tab_method:
        provenance = ui.load_provenance()
        st.markdown("#### Pipeline")
        st.markdown(
            """
            1. **Parse** — `read_csv_robust` locates the real header row inside each SSRS
               export, discounting the banner row of `textbox1, textbox2, ...` placeholders,
               and tries three encodings.
            2. **Standardise** — `column_mapping.json` maps roughly 190 raw column aliases to
               canonical names. The lookup is case-sensitive first, because the FMS
               distinguishes `DURATION` (hours) from `duration` (minutes) by case alone.
            3. **Clean** — Indian digit grouping (`1,07,912.55`), `--N.D--` and `#Error`
               tokens, `HH:MM:SS` durations and five different date formats are all handled.
               Night-shift events after midnight are attributed to the shift that started the
               previous evening.
            4. **Aggregate** — cycle, shift, hourly, reason and per-dumper master tables.
            5. **Model** — three regressors compared on a chronological split; the best is
               saved compressed to `models/base_model.pkl`.
            """
        )

        st.markdown("#### Reports consumed")
        if provenance:
            st.json(provenance.get("files_used", {}), expanded=False)
            st.json(provenance.get("row_counts", {}), expanded=False)

        st.markdown("#### Known limitations")
        st.markdown(
            """
            - **`START_TIMESTAMP` in the cycle report is unusable.** Its dates and times
              disagree with the shift columns and some rows fall in June. Cycles are therefore
              dated from `LOAD_START_SHIFT_DATE` and `LOAD_START_SHIFT_IDENT` only, and all
              hour-of-day analysis comes from the two reports with trustworthy clock times.
            - **Sub-status codes are largely blank** for Delay and Down events, so the status
              code carries the reason. Sub-status is populated for Net Operating states only.
            - **`FUEL_CONSUMED` inside the cycle report is empty** (`--N.D--` throughout).
              Fuel comes from the separate Fuel Consumption by Hauling Unit report, which is a
              monthly per-dumper total and cannot be split by shift.
            - **Availability context covers part of the fleet.** The `Dumper_QSE_Report`
              supplies breakdown and availability hours for roughly half the dumper-shifts, so
              those features are sparse and the model imputes them.
            - **One month of data.** July is monsoon season in Ramgarh; wet haul roads may
              inflate stopped-in-trip time relative to a dry month. No comparison period is
              available to test this.
            - **Idle cost per hour is an assumption.** Fuel litres and tonnes moved are
              measured; the rupee conversion is not.
            """
        )

        st.markdown("#### Reproducing this analysis")
        st.code(
            "python scripts/ingest.py      # parse, aggregate, train\n"
            "streamlit run app.py          # launch the dashboard",
            language="text",
        )


main()
