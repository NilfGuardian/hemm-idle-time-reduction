"""Plotly chart builders with consistent Tata-themed styling.

Kept separate from the pages so every chart on every page shares one visual
language and the pages stay readable.
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

import config
from utils.helpers import safe_shift_label

FONT = dict(family="Inter, Segoe UI, sans-serif", size=12, color=config.TEXT_LIGHT)

_INTER_TEMPLATE = go.layout.Template(
    layout=go.Layout(
        font=FONT,
        title_font=dict(size=13, color=config.LIME, family="Bebas Neue, sans-serif"),
        legend=dict(font=dict(color=config.TEXT_LIGHT, family="Inter, sans-serif")),
        xaxis=dict(tickfont=dict(family="Inter, sans-serif"), title_font=dict(family="Inter, sans-serif")),
        yaxis=dict(tickfont=dict(family="Inter, sans-serif"), title_font=dict(family="Inter, sans-serif")),
        hoverlabel=dict(font=dict(family="Inter, sans-serif")),
        coloraxis=dict(colorbar=dict(tickfont=dict(family="Inter, sans-serif"))),
    )
)


def _style(figure: go.Figure, height: int = 340, legend: bool = True) -> go.Figure:
    """Apply the shared dark/technical layout to any figure."""
    figure.update_layout(
        template=_INTER_TEMPLATE,
        height=height,
        font=FONT,
        plot_bgcolor=config.BG_DARK,
        paper_bgcolor=config.BG_DARK,
        margin=dict(l=50, r=50, t=80, b=40),
        title_font=dict(size=13, color=config.LIME, family="Bebas Neue, sans-serif"),
        title=dict(x=0.0, xanchor="left", y=0.97, yanchor="top"),
        showlegend=legend,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, x=0,
            font=dict(color=config.TEXT_LIGHT, family="Inter, sans-serif"),
            bgcolor="rgba(0,0,0,0)",
        ),
        hoverlabel=dict(bgcolor=config.BG_CARD, font_color=config.TEXT_LIGHT, font_size=12, font_family="Inter, sans-serif"),
    )
    figure.update_xaxes(
        showgrid=True, gridcolor=config.GRID_DARK, linecolor=config.BORDER_MUTED,
        tickfont=dict(color=config.TEXT_MUTED, family="Inter, sans-serif"),
        title_font=dict(color=config.TEXT_MUTED, family="Inter, sans-serif"),
    )
    figure.update_yaxes(
        showgrid=True, gridcolor=config.GRID_DARK, linecolor=config.BORDER_MUTED,
        tickfont=dict(color=config.TEXT_MUTED, family="Inter, sans-serif"),
        title_font=dict(color=config.TEXT_MUTED, family="Inter, sans-serif"),
    )
    return figure


def cycle_breakdown_bar(shifts: pd.DataFrame) -> go.Figure:
    """Horizontal bar of average minutes per cycle, split productive vs idle.

    This is the single most important chart in the app: it shows how much of a
    haul cycle is not moving material.
    """
    totals: list[dict[str, object]] = []
    cycles = float(shifts["Cycles"].sum()) or 1.0
    for column, label in config.BUCKET_LABELS.items():
        if column not in shifts.columns:
            continue
        if column in config.PRODUCTIVE_BUCKETS:
            group = "Productive"
        elif column in config.IDLE_BUCKETS:
            group = "Idle"
        else:
            group = "Spotting"
        totals.append(
            {
                "Bucket": label,
                "Group": group,
                "Minutes per cycle": float(shifts[column].sum()) / cycles,
            }
        )

    frame = pd.DataFrame(totals).sort_values("Minutes per cycle")
    figure = px.bar(
        frame, x="Minutes per cycle", y="Bucket", color="Group", orientation="h",
        color_discrete_map={
            "Productive": config.TATA_BLUE,
            "Idle": config.TATA_ORANGE,
            "Spotting": config.TATA_GREY,
        },
        title="Where a haul cycle goes (average minutes per cycle)",
        text=frame["Minutes per cycle"].round(2),
    )
    figure.update_traces(textposition="outside", cliponaxis=False, textfont=dict(size=10, family="Inter, sans-serif"))
    return _style(figure, height=420)


def idle_trend(shifts: pd.DataFrame) -> go.Figure:
    """Daily idle hours split into in-cycle idle and reason-coded delay."""
    daily = shifts.groupby("Shift_Date", as_index=False).agg(
        Cycle_Idle=("Cycle_Idle_Min", "sum"), Delay=("Delay_Min", "sum")
    )
    daily["In-cycle idle"] = daily["Cycle_Idle"] / 60
    daily["Reason-coded delay"] = daily["Delay"] / 60

    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=daily["Shift_Date"], y=daily["In-cycle idle"], name="In-cycle idle",
        mode="lines", stackgroup="one", line=dict(width=0.5, color=config.TATA_ORANGE),
        fillcolor="rgba(230,126,34,.55)",
    ))
    figure.add_trace(go.Scatter(
        x=daily["Shift_Date"], y=daily["Reason-coded delay"], name="Reason-coded delay",
        mode="lines", stackgroup="one", line=dict(width=0.5, color=config.TATA_BLUE),
        fillcolor="rgba(26,82,118,.55)",
    ))
    figure.update_layout(title="Idle hours per day", yaxis_title="Hours")
    return _style(figure)


def idle_by_shift(shifts: pd.DataFrame) -> go.Figure:
    """Average idle hours per dumper-shift, by shift number."""
    by_shift = shifts.groupby("Shift", as_index=False).agg(
        Idle=("Total_Idle_Min", "mean"), Records=("Total_Idle_Min", "size")
    )
    by_shift["Hours"] = by_shift["Idle"] / 60
    by_shift["Label"] = by_shift["Shift"].map(safe_shift_label)
    figure = px.bar(
        by_shift, x="Label", y="Hours", text=by_shift["Hours"].round(2),
        title="Average idle per dumper-shift", color_discrete_sequence=[config.TATA_BLUE],
    )
    figure.update_traces(textposition="outside", cliponaxis=False)
    figure.update_layout(yaxis_title="Hours", xaxis_title="")
    return _style(figure, legend=False)


def hour_of_day_profile(hourly: pd.DataFrame) -> go.Figure:
    """Idle minutes by hour of day, which exposes the changeover and break peaks."""
    agg_dict: dict[str, tuple[str, str]] = {"Idle": ("Idle_Min", "sum")}
    if "Delay_Min" in hourly.columns:
        agg_dict["Delay"] = ("Delay_Min", "sum")
    profile = hourly.groupby("Hour", as_index=False).agg(**agg_dict)
    days = max(hourly["Shift_Date"].nunique(), 1)
    profile["Idle hours per day"] = profile["Idle"] / 60 / days

    colors = [
        config.TATA_ORANGE if hour in (6, 14, 22) else config.TATA_BLUE_LIGHT
        for hour in profile["Hour"]
    ]
    figure = go.Figure(
        go.Bar(x=profile["Hour"], y=profile["Idle hours per day"], marker_color=colors)
    )
    figure.update_layout(
        title="Fleet idle by hour of day (orange = shift changeover hours)",
        xaxis_title="Hour of day", yaxis_title="Idle hours per day",
        xaxis=dict(dtick=1),
    )
    return _style(figure, legend=False)


def reason_pareto(reasons: pd.DataFrame, top: int = 12) -> go.Figure:
    """Pareto of idle reasons coloured by how addressable each one is."""
    frame = reasons.head(top).sort_values("Hours")
    figure = px.bar(
        frame, x="Hours", y="Reason", orientation="h", color="Reason_Class",
        color_discrete_map=config.REASON_CLASS_COLORS,
        title="Idle reasons ranked by hours lost",
        text=frame["Hours"].round(0),
        custom_data=["Share_Pct", "Events", "Mean_Min"],
    )
    figure.update_traces(
        textposition="outside", cliponaxis=False,
        hovertemplate=(
            "<b>%{y}</b><br>%{x:,.0f} hours (%{customdata[0]:.1f}% of delay)"
            "<br>%{customdata[1]:,.0f} events, mean %{customdata[2]:.1f} min<extra></extra>"
        ),
    )
    figure.update_layout(xaxis_title="Hours lost in period", yaxis_title="")
    return _style(figure, height=460)


def reason_class_donut(reasons: pd.DataFrame) -> go.Figure:
    """Split of idle hours across the four management classes."""
    grouped = reasons.groupby("Reason_Class", as_index=False)["Hours"].sum()
    figure = px.pie(
        grouped, names="Reason_Class", values="Hours", hole=0.58,
        color="Reason_Class", color_discrete_map=config.REASON_CLASS_COLORS,
        title="Who controls the lost time",
    )
    figure.update_traces(
        textinfo="percent", textposition="inside",
        textfont=dict(size=11, family="Inter, sans-serif", color="#fff"),
        insidetextorientation="radial",
    )
    return _style(figure, height=360, legend=True)


def equipment_ranking(equipment: pd.DataFrame, metric: str, label: str, top: int = 20) -> go.Figure:
    """Ranked bar chart of the worst dumpers on a chosen idle metric."""
    frame = equipment.nlargest(top, metric).sort_values(metric)
    figure = px.bar(
        frame, x=metric, y="Equipment_ID", orientation="h",
        color=metric, color_continuous_scale=config.IDLE_SCALE,
        title=f"Highest {label} ({top} dumpers)",
    )
    figure.update_layout(xaxis_title=label, yaxis_title="", coloraxis_showscale=False)
    return _style(figure, height=max(360, 22 * len(frame)), legend=False)


def idle_heatmap(shifts: pd.DataFrame) -> go.Figure:
    """Day-of-month by shift heatmap of average idle hours per dumper."""
    pivot = shifts.pivot_table(
        index="Shift", columns="Day", values="Total_Idle_Min", aggfunc="mean"
    ).div(60)
    pivot.index = [safe_shift_label(i) for i in pivot.index]
    figure = px.imshow(
        pivot, color_continuous_scale=config.IDLE_SCALE, aspect="auto",
        labels=dict(x="Day of month", y="", color="Idle h"),
        title="Average idle hours per dumper-shift",
    )
    return _style(figure, height=300, legend=False)


def reason_hour_heatmap(delay_events: pd.DataFrame, reasons: list[str]) -> go.Figure:
    """Hour-of-day fingerprint for the selected reasons."""
    frame = delay_events[delay_events["Reason"].isin(reasons)]
    if frame.empty:
        return _style(go.Figure(), height=300)
    days = max(frame["Shift_Date"].nunique(), 1)
    pivot = (
        frame.pivot_table(index="Reason", columns="Hour", values="Delay_Min", aggfunc="sum")
        .div(60 * days)
        .reindex(columns=range(24), fill_value=0)
    )
    figure = px.imshow(
        pivot, color_continuous_scale=config.IDLE_SCALE, aspect="auto",
        labels=dict(x="Hour of day", y="", color="Hours/day"),
        title="When each reason happens (hours lost per day)",
    )
    figure.update_xaxes(dtick=1)
    return _style(figure, height=max(260, 46 * len(pivot)), legend=False)


def residual_scatter(scored: pd.DataFrame) -> go.Figure:
    """Actual vs model-expected idle, highlighting the abnormal shifts."""
    frame = scored.dropna(subset=["Predicted_Idle_Min", "Total_Idle_Min"])
    figure = px.scatter(
        frame, x="Predicted_Idle_Min", y="Total_Idle_Min",
        color="Idle_Flag" if "Idle_Flag" in frame.columns else None,
        color_discrete_map={True: config.WARN_RED, False: config.TATA_BLUE_LIGHT},
        opacity=0.35,
        hover_data=["Equipment_ID", "Shift_Date", "Shift", "Cycles"],
        title="Actual idle vs what the model expected",
        labels={
            "Predicted_Idle_Min": "Expected idle (min)",
            "Total_Idle_Min": "Actual idle (min)",
            "Idle_Flag": "Abnormal",
        },
    )
    figure.update_traces(marker=dict(size=4, line=dict(width=0)))
    limit = float(frame[["Predicted_Idle_Min", "Total_Idle_Min"]].to_numpy().max())
    figure.add_trace(go.Scatter(
        x=[0, limit], y=[0, limit], mode="lines", name="Expected = actual",
        line=dict(color=config.TATA_GREY, dash="dash", width=1),
    ))
    return _style(figure, height=440)


def importance_bar(importances: pd.DataFrame, top: int = 12) -> go.Figure:
    """What the model leans on to predict idle."""
    frame = importances.head(top).sort_values("Importance")
    figure = px.bar(
        frame, x="Importance", y="Feature", orientation="h",
        color_discrete_sequence=[config.TATA_BLUE],
        title="What predicts idle time",
    )
    figure.update_layout(xaxis_title="Relative importance", yaxis_title="")
    return _style(figure, height=max(340, 26 * len(frame)), legend=False)


def savings_waterfall(rows: list[dict[str, object]], total_hours: float) -> go.Figure:
    """Waterfall from current idle hours down to the simulated level."""
    labels = ["Current idle"] + [str(r["Reason"]) for r in rows] + ["After changes"]
    values = [total_hours] + [-float(r["Hours saved"]) for r in rows] + [0.0]
    measures = ["absolute"] + ["relative"] * len(rows) + ["total"]

    figure = go.Figure(go.Waterfall(
        orientation="v", measure=measures, x=labels, y=values,
        decreasing=dict(marker=dict(color=config.OK_GREEN)),
        totals=dict(marker=dict(color=config.TATA_BLUE)),
        connector=dict(line=dict(color="#bdc3c7")),
        text=[f"{abs(v):,.0f}" for v in values[:-1]] + [""],
        textposition="outside",
    ))
    figure.update_layout(title="Idle hours removed by the selected changes", yaxis_title="Hours")
    return _style(figure, height=420, legend=False)


def breakdown_ranking(frame: pd.DataFrame, median_hours: float) -> go.Figure:
    """Horizontal bar of breakdown hours per dumper, highlighting repeat offenders.

    ``frame`` must have columns ``Equipment_ID`` and ``Breakdown_Hours``,
    sorted descending by ``Breakdown_Hours``.  Dumpers above the 75th
    percentile are coloured ``config.DANGER``; the rest are muted.
    A vertical line marks the fleet median.
    """
    plot = frame.sort_values("Breakdown_Hours", ascending=False).head(25)
    if plot.empty:
        fig = go.Figure()
        fig.update_layout(title="No breakdown events in this selection")
        return _style(fig, height=300, legend=False)

    threshold_75 = float(plot["Breakdown_Hours"].quantile(0.75))
    colors = [config.DANGER if h > threshold_75 else config.TEXT_MUTED
              for h in plot["Breakdown_Hours"]]

    fig = go.Figure(go.Bar(
        y=plot["Equipment_ID"][::-1],
        x=plot["Breakdown_Hours"][::-1],
        orientation="h",
        marker_color=colors[::-1],
        text=[f"{h:,.0f} h" for h in plot["Breakdown_Hours"][::-1]],
        textposition="outside",
        textfont=dict(color=config.TEXT_MUTED, size=10, family="Inter, sans-serif"),
    ))
    fig.add_vline(
        x=median_hours, line_dash="dash", line_color=config.LIME,
        annotation_text=f"median {median_hours:,.0f} h",
        annotation_font=dict(color=config.LIME, size=10, family="Inter, sans-serif"),
    )
    fig.update_layout(
        title="Breakdown / Down hours per dumper",
        xaxis_title="Hours", yaxis_title="",
    )
    return _style(fig, height=max(360, 22 * len(plot)), legend=False)


def shovel_starvation_scatter(frame: pd.DataFrame) -> go.Figure:
    """Scatter of trucks-per-shovel vs shovel idle hours.

    ``frame`` must have columns ``trucks``, ``shovel_idle_h``, ``Loading_Unit``,
    and ``Run_Hours``.  Each point is one shovel-shift; colour is by shovel.
    A weak correlation (typically r ≈ −0.08) demonstrates that adding trucks
    alone does not linearly reduce shovel idle — the chart shows this honestly.
    """
    if frame.empty:
        fig = go.Figure()
        fig.update_layout(title="No shovel-shift data available for this selection")
        return _style(fig, height=300, legend=False)

    corr = frame["trucks"].corr(frame["shovel_idle_h"])

    fig = px.scatter(
        frame,
        x="trucks",
        y="shovel_idle_h",
        color="Loading_Unit",
        hover_data=["Run_Hours"],
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig.update_traces(marker=dict(size=6, opacity=0.7))
    fig.update_layout(
        title=f"Trucks per shovel vs shovel idle hours (r = {corr:+.2f})",
        xaxis_title="Trucks assigned to shovel (per shift)",
        yaxis_title="Shovel idle hours (Available − Run)",
    )
    return _style(fig, height=440)


def dumper_timeline(shifts: pd.DataFrame, dumper_id: str) -> go.Figure:
    """Shift-by-shift idle timeline for a single dumper.

    Shows total idle hours per shift as a bar chart, coloured by shift number,
    with a rolling 5-shift average overlay.
    """
    d = shifts[shifts["Equipment_ID"] == dumper_id].copy()
    if d.empty:
        fig = go.Figure()
        fig.update_layout(title=f"No shifts found for {dumper_id}")
        return _style(fig, height=300, legend=False)

    d["Shift_Label"] = d["Shift_Date"].dt.strftime("%d %b") + " S" + d["Shift"].fillna(0).astype(int).astype(str)
    d["Idle_Hours"] = d["Total_Idle_Min"] / 60
    d = d.sort_values(["Shift_Date", "Shift"])
    d["Rolling_Avg"] = d["Idle_Hours"].rolling(5, min_periods=1).mean()

    shift_colors = {1: config.LIME, 2: config.TEXT_LIGHT, 3: config.TEXT_MUTED}
    fig = go.Figure()
    for shift_num in sorted(d["Shift"].dropna().unique()):
        subset = d[d["Shift"] == shift_num]
        fig.add_trace(go.Bar(
            x=subset["Shift_Label"], y=subset["Idle_Hours"],
            name=f"Shift {int(shift_num)}",
            marker_color=shift_colors.get(int(shift_num), config.LIME),
        ))
    fig.add_trace(go.Scatter(
        x=d["Shift_Label"], y=d["Rolling_Avg"],
        name="5-shift rolling avg", mode="lines+markers",
        line=dict(color=config.DANGER, width=2, dash="dot"),
        marker=dict(size=5),
    ))
    fig.update_layout(
        title=f"{dumper_id} — idle hours per shift",
        xaxis_title="", yaxis_title="Idle hours",
        barmode="group", bargap=0.15,
    )
    return _style(fig, height=380)


def dumper_reason_bar(delay_events: pd.DataFrame, dumper_id: str) -> go.Figure:
    """Horizontal bar chart of delay reasons for a single dumper."""
    d = delay_events[delay_events["Equipment_ID"] == dumper_id].copy()
    if d.empty:
        fig = go.Figure()
        fig.update_layout(title=f"No delay events for {dumper_id}")
        return _style(fig, height=300, legend=False)

    d["Hours"] = d["Delay_Min"] / 60
    by_reason = d.groupby("Reason", as_index=False).agg(
        Hours=("Hours", "sum"), Events=("Delay_Min", "size"),
    ).sort_values("Hours", ascending=True).tail(12)

    fig = px.bar(
        by_reason, x="Hours", y="Reason", orientation="h",
        text=by_reason["Hours"].round(1),
        color_discrete_sequence=[config.LIME],
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(
        title=f"{dumper_id} — top delay reasons",
        xaxis_title="Hours", yaxis_title="",
    )
    return _style(fig, height=max(300, 28 * len(by_reason)), legend=False)
