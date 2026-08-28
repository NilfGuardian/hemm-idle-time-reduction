"""Utility helpers for the HEMM idle time reduction app."""
from __future__ import annotations

import re
from typing import Union

import numpy as np
import pandas as pd


def format_duration(minutes: float) -> str:
    """Return a human-readable string for a number of minutes."""
    if pd.isna(minutes) or minutes < 0:
        return "-"
    hours, mins = divmod(int(minutes), 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h {mins}m"
    return f"{hours}h {mins}m" if hours else f"{mins}m"


def calculate_cost(idle_minutes: float, cost_per_hour: float) -> float:
    """Return the cost of idle time in INR."""
    if pd.isna(idle_minutes):
        return 0.0
    return (idle_minutes / 60.0) * cost_per_hour


def parse_indian_number(value: Union[str, float, int]) -> Union[float, None]:
    """Parse strings like '1,07,912.55' or '12,764.47' to float."""
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s in ("", "--N.D--", "#Error", "-", "N.D", "N.A"):
        return None
    # Remove all non-digit, non-dot, non-minus, non-comma chars
    s = re.sub(r"[^0-9.,\-]", "", s)
    if not s:
        return None
    # Try standard grouping first
    try:
        return float(s.replace(",", ""))
    except ValueError:
        pass
    # Indian numbering: last 3 digits before dot, then pairs
    if "," in s:
        # split into parts
        parts = s.split(",")
        if len(parts) > 1:
            # If last part before decimal is length 3, use all parts as thousands (indian style)
            try:
                return float("".join(parts))
            except ValueError:
                pass
    return None


def detect_outliers(series: pd.Series, method: str = "iqr", threshold: float = 1.5) -> pd.Series:
    """Return a boolean series flagging outliers."""
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return pd.Series(False, index=series.index)
    if method == "iqr":
        q1 = numeric.quantile(0.25)
        q3 = numeric.quantile(0.75)
        iqr = q3 - q1
        lower, upper = q1 - threshold * iqr, q3 + threshold * iqr
    elif method == "zscore":
        mean = numeric.mean()
        std = numeric.std()
        lower, upper = mean - threshold * std, mean + threshold * std
    else:
        raise ValueError(f"Unknown outlier method: {method}")
    outlier = pd.Series(False, index=series.index)
    outlier.loc[numeric.index] = (numeric < lower) | (numeric > upper)
    return outlier


def classify_site(
    location: str,
    qse_prefixes: list[str] | None = None,
    qab_prefixes: list[str] | None = None,
) -> str:
    """Classify a location string as QSE, QAB or Unknown."""
    if pd.isna(location):
        return "Unknown"
    qse_prefixes = qse_prefixes or ["QSE"]
    qab_prefixes = qab_prefixes or ["QAB"]
    loc = str(location).upper().strip()
    for p in qse_prefixes:
        if loc.startswith(p.upper()):
            return "QSE"
    for p in qab_prefixes:
        if loc.startswith(p.upper()):
            return "QAB"
    return "Unknown"


def format_inr(amount: float, decimals: int = 2) -> str:
    """Format a rupee amount using crore / lakh, the units the mine reports in."""
    if pd.isna(amount):
        return "-"
    sign = "-" if amount < 0 else ""
    value = abs(float(amount))
    if value >= 1e7:
        return f"{sign}\u20b9{value / 1e7:.{decimals}f} Cr"
    if value >= 1e5:
        return f"{sign}\u20b9{value / 1e5:.{decimals}f} L"
    if value >= 1e3:
        return f"{sign}\u20b9{value / 1e3:.1f} K"
    return f"{sign}\u20b9{value:,.0f}"


def format_number(value: float, decimals: int = 0) -> str:
    """Thousands-separated number, or a dash when missing."""
    if pd.isna(value):
        return "-"
    return f"{value:,.{decimals}f}"


def idle_fuel_litres(idle_minutes: float, burn_rate_per_hour: float) -> float:
    """Litres of diesel burnt while standing still with the engine running."""
    if pd.isna(idle_minutes):
        return 0.0
    return (idle_minutes / 60.0) * burn_rate_per_hour


def tonnes_recoverable(
    idle_minutes_saved: float, tonnes_per_operating_hour: float
) -> float:
    """Extra tonnes that the recovered idle hours could move.

    Assumes the recovered time is spent hauling at the observed productivity
    rate, which is the standard convention and is stated in the UI.
    """
    if pd.isna(idle_minutes_saved) or pd.isna(tonnes_per_operating_hour):
        return 0.0
    return (idle_minutes_saved / 60.0) * tonnes_per_operating_hour


def safe_shift_label(shift_value) -> str:
    """Return a human-readable shift label, handling NaN safely."""
    if pd.isna(shift_value):
        return "Unknown"
    sv = int(shift_value)
    import config
    return config.SHIFT_LABELS.get(sv, f"Shift {sv}")
