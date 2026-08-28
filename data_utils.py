"""Parsing, cleaning, aggregation and modelling for HEMM idle time reduction.

The FMS (Wenco/VPTL) exports are SSRS-style CSVs: banner rows, blank rows,
repeated label columns, pivot layouts and mixed date formats. This module turns
them into three tidy tables that the dashboard consumes:

    cycles.parquet   one row per haul cycle, with idle time decomposed
    shifts.parquet   one row per dumper x shift-date x shift, the model grain
    reasons.parquet  one row per reason-coded delay/down event

Everything is oriented around the project goal: quantify idle time, explain it,
and show what removing it is worth.
"""
from __future__ import annotations

import csv
import json
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

import config
from utils.helpers import parse_indian_number

warnings.filterwarnings("ignore", category=FutureWarning)

NULL_TOKENS = {
    "", "-", "--", "n.d", "n.a", "na", "nan", "null", "none",
    "--n.d--", "#error", "#div/0!", "#n/a", "#value!", "#ref!",
}

ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


# ==========================================================================  #
# 1. Robust reading
# ==========================================================================  #
def _read_rows(path: Path, max_rows: int | None = None) -> tuple[list[list[str]], str]:
    """Read a CSV into a list of rows, trying several encodings.

    Returns the rows and the encoding that worked.
    """
    last_error: Exception | None = None
    for enc in ENCODINGS:
        try:
            with Path(path).open("r", encoding=enc, newline="") as fh:
                reader = csv.reader(fh)
                rows: list[list[str]] = []
                for i, row in enumerate(reader):
                    rows.append(row)
                    if max_rows is not None and i >= max_rows:
                        break
            return rows, enc
        except UnicodeDecodeError as exc:  # try the next encoding
            last_error = exc
        except csv.Error as exc:
            last_error = exc
    raise ValueError(f"Could not decode {path}: {last_error}")


def _is_placeholder(cell: str) -> bool:
    """True for SSRS auto-generated names such as ``textbox1`` or ``Textbox298``."""
    return bool(re.fullmatch(r"[Tt]extbox\d*", cell.strip()))


def _is_headerish(cell: str) -> bool:
    """True if a cell looks like a column name rather than a data value."""
    text = cell.strip()
    if not text or len(text) > 60:
        return False
    if text.lower() in NULL_TOKENS:
        return False
    # Pure numbers, dates and times are data, not headers.
    if re.fullmatch(r"[-+]?[\d,]*\.?\d+", text):
        return False
    if re.fullmatch(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}.*", text):
        return False
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?\s*([AaPp][Mm])?", text):
        return False
    return bool(re.search(r"[A-Za-z]", text))


def detect_header_row(rows: list[list[str]], scan: int = 60) -> int:
    """Find the index of the real header row in an SSRS-style export.

    Scores each candidate row by how many header-looking cells it has and how
    well the rows beneath it match its filled-column layout.
    """
    best_index, best_score = 0, -1.0
    limit = min(len(rows) - 1, scan)
    for i in range(limit):
        row = rows[i]
        header_cells = [j for j, c in enumerate(row) if _is_headerish(c)]
        if len(header_cells) < 2:
            continue
        # Look at the following rows: a real header is followed by data that
        # fills roughly the same columns.
        followers = rows[i + 1 : i + 6]
        if not followers:
            continue
        agreement = 0.0
        for follower in followers:
            filled = {j for j, c in enumerate(follower) if c.strip()}
            if not filled:
                continue
            overlap = len(filled & set(header_cells)) / len(header_cells)
            agreement += overlap
        agreement /= max(len(followers), 1)
        # Every SSRS export starts with a banner row of "textbox1, textbox2, ..."
        # placeholders whose values are the report title and parameters. Those
        # names carry no meaning, so they are heavily discounted and the row of
        # genuine column names further down wins.
        placeholders = sum(1 for j in header_cells if _is_placeholder(row[j]))
        weight = (len(header_cells) - placeholders) + 0.15 * placeholders
        # Real header rows are database identifiers (STATUS_DESC, NAME2) with no
        # spaces, whereas the first data row holds prose ("Caterpillar 773E",
        # "Grand Total"). Without this term the first data row can outscore the
        # header it belongs to.
        identifiers = sum(
            1 for j in header_cells if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", row[j].strip())
        )
        identifier_ratio = identifiers / len(header_cells)
        score = weight * (0.4 + agreement) * (1.0 + identifier_ratio)
        if score > best_score:
            best_index, best_score = i, score
    return best_index


def read_csv_robust(
    path: str | Path,
    header_row: int | None = None,
    drop_empty_cols: bool = True,
) -> pd.DataFrame:
    """Read a messy FMS CSV into a DataFrame.

    Skips banner rows, finds the real header, drops all-empty columns and
    de-duplicates repeated column names.

    Args:
        path: CSV file to read.
        header_row: Force a header row index instead of auto-detecting.
        drop_empty_cols: Remove columns with no data and no name.
    """
    path = Path(path)
    rows, _enc = _read_rows(path)
    if not rows:
        return pd.DataFrame()

    idx = detect_header_row(rows) if header_row is None else header_row
    header = rows[idx]
    body = rows[idx + 1 :]

    width = max([len(header)] + [len(r) for r in body[:2000]] or [0])
    header = list(header) + [""] * (width - len(header))
    body = [list(r) + [""] * (width - len(r)) for r in body]

    names: list[str] = []
    seen: dict[str, int] = {}
    for j, raw in enumerate(header):
        name = re.sub(r"\s+", " ", str(raw)).strip()
        if not name:
            name = f"col_{j}"
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
        names.append(name)

    frame = pd.DataFrame(body, columns=names, dtype="object")

    # Drop rows that are entirely blank.
    frame = frame.loc[~frame.apply(lambda r: all(str(v).strip() == "" for v in r), axis=1)]

    if drop_empty_cols and not frame.empty:
        keep = []
        for col in frame.columns:
            has_data = frame[col].astype(str).str.strip().ne("").any()
            named = not str(col).startswith("col_")
            if has_data or named:
                keep.append(col)
        frame = frame[keep]

    return frame.reset_index(drop=True)


def _is_section_header(row: list[str]) -> bool:
    """True if a row is an SSRS sub-report header rather than data.

    Several exports stack two or three unrelated tables into a single CSV, each
    introduced by its own header row of database identifiers such as
    ``EquipmentNo1, Owner3, First_Shift_Trips2``. Data rows always carry at
    least one number, date or multi-word string, so requiring every populated
    cell to be a bare identifier separates the two reliably.
    """
    cells = [c.strip() for c in row if c.strip()]
    if len(cells) < 3:
        return False
    if not all(_is_headerish(c) for c in cells):
        return False
    identifiers = sum(1 for c in cells if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", c))
    return identifiers / len(cells) >= 0.7


def _frame_from_rows(header: list[str], body: list[list[str]]) -> pd.DataFrame:
    """Build a DataFrame from a header row and its data rows, de-duplicating
    repeated column names the way ``read_csv_robust`` does."""
    width = max([len(header)] + [len(r) for r in body] or [0])
    header = list(header) + [""] * (width - len(header))
    body = [list(r) + [""] * (width - len(r)) for r in body]

    names: list[str] = []
    seen: dict[str, int] = {}
    for j, raw in enumerate(header):
        name = re.sub(r"\s+", " ", str(raw)).strip() or f"col_{j}"
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
        names.append(name)

    frame = pd.DataFrame(body, columns=names, dtype="object")
    if frame.empty:
        return frame
    frame = frame.loc[~frame.apply(lambda r: all(str(v).strip() == "" for v in r), axis=1)]
    keep = [
        c for c in frame.columns
        if frame[c].astype(str).str.strip().ne("").any() or not str(c).startswith("col_")
    ]
    return frame[keep].reset_index(drop=True)


def read_csv_sections(path: str | Path, min_rows: int = 1) -> list[pd.DataFrame]:
    """Split an SSRS export that stacks several tables into one CSV.

    Returns one frame per section with raw (unstandardised) column names, so the
    caller can tell sections apart by their headers. Files with a single table
    come back as a one-element list, which makes this safe to use everywhere.
    """
    rows, _enc = _read_rows(path)
    if not rows:
        return []

    first = detect_header_row(rows)
    starts = [first] + [
        i for i in range(first + 1, len(rows)) if _is_section_header(rows[i])
    ]

    sections: list[pd.DataFrame] = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(rows)
        frame = _frame_from_rows(rows[start], rows[start + 1 : end])
        if len(frame) >= min_rows:
            sections.append(frame)
    return sections


def _pick_section(sections: list[pd.DataFrame], *required: str) -> pd.DataFrame:
    """Return the first section containing all ``required`` column names."""
    for frame in sections:
        if all(col in frame.columns for col in required):
            return frame
    return pd.DataFrame()


# ==========================================================================  #
# 2. Column standardisation
# ==========================================================================  #
def _load_mapping() -> tuple[dict[str, str], dict[str, str]]:
    """Return exact and lower-cased {raw_name: canonical_name} lookups.

    Two lookups are needed because the FMS distinguishes columns only by case:
    ``DURATION`` is in hours while ``duration`` is in minutes. The exact lookup
    is consulted first, the lower-cased one only as a fallback.
    """
    with config.COLUMN_MAPPING_PATH.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)["canonical"]
    exact: dict[str, str] = {}
    lower: dict[str, str] = {}
    for canonical, aliases in raw.items():
        exact.setdefault(canonical, canonical)
        lower.setdefault(canonical.lower(), canonical)
        for alias in aliases:
            key = str(alias).strip()
            exact.setdefault(key, canonical)
            lower.setdefault(key.lower(), canonical)
    return exact, lower


_MAPPING_CACHE: tuple[dict[str, str], dict[str, str]] | None = None


def get_mapping() -> tuple[dict[str, str], dict[str, str]]:
    """Cached accessor for the (exact, lower-cased) column mappings."""
    global _MAPPING_CACHE
    if _MAPPING_CACHE is None:
        _MAPPING_CACHE = _load_mapping()
    return _MAPPING_CACHE


def standardize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Rename columns to canonical names using ``column_mapping.json``.

    Unmapped columns keep their original name. Duplicate canonical names are
    suffixed so no information is silently lost.
    """
    exact, lower = get_mapping()
    out = frame.copy()
    new_names: list[str] = []
    used: dict[str, int] = {}
    for col in out.columns:
        base = re.sub(r"\.\d+$", "", str(col)).strip()
        canonical = exact.get(base) or lower.get(base.lower(), base)
        if canonical in used:
            used[canonical] += 1
            canonical = f"{canonical}__{used[canonical]}"
        else:
            used[canonical] = 0
        new_names.append(canonical)
    out.columns = new_names
    return out


# ==========================================================================  #
# 3. Cleaning primitives
# ==========================================================================  #
def to_number(series: pd.Series) -> pd.Series:
    """Convert a text column to float, handling Indian digit grouping."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype("float64")
    return series.map(parse_indian_number).astype("float64")


def duration_to_minutes(series: pd.Series) -> pd.Series:
    """Convert ``HH:MM:SS`` / ``HH:MM`` / numeric-minutes text to float minutes."""

    def convert(value: Any) -> float | None:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        text = str(value).strip()
        if text.lower() in NULL_TOKENS:
            return None
        if ":" in text:
            parts = text.split(":")
            try:
                nums = [float(p) for p in parts]
            except ValueError:
                return None
            if len(nums) == 3:
                return nums[0] * 60 + nums[1] + nums[2] / 60
            if len(nums) == 2:
                return nums[0] * 60 + nums[1]
            return None
        return parse_indian_number(text)

    return series.map(convert).astype("float64")


_DATE_FORMATS = (
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y",
    "%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%d-%b-%y",
)


def _smart_to_datetime(series: pd.Series, dayfirst_hint: bool | None = None) -> pd.Series:
    """Parse a date/datetime column without guessing per row.

    Tries a list of explicit formats and keeps the one that parses the most
    values, which avoids the classic ``01-07-2026`` day/month flip. FMS exports
    mix ``dd-mm-yyyy`` (report bodies) with ``m/d/yyyy h:mm:ss AM`` (pivot
    exports), so format choice must be decided per column, not per value.
    """
    text = series.astype(str).str.strip()
    text = text.mask(text.str.lower().isin(NULL_TOKENS))
    if text.dropna().empty:
        return pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    sample = text.dropna().head(3000)
    best: pd.Series | None = None
    best_hits = -1
    formats = _DATE_FORMATS
    if dayfirst_hint is True:
        formats = tuple(f for f in _DATE_FORMATS if not f.startswith("%m/"))
    elif dayfirst_hint is False:
        formats = tuple(f for f in _DATE_FORMATS if not f.startswith("%d/"))

    for fmt in formats:
        probe = pd.to_datetime(sample, format=fmt, errors="coerce")
        hits = int(probe.notna().sum())
        if hits > best_hits:
            best_hits, best = hits, fmt  # type: ignore[assignment]
        if hits == len(sample):
            break

    if best_hits <= 0:
        return pd.to_datetime(text, errors="coerce", dayfirst=True)

    parsed = pd.to_datetime(text, format=best, errors="coerce")  # type: ignore[arg-type]
    # Anything the winning format missed gets a lenient second pass.
    missing = parsed.isna() & text.notna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(
            text.loc[missing], errors="coerce", dayfirst=True
        )
    return parsed


def shift_from_hour(hour: pd.Series) -> pd.Series:
    """Map hour-of-day to shift number (1, 2 or 3)."""
    hour = pd.to_numeric(hour, errors="coerce")
    shift = pd.Series(np.nan, index=hour.index, dtype="float64")
    shift[(hour >= 6) & (hour < 14)] = 1
    shift[(hour >= 14) & (hour < 22)] = 2
    shift[(hour >= 22) | (hour < 6)] = 3
    return shift


def shift_date_from_timestamp(ts: pd.Series) -> pd.Series:
    """Return the FMS shift date for a timestamp.

    Hours 00:00-05:59 belong to the night shift that started the previous
    calendar evening, so they are attributed to the previous day.
    """
    ts = pd.to_datetime(ts, errors="coerce")
    dates = ts.dt.normalize()
    early_morning = ts.dt.hour < 6
    dates = dates.mask(early_morning, dates - pd.Timedelta(days=1))
    return dates


def normalize_equipment_id(series: pd.Series) -> pd.Series:
    """Normalise dumper/shovel ids so ``RD-208``, ``RD208 - RD-208`` all match."""
    text = series.astype(str).str.strip()
    # "RD208 - RD-208" -> "RD208";  "F01 - KOMATSU HD785-7" is a fleet, left alone
    text = text.str.split(" - ").str[0].str.strip()
    text = text.str.replace(r"[\s_]+", "", regex=True)
    text = text.str.replace(r"^([A-Za-z]+)-+", r"\1", regex=True)
    text = text.str.upper()
    return text.mask(text.str.lower().isin(NULL_TOKENS))


def clean_text(series: pd.Series) -> pd.Series:
    """Trim whitespace and blank out null tokens."""
    text = series.astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    return text.mask(text.str.lower().isin(NULL_TOKENS))


def drop_total_rows(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    """Drop SSRS subtotal / grand-total rows.

    These reports repeat "Total" and "Grand Total" inside the data columns,
    which would otherwise double-count every aggregate.
    """
    if frame.empty:
        return frame
    mask = pd.Series(False, index=frame.index)
    for col in columns:
        if col in frame.columns:
            text = frame[col].astype(str).str.strip().str.lower()
            mask |= text.isin({"total", "grand total", "subtotal", "sum", "average", "avg"})
    return frame.loc[~mask]


def clean_data(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the standard cleaning pass to a standardised frame.

    Blanks out null tokens, parses timestamps, coerces durations to minutes and
    normalises equipment ids. Column-specific logic lives in the loaders; this
    handles the fields that appear across many reports.
    """
    out = frame.copy()

    for col in ("Equipment_ID", "Loading_Unit"):
        if col in out.columns:
            out[col] = normalize_equipment_id(out[col])

    for col in ("Operator", "Fleet", "Material", "Load_Location", "Dump_Location",
                "Status_Category", "Status_Desc", "Sub_Status_Desc", "Status_Comment"):
        if col in out.columns:
            out[col] = clean_text(out[col])

    if "Shift" in out.columns:
        shift = out["Shift"].astype(str).str.extract(r"(\d)", expand=False)
        out["Shift"] = pd.to_numeric(shift, errors="coerce").astype("Int64")

    for col in ("Shift_Date",):
        if col in out.columns:
            out[col] = _smart_to_datetime(out[col], dayfirst_hint=True)

    for col in ("Start_Timestamp", "End_Timestamp", "Load_Timestamp", "Dump_Timestamp"):
        if col in out.columns:
            out[col] = _smart_to_datetime(out[col])

    return out


# ==========================================================================  #
# 4. Report classification
# ==========================================================================  #
# Report key -> (filename tokens, signature column tokens). A file matches when
# either the filename or the detected header contains the signature.
REPORT_SIGNATURES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "cycles": (("dumper cycle time",), ("EMPTY_STOPPED_TIME_NEW", "HAULING_STOPPED_TIME_NEW")),
    "idle_events": (("dumper idle time",), ("DUMPER NO", "Start Time", "Status")),
    "delay_events": (("delays and downs",), ("SHIFT_DATE1", "STATUS_DESC", "duration")),
    "status_summary": (("status and sub-status",), ("STATUSCAT_DESCRIP", "SUB_STATUS_DESC", "DURATION1")),
    "loader_delays": (("delay by loading unit",), ()),
    "fuel": (("fuel consumption by hauling unit",), ("FUEL_CONSUMPTION2",)),
    "fuel_shift": (("fuel consumption  by shift", "fuel consumption by shift"), ("LEVEL_START", "TANK_VOLUME")),
    "dumper_shift": (("dumper_qse_report",), ("BREAKDOWN_HR", "AVAILABIL_HR", "Canteen_Break")),
    "shovel_shift": (("shovel_qse_report",), ()),
    "operator": (("operator performance report",), ("Operator_PNo", "RunHours")),
    "tkph": (("productivity tkph",), ("HAUL_DISTANCE", "EMPTY_DISTANCE")),
    "payload_cycles": (("cycle time and payload",), ("HAULING_UNIT_PAYLOAD2",)),
    "engine_hours": (("net engine hours",), ("MAX_TIMESTAMP", "NUM_LOADS2")),
    "loading_unit_summary": (("loading unit summary",), ("Total_Trips", "Coal_Trips", "OB_Trips")),
    "loader_profile": (("loading unit time profile",), ("Sub-Status Code", "Hauling Unit")),
    "hauling_summary": (("hauling unit summary",), ("Equipment No", "Shift Date", "Hauling Unit First Load")),
    "status_category": (("time by status category",), ()),
    "manpower": (("manpower and production",), ("Operator_Count_First_Shift",)),
    "daily_production": (("daily production report",), ()),
    "availability": (("availability and productivity",), ()),
    "efh": (("efh summary",), ()),
    "point_times": (("time at loading and dumping",), ()),
    "payload_stats": (("payload statistics",), ()),
}

# Tables that are extra sections inside another report's CSV rather than files
# of their own, mapped to the report key whose files they are parsed from.
DERIVED_REPORTS: dict[str, str] = {
    "loading_unit_time": "loading_unit_summary",
    "loading_routes": "loading_unit_summary",
}

# Reports the dashboard actually consumes, in ingest order.
CORE_REPORTS = (
    "cycles", "idle_events", "delay_events", "status_summary",
    "fuel", "dumper_shift", "tkph", "shovel_shift",
    "hauling_summary", "loading_unit_summary", "daily_production",
    "loader_profile", "operator", "payload_cycles",
    "loading_unit_time", "loading_routes",
    "status_category", "engine_hours",
)


def source_key(report: str) -> str:
    """Discovery key whose files a report is parsed from."""
    return DERIVED_REPORTS.get(report, report)


def classify_file(path: str | Path) -> str:
    """Identify which FMS report a CSV is, by filename then by header signature.

    Returns the report key, or ``"unknown"`` if nothing matches.
    """
    path = Path(path)
    name = path.name.lower()

    for key, (name_tokens, _cols) in REPORT_SIGNATURES.items():
        if any(token in name for token in name_tokens):
            return key

    # Fall back to sniffing the header row.
    try:
        rows, _enc = _read_rows(path, max_rows=80)
    except (OSError, ValueError):
        return "unknown"
    if not rows:
        return "unknown"
    header = set(rows[detect_header_row(rows)])
    best_key, best_hits = "unknown", 0
    for key, (_names, col_tokens) in REPORT_SIGNATURES.items():
        if not col_tokens:
            continue
        hits = sum(1 for token in col_tokens if token in header)
        if hits > best_hits:
            best_key, best_hits = key, hits
    return best_key if best_hits >= 2 else "unknown"


def discover_files(dirs: Iterable[Path] | None = None) -> dict[str, list[Path]]:
    """Group every CSV in the given folders by report type."""
    dirs = list(dirs) if dirs is not None else list(config.DEFAULT_SOURCE_DIRS)
    found: dict[str, list[Path]] = {}
    for folder in dirs:
        folder = Path(folder)
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.csv")):
            found.setdefault(classify_file(path), []).append(path)
    return found


# ==========================================================================  #
# 5. Site classification and reason taxonomy
# ==========================================================================  #
def classify_site(series: pd.Series) -> pd.Series:
    """Label each location string as QSE, QAB or Unknown."""
    text = series.astype(str).str.upper()
    site = pd.Series("Unknown", index=series.index, dtype="object")
    for token in config.QAB_TOKENS:
        site = site.mask(text.str.contains(token, na=False, regex=False), "QAB")
    for token in config.QSE_TOKENS:
        site = site.mask(text.str.contains(token, na=False, regex=False), "QSE")
    return site


@dataclass(frozen=True)
class ReasonInfo:
    """Classification of a single FMS status/reason string."""

    reason_class: str = "Unclassified"
    addressable: bool = False


def classify_reason(text: Any) -> ReasonInfo:
    """Map an FMS status or sub-status string to a management-facing class.

    Returns the class (Organisational / Operational / Mechanical / External) and
    whether the mine can realistically act on it. First matching rule wins, so
    the rule order in ``config.REASON_RULES`` matters.
    """
    if text is None or (isinstance(text, float) and np.isnan(text)):
        return ReasonInfo()
    lowered = str(text).strip().lower()
    if not lowered or lowered in NULL_TOKENS:
        return ReasonInfo()
    for token, reason_class, addressable in config.REASON_RULES:
        if token in lowered:
            return ReasonInfo(reason_class, addressable)
    return ReasonInfo()


def strip_reason_code(series: pd.Series) -> pd.Series:
    """Turn ``"S19 - Tea/ Breakfast / Snacks"`` into ``"Tea/ Breakfast / Snacks"``."""
    text = clean_text(series)
    return text.str.replace(r"^[A-Z]{1,3}\d{1,3}\s*-\s*", "", regex=True).str.strip()


def add_reason_classes(frame: pd.DataFrame, source_col: str = "Reason") -> pd.DataFrame:
    """Attach ``Reason_Class`` and ``Addressable`` columns based on ``source_col``."""
    out = frame.copy()
    if source_col not in out.columns:
        out["Reason_Class"] = "Unclassified"
        out["Addressable"] = False
        return out
    unique = out[source_col].dropna().unique()
    lookup = {value: classify_reason(value) for value in unique}
    out["Reason_Class"] = out[source_col].map(
        lambda v: lookup.get(v, ReasonInfo()).reason_class
    ).fillna("Unclassified")
    out["Addressable"] = out[source_col].map(
        lambda v: lookup.get(v, ReasonInfo()).addressable
    ).fillna(False).astype(bool)
    return out


# ==========================================================================  #
# 6. Report loaders
# ==========================================================================  #
def _read_many(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Read and vertically stack several exports of the same report.

    Adds ``Source_File`` for traceability and drops rows that are byte-identical
    across chunk files, which happens when date ranges overlap.
    """
    frames: list[pd.DataFrame] = []
    for path in paths:
        path = Path(path)
        frame = standardize_columns(read_csv_robust(path))
        frame["Source_File"] = path.name
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True, sort=False)
    payload_cols = [c for c in combined.columns if c != "Source_File"]
    return combined.drop_duplicates(subset=payload_cols).reset_index(drop=True)


def load_cycles(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load the ``Dumper Cycle Time`` report into one row per haul cycle.

    ``START_TIMESTAMP`` in this report is unreliable (its dates and times
    disagree with the shift columns and some rows fall in June), so the cycle is
    dated from ``LOAD_START_SHIFT_DATE`` + ``LOAD_START_SHIFT_IDENT`` only.
    """
    raw = _read_many(paths)
    if raw.empty:
        return raw

    frame = clean_data(raw)
    frame = frame.drop(columns=[c for c in ("Start_Timestamp",) if c in frame.columns])

    for col in config.ALL_CYCLE_BUCKETS + ["Cycle_Time", "Payload"]:
        if col in frame.columns:
            frame[col] = to_number(frame[col])
        else:
            frame[col] = 0.0

    frame = frame.dropna(subset=["Equipment_ID", "Shift_Date"])
    frame = frame[frame["Cycle_Time"].between(0.5, 600, inclusive="both")]

    idle_cols = config.idle_bucket_columns()
    frame["Idle_Min"] = frame[idle_cols].sum(axis=1)
    frame["Queue_Min"] = frame[config.QUEUE_BUCKETS].sum(axis=1)
    frame["Stopped_Min"] = frame[config.STOPPED_BUCKETS].sum(axis=1)
    frame["Productive_Min"] = frame[list(config.PRODUCTIVE_BUCKETS)].sum(axis=1)
    frame["Spotting_Min"] = frame[list(config.SEMI_PRODUCTIVE_BUCKETS)].sum(axis=1)
    frame["Idle_Share"] = (frame["Idle_Min"] / frame["Cycle_Time"]).clip(0, 1)

    location = frame.get("Load_Location")
    if location is None:
        location = frame.get("Dump_Location", pd.Series("", index=frame.index))
    frame["Site"] = classify_site(location.fillna(""))
    frame["Route"] = (
        frame.get("Load_Location", pd.Series("?", index=frame.index)).fillna("?")
        + " -> "
        + frame.get("Dump_Location", pd.Series("?", index=frame.index)).fillna("?")
    )
    frame["Day_Of_Week"] = frame["Shift_Date"].dt.day_name()
    frame["Day"] = frame["Shift_Date"].dt.day
    return frame.reset_index(drop=True)


def load_idle_events(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load the ``Dumper Idle Time`` report: one row per explicit idle event.

    This is the authoritative measure of stand-still time because it carries
    real start/end timestamps. ``Status`` is always ``ON``, i.e. the engine was
    running throughout, so every minute here also burns diesel.
    """
    raw = _read_many(paths)
    if raw.empty:
        return raw

    frame = standardize_columns(raw)
    frame["Equipment_ID"] = normalize_equipment_id(frame["Equipment_ID"])
    frame["Start_Timestamp"] = _smart_to_datetime(frame["Start_Timestamp"], dayfirst_hint=False)
    frame["End_Timestamp"] = _smart_to_datetime(frame["End_Timestamp"], dayfirst_hint=False)

    if "Duration" in frame.columns:
        frame["Idle_Min"] = duration_to_minutes(frame["Duration"])
    else:
        frame["Idle_Min"] = (
            frame["End_Timestamp"] - frame["Start_Timestamp"]
        ).dt.total_seconds() / 60

    frame = frame.dropna(subset=["Equipment_ID", "Start_Timestamp", "Idle_Min"])
    frame = frame[frame["Idle_Min"] > 0]

    frame["Engine_Running"] = (
        frame.get("Status", pd.Series("ON", index=frame.index))
        .astype(str).str.strip().str.upper().eq("ON")
    )
    frame["Shift_Date"] = shift_date_from_timestamp(frame["Start_Timestamp"])
    frame["Hour"] = frame["Start_Timestamp"].dt.hour
    frame["Shift"] = shift_from_hour(frame["Hour"]).astype("Int64")
    frame["Day_Of_Week"] = frame["Shift_Date"].dt.day_name()

    keep = [
        "Equipment_ID", "Start_Timestamp", "End_Timestamp", "Idle_Min",
        "Engine_Running", "Shift_Date", "Shift", "Hour", "Day_Of_Week", "Source_File",
    ]
    return frame[[c for c in keep if c in frame.columns]].reset_index(drop=True)


def load_delay_events(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Delays and Downs by Equipment, Shift, Status Code and Sub-Status``.

    One row per reason-coded delay or down event, with a real clock time. The
    report stores only a time-of-day, so it is combined with the shift date and
    rolled to the next calendar day for night-shift events after midnight.
    """
    raw = _read_many(paths)
    if raw.empty:
        return raw

    frame = standardize_columns(raw)

    # The shift number hides in an unnamed label column as "Shift: 2".
    if "Shift" not in frame.columns:
        for col in frame.columns:
            sample = frame[col].astype(str).str.strip()
            if sample.str.match(r"^Shift:\s*\d$").mean() > 0.5:
                frame["Shift"] = sample.str.extract(r"(\d)", expand=False)
                break

    frame["Shift_Date"] = _smart_to_datetime(frame["Shift_Date"], dayfirst_hint=True)
    frame["Equipment_ID"] = normalize_equipment_id(frame["Equipment_ID"])
    frame["Shift"] = pd.to_numeric(
        frame.get("Shift", pd.Series(np.nan, index=frame.index)), errors="coerce"
    ).astype("Int64")

    time_start = clean_text(frame.get("Start_Timestamp", pd.Series("", index=frame.index)))
    offset = pd.to_timedelta(time_start, errors="coerce")
    # Night-shift events logged as 00:xx-05:xx belong to the following day.
    rolls_over = (frame["Shift"] == 3) & (offset < pd.Timedelta(hours=6))
    frame["Start_Timestamp"] = frame["Shift_Date"] + offset + pd.to_timedelta(
        rolls_over.astype(int), unit="D"
    )

    frame["Delay_Min"] = to_number(frame.get("Duration_Min", pd.Series(np.nan, index=frame.index)))
    if "Duration_Min" not in frame.columns or frame["Delay_Min"].isna().all():
        end = pd.to_timedelta(
            clean_text(frame.get("End_Timestamp", pd.Series("", index=frame.index))),
            errors="coerce",
        )
        frame["Delay_Min"] = (end - offset).dt.total_seconds() / 60

    frame["Reason"] = strip_reason_code(frame.get("Status_Desc", pd.Series("", index=frame.index)))
    sub = strip_reason_code(frame.get("Sub_Status_Desc", pd.Series("", index=frame.index)))
    frame["Sub_Reason"] = sub
    # Sub-status is mostly blank in this export, so fall back to the status code.
    frame["Reason"] = frame["Reason"].fillna(sub)

    frame = frame.dropna(subset=["Equipment_ID", "Shift_Date", "Delay_Min"])
    frame = frame[frame["Delay_Min"] > 0]

    frame = add_reason_classes(frame, "Reason")
    frame["Equipment_Class"] = np.where(
        frame["Equipment_ID"].str.startswith(("RD", "CRD")), "Dumper", "Loader/Other"
    )
    frame["Hour"] = frame["Start_Timestamp"].dt.hour
    frame["Day_Of_Week"] = frame["Shift_Date"].dt.day_name()

    keep = [
        "Shift_Date", "Shift", "Hour", "Day_Of_Week", "Equipment_ID", "Equipment_Class",
        "Fleet", "Reason", "Sub_Reason", "Reason_Class", "Addressable", "Delay_Min",
        "Start_Timestamp", "Status_Comment", "Source_File",
    ]
    return frame[[c for c in keep if c in frame.columns]].reset_index(drop=True)


def load_status_summary(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Status and Sub-Status by Hauling Unit`` (month totals per reason)."""
    raw = _read_many(paths)
    if raw.empty:
        return raw

    frame = standardize_columns(raw)
    frame = drop_total_rows(frame, ["Equipment_ID"])
    frame["Equipment_ID"] = normalize_equipment_id(frame["Equipment_ID"])
    frame["Status_Category"] = clean_text(frame.get("Status_Category", pd.Series("", index=frame.index)))
    frame["Reason"] = strip_reason_code(frame.get("Status_Desc", pd.Series("", index=frame.index)))
    frame["Hours"] = to_number(frame.get("Duration_Hours", pd.Series(np.nan, index=frame.index)))
    frame = frame.dropna(subset=["Equipment_ID", "Hours"])
    frame = frame[frame["Hours"] > 0]
    frame = add_reason_classes(frame, "Reason")
    keep = ["Equipment_ID", "Fleet", "Status_Category", "Reason", "Reason_Class",
            "Addressable", "Hours", "Source_File"]
    return frame[[c for c in keep if c in frame.columns]].reset_index(drop=True)


def load_fuel(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Fuel Consumption by Hauling Unit`` (litres per dumper per chunk)."""
    raw = _read_many(paths)
    if raw.empty:
        return raw

    frame = standardize_columns(raw)
    frame = drop_total_rows(frame, ["Equipment_ID"])
    frame["Equipment_ID"] = normalize_equipment_id(frame["Equipment_ID"])
    frame["Fuel_Litres"] = to_number(frame.get("Fuel_Consumed", pd.Series(np.nan, index=frame.index)))
    frame = frame.dropna(subset=["Equipment_ID"])
    frame = frame[frame["Fuel_Litres"].fillna(0) > 0]
    grouped = (
        frame.groupby("Equipment_ID", as_index=False)
        .agg(Fuel_Litres=("Fuel_Litres", "sum"),
             Fleet=("Fleet", "first") if "Fleet" in frame.columns else ("Equipment_ID", "first"))
    )
    return grouped


def load_dumper_shift(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Dumper_QSE_Report``: operator-level shift records for dumpers.

    Supplies availability, breakdown hours, canteen-break minutes and
    first-load delay, which explain idle time the cycle report cannot see.
    """
    raw = _read_many(paths)
    if raw.empty:
        return raw

    frame = clean_data(standardize_columns(raw))
    numeric = {
        "Total_Trips": "Trips", "Run_Hours": "Run_Hours",
        "Breakdown_Hours": "Breakdown_Hours", "Available_Hours": "Available_Hours",
        "Canteen_Break_Min": "Canteen_Break_Min",
        "First_Load_Delay_Min": "First_Load_Delay_Min",
        "Last_Load_Delay_Min": "Last_Load_Delay_Min",
        "LEAD_DISTANCE": "Lead_Distance_Km",
        "WAIT_AT_LU": "Wait_At_Shovel_Min",
        "Queue_Dump": "Wait_At_Dump_Min",
        "BLASTING_DURATION": "Blasting_Min",
        "DAILY_MAITENANCE_DURATION": "Daily_Maintenance_Min",
        "Total_OB_Tonnage2": "OB_Tonnes",
        "Total_Coal_Tonnage": "Coal_Tonnes",
    }
    for source, target in numeric.items():
        frame[target] = to_number(frame[source]) if source in frame.columns else np.nan

    frame = frame.dropna(subset=["Equipment_ID", "Shift_Date"])
    frame["Tonnes"] = frame[["OB_Tonnes", "Coal_Tonnes"]].sum(axis=1, min_count=1)
    frame["Idle_Hours_Reported"] = (
        frame["Available_Hours"] - frame["Run_Hours"]
    ).clip(lower=0)

    keep = [
        "Shift_Date", "Shift", "Equipment_ID", "Operator", "Loading_Equipment",
        "Trips", "Run_Hours", "Breakdown_Hours", "Available_Hours",
        "Idle_Hours_Reported", "Canteen_Break_Min", "First_Load_Delay_Min",
        "Last_Load_Delay_Min", "Lead_Distance_Km", "Wait_At_Shovel_Min",
        "Wait_At_Dump_Min", "Blasting_Min", "Daily_Maintenance_Min",
        "OB_Tonnes", "Coal_Tonnes", "Tonnes", "Source_File",
    ]
    out = frame[[c for c in keep if c in frame.columns]]
    return out.reset_index(drop=True)


def load_tkph(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Productivity TKPH-TMPH``: per-dumper distances and travel hours.

    These distances are what proved the cycle-report column names are swapped,
    and they give the haul-length context for the model.
    """
    raw = _read_many(paths)
    if raw.empty:
        return raw

    frame = standardize_columns(raw)
    frame = drop_total_rows(frame, ["Equipment_ID"])
    frame["Equipment_ID"] = normalize_equipment_id(frame["Equipment_ID"])

    # Unnamed textbox columns hold the values; the labels sit in sibling columns.
    renames = {
        "Textbox169": "Tonnes", "Num_Loads": "Loads",
        "Haul_Distance": "Haul_Km", "Empty_Distance": "Empty_Km",
        "Textbox6": "Travel_Hours",
    }
    for source, target in renames.items():
        frame[target] = to_number(frame[source]) if source in frame.columns else np.nan

    frame = frame.dropna(subset=["Equipment_ID"])
    frame = frame[frame["Loads"].fillna(0) > 0]
    frame["Total_Km"] = frame[["Haul_Km", "Empty_Km"]].sum(axis=1, min_count=1)
    frame["Km_Per_Cycle"] = frame["Total_Km"] / frame["Loads"]
    frame["Avg_Speed_Kmph"] = frame["Total_Km"] / frame["Travel_Hours"].replace(0, np.nan)
    keep = ["Equipment_ID", "Fleet", "Loads", "Tonnes", "Haul_Km", "Empty_Km",
            "Total_Km", "Km_Per_Cycle", "Travel_Hours", "Avg_Speed_Kmph"]
    return frame[[c for c in keep if c in frame.columns]].drop_duplicates(
        subset=["Equipment_ID"]
    ).reset_index(drop=True)


def _clock_to_hour(series: pd.Series) -> pd.Series:
    """Convert a bare clock time (``6:38 AM``, ``13:10``) to hour of day."""
    text = series.astype(str).str.strip().str.upper()
    parts = text.str.extract(r"^(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?$")
    hour = pd.to_numeric(parts[0], errors="coerce")
    minute = pd.to_numeric(parts[1], errors="coerce")
    meridiem = parts[2]
    hour = hour.where(~((meridiem == "PM") & (hour < 12)), hour + 12)
    hour = hour.where(~((meridiem == "AM") & (hour == 12)), 0)
    return hour + minute / 60.0


def load_shovel_shift(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``SHOVEL_QSE_REPORT``: shovel-level shift records.

    This is the shovel twin of ``dumper_shift`` and the richest upstream signal
    on site: it records why a shovel was not loading (breakdown, marching,
    blasting, face preparation, daily maintenance) and how late the first and
    last load of the shift were. A dumper queue at the face is usually the
    downstream symptom of something in this table.
    """
    raw = _read_many(paths)
    if raw.empty:
        return raw

    frame = clean_data(standardize_columns(raw))
    # Raw-specific columns that have no shared canonical name.
    renames = {
        "TOTAL_LOAD": "Total_Load",
        "AVAILABLE_HOUR1": "Available_Hours",
        "RUN_HOUR": "Run_Hours",
        "MARCHING_HOUR": "Marching_Hours",
        "Face_Preparatin__min_": "Face_Preparation_Min",
        "FIRST_LOAD_START_TIME": "First_Load_Start_Time",
        "LAST_LOAD_TIME": "Last_Load_Time",
        "First_load_Delay": "First_Load_Delay_Min",
        "LAST_LOAD_DELAY_MIN": "Last_Load_Delay_Min",
        "Load_Locations1": "Load_Locations",
        "Total_OB_Tonnage2": "OB_Tonnes",
        "OB_TPH1": "OB_TPH",
        "Total_Coal_Tonnage": "Coal_Tonnes",
        "COAL_TPH": "Coal_TPH",
        "PRODUCTIVITY1": "Productivity",
        "BLASTING_START_TIME": "Blasting_Start_Time",
        "BLASTING_DELAY": "Blasting_Delay_Min",
        "DAILY_MAITENANCE_START_TIME": "Maintenance_Start_Time",
        "DAILY_MAITENANCE_END_TIME": "Maintenance_End_Time",
        "DAILY_MAITENANCE": "Maintenance_Min",
    }
    for source, target in renames.items():
        if source in frame.columns:
            frame[target] = frame[source]
            frame = frame.drop(columns=[source])

    numeric = [
        "Total_Load", "Available_Hours", "Run_Hours", "Breakdown_Hours",
        "Canteen_Break_Min", "Marching_Hours", "Face_Preparation_Min",
        "First_Load_Delay_Min", "Last_Load_Delay_Min", "OB_Tonnes", "OB_TPH",
        "Coal_Tonnes", "Coal_TPH", "Productivity", "Blasting_Delay_Min",
        "Maintenance_Min",
    ]
    for col in numeric:
        if col in frame.columns:
            frame[col] = to_number(frame[col])

    # These columns hold a bare clock time ("6:38 AM") with no date. Parsing
    # them as timestamps silently stamps them with today's date, and pinning
    # them to Shift_Date would be wrong for night shifts that cross midnight,
    # so they are exposed as an unambiguous hour of day instead.
    clocks = {
        "Equipment_Start_Time": "Equipment_Start_Hour",
        "First_Load_Start_Time": "First_Load_Hour",
        "Last_Load_Time": "Last_Load_Hour",
        "Blasting_Start_Time": "Blasting_Start_Hour",
        "Maintenance_Start_Time": "Maintenance_Start_Hour",
        "Maintenance_End_Time": "Maintenance_End_Hour",
    }
    for source, target in clocks.items():
        if source in frame.columns:
            frame[target] = _clock_to_hour(frame[source])

    keep = [
        "Shift_Date", "Shift", "Equipment_ID", "Operators", "Load_Locations",
        "Total_Load", "Available_Hours", "Run_Hours", "Breakdown_Hours",
        "Canteen_Break_Min", "Marching_Hours", "Face_Preparation_Min",
        "Blasting_Delay_Min", "Maintenance_Min",
        "First_Load_Delay_Min", "Last_Load_Delay_Min",
        "Coal_Tonnes", "OB_Tonnes", "Coal_TPH", "OB_TPH", "Productivity",
        *clocks.values(),
        "Source_File",
    ]
    out = frame[[c for c in keep if c in frame.columns]]
    return out.dropna(subset=["Equipment_ID", "Shift_Date"]).reset_index(drop=True)


def _map_local_columns(frame: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """Rename a frame using a raw-name -> canonical-name mapping without going
    through the shared ``column_mapping.json`` aliases, so report-specific
    columns like Coal_Quantity and OB_Quantity are not conflated with Payload.
    """
    out = frame.copy()
    new_cols = []
    for col in out.columns:
        base = re.sub(r"\.\d+$", "", str(col)).strip()
        new_cols.append(mapping.get(base, base))
    out.columns = new_cols
    return out


def load_hauling_summary(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Hauling Unit Summary Report``: dumper shift-level trip summary.

    Cross-check for trips, tonnes and first/last load timestamps. Some months
    are split into multiple files (e.g. 1-10, 11-20, 21-31 July); the loader
    stacks all of them.
    """
    raw = _read_many(paths)
    if raw.empty:
        return raw

    mapping = {
        "Equipment No": "Equipment_ID",
        "Shift Date": "Shift_Date",
        "Shift Ident": "Shift",
        "Total Trips": "Total_Trips",
        "Coal Trips": "Coal_Trips",
        "Coal Quantity": "Coal_Quantity",
        "OB Trips": "OB_Trips",
        "OB Quantity": "OB_Quantity",
        "Hauling Unit First Load": "First_Load_Time",
        "Hauling Unit Last Load": "Last_Load_Time",
    }
    frame = _map_local_columns(raw, mapping)
    frame = clean_data(frame)

    for col in ("Total_Trips", "Coal_Trips", "Coal_Quantity",
                "OB_Trips", "OB_Quantity"):
        if col in frame.columns:
            frame[col] = to_number(frame[col])

    for col in ("First_Load_Time", "Last_Load_Time"):
        if col in frame.columns:
            frame[col] = _smart_to_datetime(frame[col])

    keep = [
        "Shift_Date", "Shift", "Equipment_ID", "Total_Trips", "Coal_Trips",
        "Coal_Quantity", "OB_Trips", "OB_Quantity",
        "First_Load_Time", "Last_Load_Time", "Source_File",
    ]
    out = frame[[c for c in keep if c in frame.columns]]
    # The export is padded with several hundred entirely blank trailing rows.
    return out.dropna(subset=["Equipment_ID", "Shift_Date"]).reset_index(drop=True)


def _hms_to_minutes(series: pd.Series) -> pd.Series:
    """Convert ``HH:MM:SS`` durations to minutes.

    The hour field is a running total and regularly exceeds 24 (``155:38:06``),
    so this cannot go through ``pd.to_timedelta`` on a time-of-day parse.
    """
    text = series.astype(str).str.strip()
    parts = text.str.extract(r"^(\d+):(\d{2}):(\d{2})$")
    minutes = (
        pd.to_numeric(parts[0], errors="coerce") * 60
        + pd.to_numeric(parts[1], errors="coerce")
        + pd.to_numeric(parts[2], errors="coerce") / 60
    )
    return minutes


def _read_sections_many(
    paths: Iterable[str | Path], *required: str
) -> pd.DataFrame:
    """Stack the section matching ``required`` from each of several exports."""
    frames: list[pd.DataFrame] = []
    for path in paths:
        path = Path(path)
        frame = _pick_section(read_csv_sections(path), *required)
        if frame.empty:
            continue
        frame = frame.copy()
        frame["Source_File"] = path.name
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True, sort=False)
    payload = [c for c in combined.columns if c != "Source_File"]
    return combined.drop_duplicates(subset=payload).reset_index(drop=True)


def load_loading_unit_summary(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load the trip summary section of ``Loading Unit Summary Report``.

    The shovel twin to ``load_hauling_summary``: trips and tonnes per loading
    unit per shift, used to pair dumper counts to loader counts.

    The export actually stacks three unrelated tables in one CSV. Reading it as
    a single frame silently mixed all three and, because the shared column map
    aliases ``Coal_Quantity`` to ``Payload``, quietly dropped both tonnage
    columns. The sections are therefore split apart here and the raw header
    names are used directly. The other two sections are loaded by
    ``load_loading_unit_time`` and ``load_loading_routes``.
    """
    raw = _read_sections_many(paths, "EquipmentNo", "Total_Trips", "Coal_Quantity")
    if raw.empty:
        return raw

    frame = _map_local_columns(raw, {
        "EquipmentNo": "Equipment_ID",
        "LOAD_START_SHIFT_DATE": "Shift_Date",
        "Load_Start_Shift_Ident": "Shift",
        "Loading_Unit_First_Load": "First_Load_Time",
        "Loading_Unit_Last_Load": "Last_Load_Time",
    })
    frame = clean_data(frame)

    for col in ("Total_Trips", "Coal_Trips", "Coal_Quantity",
                "OB_Trips", "OB_Quantity"):
        if col in frame.columns:
            frame[col] = to_number(frame[col])

    for col in ("First_Load_Time", "Last_Load_Time"):
        if col in frame.columns:
            frame[col] = _smart_to_datetime(frame[col])

    keep = [
        "Shift_Date", "Shift", "Equipment_ID", "Total_Trips", "Coal_Trips",
        "Coal_Quantity", "OB_Trips", "OB_Quantity",
        "First_Load_Time", "Last_Load_Time", "Source_File",
    ]
    out = frame[[c for c in keep if c in frame.columns]]
    return out.dropna(subset=["Equipment_ID", "Shift_Date"]).reset_index(drop=True)


def load_loading_unit_time(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load the time-profile section of ``Loading Unit Summary Report``.

    Grain is loading unit x shift number for the whole period, so it does not
    join to the shift master. It is the only report that states how long each
    shovel spent *waiting for a truck* versus actually loading, which is the
    direct counterpart of the dumper-side queue at the face.

    All durations are ``HH:MM:SS`` running totals and are converted to minutes.
    """
    raw = _read_sections_many(paths, "EquipmentNo1", "Total_Waiting_time")
    if raw.empty:
        return raw

    frame = _map_local_columns(raw, {
        "EquipmentNo1": "Equipment_ID",
        "Shift_Ident": "Shift",
        "Run_Hours": "Run_Hours",
        "Total_Loading_time": "Loading_Min",
        "Avg_Loading_time": "Avg_Loading_Min",
        "Total_Waiting_time": "Waiting_Min",
        "Avg_Waiting_Time": "Avg_Waiting_Min",
        "Tea_breakfast_time": "Tea_Break_Min",
        "Total_Loading_Units_Availability": "Available_Min",
    })
    frame = clean_data(frame)

    for col in ("Loading_Min", "Avg_Loading_Min", "Waiting_Min",
                "Avg_Waiting_Min", "Tea_Break_Min", "Available_Min"):
        if col in frame.columns:
            frame[col] = _hms_to_minutes(frame[col])
    if "Run_Hours" in frame.columns:
        frame["Run_Hours"] = to_number(frame["Run_Hours"])

    frame["Waiting_Share"] = frame["Waiting_Min"] / (
        frame["Loading_Min"] + frame["Waiting_Min"]
    ).replace(0, np.nan)

    keep = ["Equipment_ID", "Shift", "Operator", "Run_Hours", "Available_Min",
            "Loading_Min", "Waiting_Min", "Waiting_Share", "Avg_Loading_Min",
            "Avg_Waiting_Min", "Tea_Break_Min", "Source_File"]
    out = frame[[c for c in keep if c in frame.columns]]
    return out.dropna(subset=["Equipment_ID"]).reset_index(drop=True)


def load_loading_routes(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load the route section of ``Loading Unit Summary Report``.

    One row per loading unit x shift x material x loading face x dumping face,
    carrying the lead distance for that route. This is the only per-shift source
    of haul distance; the TKPH report gives only a per-dumper monthly average.
    """
    raw = _read_sections_many(paths, "EquipmentNo2", "Lead_Distance")
    if raw.empty:
        return raw

    frame = _map_local_columns(raw, {
        "EquipmentNo2": "Equipment_ID",
        "LOAD_START_SHIFT_DATE1": "Shift_Date",
        "Load_Start_Shift_Ident1": "Shift",
        "Material_Ident": "Material",
        "Loading_Face": "Load_Location",
        "Dumping_Face": "Dump_Location",
        "Lead_Distance": "Lead_Km",
    })
    frame = clean_data(frame)
    frame["Lead_Km"] = to_number(frame["Lead_Km"])
    frame["Dump_Location"] = clean_text(frame["Dump_Location"])

    keep = ["Shift_Date", "Shift", "Equipment_ID", "Material",
            "Load_Location", "Dump_Location", "Lead_Km", "Source_File"]
    out = frame[[c for c in keep if c in frame.columns]]
    return out.dropna(subset=["Equipment_ID", "Shift_Date"]).reset_index(drop=True)


_PRODUCTION_METRICS: tuple[tuple[str, str], ...] = (
    # Checked in order; the OB variants must be tested before the plain ones.
    ("Location_Name", "Location_Name"),
    ("AVAILABILITY", "Available_Hours"),
    ("Break_Down_Hours", "Breakdown_Hours"),
    ("Trips", "Trips"),
    ("Output_in_Cum_OB", "OB_Cum"),
    ("Output_in_Cum", "Coal_Cum"),
    ("Utilization_Perf_Cum_OB", "OB_Cum_Per_Hour"),
    ("Utilization_Perf_Cum_ob", "OB_Cum_Per_Hour"),
    ("Utilization_Perf_Cum_coal", "Coal_Cum_Per_Hour"),
    ("Utilization_Perf_Cum", "Coal_Cum_Per_Hour"),
    ("Utilization", "Utilized_Hours"),
)


def _production_metric(name: str) -> str | None:
    """Map a shift-prefixed production column to a canonical metric name.

    SSRS suffixes every column with a disambiguating digit that differs between
    the three sub-reports (``First_Shift_Trips1`` vs ``First_Shift_Trips2``),
    so matching is done on a trailing-digit-stripped prefix.
    """
    base = re.sub(r"\d+$", "", name)
    for token, canonical in _PRODUCTION_METRICS:
        if base == token or base == re.sub(r"\d+$", "", token):
            return canonical
    return None


def load_daily_production(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Daily Production Report QSE``: period production totals by shift.

    Despite the name this is NOT daily grain. It is one row per machine for the
    whole reporting period (July 2026 here), with a repeated block of columns
    for the first, second and third shift. The loader unpivots it to one row per
    machine x shift number and carries the period bounds from the banner.

    The file stacks three sub-reports with slightly different column sets and
    different SSRS suffixes: shovels, dumpers and payloaders. They are parsed
    separately and tagged with ``Equipment_Class`` rather than being merged
    positionally, which previously mixed the three schemas together.
    """
    frames: list[pd.DataFrame] = []
    for path in paths:
        path = Path(path)
        rows, _enc = _read_rows(path, max_rows=200)
        period_start = period_end = pd.NaT
        for row in rows[:4]:
            cells = [c.strip() for c in row if c.strip()]
            if len(cells) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
                period_start = pd.to_datetime(cells[0], errors="coerce")
                period_end = pd.to_datetime(cells[1], errors="coerce")
                break

        for section in read_csv_sections(path):
            id_col = next(
                (c for c in section.columns if re.fullmatch(r"EquipmentNo\d*", c)), None
            )
            if id_col is None:
                continue
            owner_col = next(
                (c for c in section.columns if re.fullmatch(r"Owner\d*", c)), None
            )

            unpivoted: list[pd.DataFrame] = []
            for shift_num, prefix in enumerate(("First", "Second", "Third"), start=1):
                renames = {
                    col: metric
                    for col in section.columns
                    if col.startswith(f"{prefix}_Shift_")
                    and (metric := _production_metric(col[len(prefix) + 7:]))
                }
                if not renames:
                    continue
                sub = section[[id_col] + ([owner_col] if owner_col else []) + list(renames)]
                sub = sub.rename(columns={id_col: "Equipment_ID", **renames})
                if owner_col:
                    sub = sub.rename(columns={owner_col: "Owner"})
                sub = sub.copy()
                sub["Shift"] = shift_num
                unpivoted.append(sub)

            if not unpivoted:
                continue
            stacked = pd.concat(unpivoted, ignore_index=True)
            stacked["Period_Start"] = period_start
            stacked["Period_End"] = period_end
            stacked["Source_File"] = path.name
            frames.append(stacked)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True, sort=False)
    out = clean_data(out)
    out = out.dropna(subset=["Equipment_ID"])

    for col in ("Available_Hours", "Utilized_Hours", "Breakdown_Hours", "Trips",
                "Coal_Cum", "OB_Cum", "Coal_Cum_Per_Hour", "OB_Cum_Per_Hour"):
        if col in out.columns:
            out[col] = to_number(out[col])

    out["Equipment_Class"] = np.select(
        [
            out["Equipment_ID"].str.startswith("RD", na=False),
            out["Equipment_ID"].str.startswith("PL", na=False),
        ],
        ["Dumper", "Payloader"],
        default="Shovel",
    )
    # Location_Name is a comma-joined list of every face the machine touched.
    if "Location_Name" in out.columns:
        out["Location_Name"] = out["Location_Name"].astype(str).str.strip(" ,")

    order = ["Equipment_ID", "Equipment_Class", "Owner", "Shift", "Location_Name",
             "Available_Hours", "Utilized_Hours", "Breakdown_Hours", "Trips",
             "Coal_Cum", "OB_Cum", "Coal_Cum_Per_Hour", "OB_Cum_Per_Hour",
             "Period_Start", "Period_End", "Source_File"]
    return out[[c for c in order if c in out.columns]].reset_index(drop=True)


def _extract_equipment_id(name: str, pattern: str = r"(?:EX|PL)[-_]?(\d+)") -> str | None:
    """Pull a normalised loader/shovel id like EX001 or PL003 from a filename."""
    match = re.search(pattern, name.upper())
    if not match:
        return None
    prefix = match.group(0)[:2].upper()
    number = match.group(1)
    return f"{prefix}{number}"


def load_loader_profile(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Loading Unit Time Profile``: event-grain loader status log.

    One file per loading unit, eight loaders in all. Each row is a status change
    (Waiting, LU Loading, Spotting, ...) with the dumper being served in the
    ``Hauling Unit`` column. This is the shovel-side twin to the dumper
    ``idle_events`` report and the only direct measurement of queueing at the
    face from the loader's point of view.

    Two quirks: the ``Timestamp`` column is date-only despite its name, so it is
    exposed as ``Shift_Date``; and the loading unit itself is only identifiable
    from the filename, so it is parsed from there.
    """
    raw = _read_many(paths)
    if raw.empty:
        return raw

    mapping = {
        "Timestamp": "Shift_Date",
        "Status Code": "Status",
        "Sub_Status_Desc": "Sub_Status",
        "Hauling Unit": "Hauling_Unit",
    }
    frame = _map_local_columns(raw, mapping)
    frame = clean_data(frame)

    frame["Loading_Unit"] = normalize_equipment_id(
        frame["Source_File"].map(_extract_equipment_id)
    )
    frame["Hauling_Unit"] = normalize_equipment_id(frame["Hauling_Unit"])
    frame["Status"] = clean_text(frame["Status"])

    if "Duration_Min" in frame.columns:
        frame["Duration_Min"] = to_number(frame["Duration_Min"])

    keep = [
        "Shift_Date", "Loading_Unit", "Hauling_Unit", "Status", "Sub_Status",
        "Duration_Min", "Source_File",
    ]
    out = frame[[c for c in keep if c in frame.columns]]
    # SSRS repeats the header on every page break; those rows fail date parsing.
    return out[out["Shift_Date"].notna()].reset_index(drop=True)


def load_operator(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Operator Performance Report``: per-operator totals by shift number.

    Grain is operator x shift-number for the whole period, NOT per day, so this
    cannot be joined to the shift master. It is the only source of per-operator
    break duration and tonnes-per-run-hour.

    The export stacks TWO sub-reports whose columns line up positionally but
    mean different things:

    * hauling-unit operators -- ``HaulDistance`` is real haul km and the next
      column is tonnes per run hour;
    * loading-unit operators -- there is no distance at all, and the same
      position instead holds tonnes per run hour.

    Reading the file as one table therefore mixes haul km with a productivity
    rate. The sections are matched on their distinct headers, and productivity
    is recomputed from tonnes and run hours rather than trusted from either.
    """
    hauling = _read_sections_many(paths, "SHIFT_IDENT", "HaulDistance", "TON_KM")
    loading = _read_sections_many(paths, "SHIFT_IDENT2", "Textbox22")
    if hauling.empty and loading.empty:
        return pd.DataFrame()

    hauling = _map_local_columns(hauling, {
        "SHIFT_IDENT": "Shift",
        "Operator_Name": "Operator",
        "trips": "Trips",
        "Tonnage": "Tonnes",
        "RunHours": "Run_Hours",
        "HaulDistance": "Haul_Km",
        "TON_KM": "Tonne_Km",
        "Hauling": "Hauling_Min",
        "EmptyTravel": "Empty_Travel_Min",
        "BreakDuration": "Break_Min",
        "ShiftCharge": "Shift_Change_Min",
    })
    hauling["Operator_Class"] = "Hauling unit"

    loading = _map_local_columns(loading, {
        "SHIFT_IDENT2": "Shift",
        "Operator_PNo1": "Operator_PNo",
        "Operator_Name1": "Operator",
        "trips1": "Trips",
        "Tonnage1": "Tonnes",
        "RunHours2": "Run_Hours",
    })
    loading["Operator_Class"] = "Loading unit"
    # Textbox22 / Others1 are unlabelled in the export and reconcile to no
    # documented quantity, so they are dropped rather than guessed at.
    loading = loading.drop(columns=[c for c in ("Textbox22", "Others1") if c in loading.columns])

    frame = pd.concat([hauling, loading], ignore_index=True, sort=False)
    frame = clean_data(frame)
    frame = drop_total_rows(frame, ["Operator", "Operator_PNo"])

    numeric = ["Trips", "Tonnes", "Run_Hours", "Haul_Km", "Tonne_Km",
               "Hauling_Min", "Empty_Travel_Min", "Break_Min", "Shift_Change_Min"]
    for col in numeric:
        if col in frame.columns:
            frame[col] = to_number(frame[col])

    # The name column embeds the payroll number, e.g. "AJIT KR. SINGH (266798)".
    frame["Operator"] = (
        frame["Operator"].astype(str)
        .str.replace(r"\s*\(\d+\)\s*$", "", regex=True).str.strip()
    )
    frame["Operator_PNo"] = frame["Operator_PNo"].astype(str).str.strip()
    frame["Tonnes_Per_Run_Hour"] = frame["Tonnes"] / frame["Run_Hours"].replace(0, np.nan)

    keep = ["Shift", "Operator_PNo", "Operator", "Operator_Class", "Trips", "Tonnes",
            "Run_Hours", "Tonnes_Per_Run_Hour", "Haul_Km", "Tonne_Km",
            "Hauling_Min", "Empty_Travel_Min", "Break_Min", "Shift_Change_Min",
            "Source_File"]
    out = frame[[c for c in keep if c in frame.columns]]
    return out[out["Operator"].ne("") & out["Operator"].ne("nan")].reset_index(drop=True)


def load_payload_cycles(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Cycle Time and Payload by Hauling Unit and Load``: per-load cycles.

    An independent per-cycle record with real load and dump timestamps, which
    the main ``cycles`` report lacks (its ``START_TIMESTAMP`` is unreliable).
    That makes this the only source for genuine time-of-day cycle analysis.

    The export is an SSRS matrix, so most columns are repeated group labels and
    subtotals; only the eight real fields are kept.
    """
    raw = _read_many(paths)
    if raw.empty:
        return raw

    mapping = {
        "Textbox62": "Shift_Date",
        "Textbox54": "Shift",
        "Start_Timestamp": "Load_Timestamp",
        "End_Timestamp": "Dump_Timestamp",
        "Duration3": "Cycle_Min",
    }
    frame = _map_local_columns(raw, mapping)
    frame = clean_data(frame)

    for col in ("Cycle_Min", "Payload"):
        if col in frame.columns:
            frame[col] = to_number(frame[col])

    if "Load_Location" in frame.columns:
        # "ACD COAL STOCK - ACD COAL STOCK" is origin - destination.
        split = frame["Load_Location"].astype(str).str.split(r"\s+-\s+", n=1, expand=True)
        frame["Load_Location"] = clean_text(split[0])
        frame["Dump_Location"] = clean_text(split[1]) if split.shape[1] > 1 else pd.NA

    keep = ["Shift_Date", "Shift", "Equipment_ID", "Load_Location", "Dump_Location",
            "Load_Timestamp", "Dump_Timestamp", "Payload", "Cycle_Min", "Source_File"]
    out = frame[[c for c in keep if c in frame.columns]]
    out = out.dropna(subset=["Equipment_ID", "Shift_Date"])

    # A handful of rows span many hours because the truck was loaded and then
    # parked; flag them rather than dropping so the count stays reconcilable.
    out = out.copy()
    out["Implausible_Cycle"] = out["Cycle_Min"] > 180
    return out.reset_index(drop=True)


def load_status_category(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Time by Status Category, Fleet and Equipment``.

    The report gives the total hours each equipment spent in the five status
    categories (delay, down, standby, goh, noh) over the reporting period. The
    first section is a chart; the usable second section is the data table.
    """
    raw = _read_sections_many(paths, "EQUIP_IDENT", "delay", "down")
    if raw.empty:
        return raw

    frame = _map_local_columns(raw, {
        "firstDayWeek3": "Fleet",
        "EQUIP_IDENT": "Equipment_ID",
        "delay": "Delay_Hours",
        "down": "Down_Hours",
        "standby": "Standby_Hours",
        "goh": "GOH_Hours",
        "noh": "NOH_Hours",
        "duration": "Duration_Hours",
    })
    frame = clean_data(frame)

    for col in ("Delay_Hours", "Down_Hours", "Standby_Hours", "GOH_Hours",
                "NOH_Hours", "Duration_Hours"):
        if col in frame.columns:
            frame[col] = to_number(frame[col])

    frame["Idle_Hours"] = frame["Delay_Hours"].fillna(0) + frame["Standby_Hours"].fillna(0)

    keep = ["Equipment_ID", "Fleet", "Delay_Hours", "Down_Hours",
            "Standby_Hours", "GOH_Hours", "NOH_Hours", "Idle_Hours",
            "Duration_Hours", "Source_File"]
    return frame[[c for c in keep if c in frame.columns]].reset_index(drop=True)


def load_engine_hours(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Load ``Net Engine Hours by Fleet and Hauling Unit``.

    One row per engine-run interval, with first and last entry timestamps and
    the net hours run during the interval. The SSRS labels (``First Entry``,
    ``Last Entry``, ``Net Hours``) are stored in the first data cells, so the
    left-hand ``textbox`` columns are dropped.
    """
    raw = _read_sections_many(paths, "EQUIP_IDENT", "ORIGIN_SHORT_NAME2", "PRODUCTION2")
    if raw.empty:
        return raw

    # Drop the SSRS textbox columns that hold only the report column labels.
    label_cols = [c for c in raw.columns if c.startswith(("textbox", "Textbox"))]
    frame = raw.drop(columns=label_cols)
    frame = _map_local_columns(frame, {
        "EQUIP_IDENT": "Equipment_ID",
        "FLEET_IDENT": "Fleet",
        "ORIGIN_SHORT_NAME2": "First_Entry",
        "MAX_TIMESTAMP": "Last_Entry",
        "DESTINATION_SHORT_NAME2": "Start_Cumulative_Hours",
        "NUM_LOADS2": "End_Cumulative_Hours",
        "PRODUCTION2": "Net_Hours",
    })
    frame = clean_data(frame)

    for col in ("Start_Cumulative_Hours", "End_Cumulative_Hours", "Net_Hours"):
        if col in frame.columns:
            frame[col] = to_number(frame[col])
    frame["First_Entry"] = _smart_to_datetime(frame["First_Entry"])
    frame["Last_Entry"] = _smart_to_datetime(frame["Last_Entry"])

    keep = ["Equipment_ID", "Fleet", "First_Entry", "Last_Entry",
            "Start_Cumulative_Hours", "End_Cumulative_Hours", "Net_Hours",
            "Source_File"]
    out = frame[[c for c in keep if c in frame.columns]].copy()
    out = out[out["First_Entry"] < out["Last_Entry"]]
    out = out[out["Net_Hours"] > 0]
    return out.reset_index(drop=True)


LOADERS = {
    "cycles": load_cycles,
    "idle_events": load_idle_events,
    "delay_events": load_delay_events,
    "status_summary": load_status_summary,
    "fuel": load_fuel,
    "dumper_shift": load_dumper_shift,
    "tkph": load_tkph,
    "shovel_shift": load_shovel_shift,
    "hauling_summary": load_hauling_summary,
    "loading_unit_summary": load_loading_unit_summary,
    "daily_production": load_daily_production,
    "loader_profile": load_loader_profile,
    "operator": load_operator,
    "payload_cycles": load_payload_cycles,
    "loading_unit_time": load_loading_unit_time,
    "loading_routes": load_loading_routes,
    "status_category": load_status_category,
    "engine_hours": load_engine_hours,
}


# ==========================================================================  #
# 7. Master table builders
# ==========================================================================  #
def _mode(series: pd.Series) -> Any:
    """Most frequent non-null value, or None."""
    cleaned = series.dropna()
    if cleaned.empty:
        return None
    counts = cleaned.value_counts()
    return counts.index[0] if len(counts) else None


def build_shift_master(
    cycles: pd.DataFrame,
    delay_events: pd.DataFrame | None = None,
    idle_events: pd.DataFrame | None = None,
    dumper_shift: pd.DataFrame | None = None,
    tkph: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build the modelling grain: one row per dumper x shift date x shift.

    Combines in-cycle idle (from the cycle report), out-of-cycle reason-coded
    delay (from the delays report), measured stand-still time (from the idle
    event report) and shift context (availability, breakdown, tonnes).

    The row spine is the UNION of the cycle report and the dumper delay report,
    not the cycle report alone. A dumper that completed no cycles in a shift --
    because it was down for the whole eight hours -- appears nowhere in the
    cycle export, so building the spine from cycles only would silently drop
    both the machine and every hour it stood unavailable. Those shifts are the
    single largest loss in the dataset, so they are carried here with
    ``Cycles == 0`` and flagged by ``Zero_Cycle_Shift``.
    """
    if cycles.empty:
        return pd.DataFrame()

    keys = ["Equipment_ID", "Shift_Date", "Shift"]
    bucket_sums = {col: (col, "sum") for col in config.ALL_CYCLE_BUCKETS if col in cycles.columns}

    master = cycles.groupby(keys, as_index=False, observed=True).agg(
        Cycles=("Cycle_Time", "size"),
        Cycle_Min=("Cycle_Time", "sum"),
        Cycle_Min_Mean=("Cycle_Time", "mean"),
        Cycle_Idle_Min=("Idle_Min", "sum"),
        Queue_Min=("Queue_Min", "sum"),
        Stopped_Min=("Stopped_Min", "sum"),
        Productive_Min=("Productive_Min", "sum"),
        Spotting_Min=("Spotting_Min", "sum"),
        Payload_Tonnes=("Payload", "sum"),
        Site=("Site", _mode),
        Route=("Route", _mode),
        Operator=("Operator", _mode),
        Loading_Unit=("Loading_Unit", _mode),
        Material=("Material", _mode),
        **bucket_sums,
    )
    master["Zero_Cycle_Shift"] = False

    # Extend the spine with dumper-shifts that logged reason-coded delay but no
    # cycle at all. Without this the fleet view is survivorship-biased: it only
    # ever describes trucks that actually hauled.
    if delay_events is not None and not delay_events.empty:
        delay_keys = (
            delay_events.loc[delay_events["Equipment_Class"] == "Dumper", keys]
            .drop_duplicates()
        )
        missing = delay_keys.merge(master[keys], on=keys, how="left", indicator=True)
        missing = missing.loc[missing["_merge"] == "left_only", keys]
        if not missing.empty:
            filler = missing.reset_index(drop=True)
            filler["Cycles"] = 0
            zero_cols = [
                "Cycle_Min", "Cycle_Idle_Min", "Queue_Min", "Stopped_Min",
                "Productive_Min", "Spotting_Min", "Payload_Tonnes",
            ] + list(bucket_sums)
            for col in zero_cols:
                filler[col] = 0.0
            # The mean cycle time of zero cycles is undefined, not zero.
            filler["Cycle_Min_Mean"] = np.nan
            for col in ("Site", "Route", "Operator", "Loading_Unit", "Material"):
                filler[col] = None
            filler["Zero_Cycle_Shift"] = True
            master = pd.concat([master, filler], ignore_index=True)

    if delay_events is not None and not delay_events.empty:
        dumper_delays = delay_events[delay_events["Equipment_Class"] == "Dumper"]
        delay_totals = dumper_delays.groupby(keys, as_index=False, observed=True).agg(
            Delay_Min=("Delay_Min", "sum"),
            Delay_Events=("Delay_Min", "size"),
        )
        master = master.merge(delay_totals, on=keys, how="left")

        pivot = (
            dumper_delays.pivot_table(
                index=keys, columns="Reason_Class", values="Delay_Min",
                aggfunc="sum", fill_value=0.0, observed=True,
            )
            .rename(columns=lambda c: f"Delay_{c}_Min")
            .reset_index()
        )
        master = master.merge(pivot, on=keys, how="left")

        addressable = (
            dumper_delays[dumper_delays["Addressable"]]
            .groupby(keys, as_index=False, observed=True)
            .agg(Addressable_Delay_Min=("Delay_Min", "sum"))
        )
        master = master.merge(addressable, on=keys, how="left")

    if idle_events is not None and not idle_events.empty:
        measured = idle_events.groupby(keys, as_index=False, observed=True).agg(
            Measured_Idle_Min=("Idle_Min", "sum"),
            Idle_Events=("Idle_Min", "size"),
            Longest_Idle_Min=("Idle_Min", "max"),
        )
        master = master.merge(measured, on=keys, how="left")

    if dumper_shift is not None and not dumper_shift.empty:
        context_cols = [
            "Run_Hours", "Breakdown_Hours", "Available_Hours", "Canteen_Break_Min",
            "First_Load_Delay_Min", "Lead_Distance_Km", "Blasting_Min",
            "Daily_Maintenance_Min", "Tonnes",
        ]
        available = [c for c in context_cols if c in dumper_shift.columns]
        context = dumper_shift.groupby(keys, as_index=False, observed=True)[available].sum(
            min_count=1
        )
        master = master.merge(context, on=keys, how="left", suffixes=("", "_ctx"))

    if tkph is not None and not tkph.empty:
        haul = tkph[["Equipment_ID", "Km_Per_Cycle", "Avg_Speed_Kmph"]]
        master = master.merge(haul, on="Equipment_ID", how="left")

    # Loading-unit context. Queueing is a supply/demand problem: idle rises when
    # too many dumpers chase one shovel, or when that shovel is itself delayed.
    loader_keys = ["Loading_Unit", "Shift_Date", "Shift"]
    loader_load = (
        cycles.dropna(subset=["Loading_Unit"])
        .groupby(loader_keys, as_index=False, observed=True)
        .agg(Dumpers_Per_Loader=("Equipment_ID", "nunique"),
             Loader_Cycles=("Cycle_Time", "size"))
    )
    master = master.merge(loader_load, on=loader_keys, how="left")

    if delay_events is not None and not delay_events.empty:
        loader_delays = delay_events[delay_events["Equipment_Class"] != "Dumper"].copy()
        if not loader_delays.empty:
            loader_delays = loader_delays.rename(columns={"Equipment_ID": "Loading_Unit"})
            loader_totals = loader_delays.groupby(
                loader_keys, as_index=False, observed=True
            ).agg(Loader_Delay_Min=("Delay_Min", "sum"),
                  Loader_Delay_Events=("Delay_Min", "size"))
            master = master.merge(loader_totals, on=loader_keys, how="left")

    for col in ("Delay_Min", "Addressable_Delay_Min", "Measured_Idle_Min",
                "Delay_Events", "Idle_Events", "Loader_Delay_Min",
                "Loader_Delay_Events"):
        if col in master.columns:
            master[col] = master[col].fillna(0.0)

    # Total idle a supervisor would recognise: standing still inside the cycle
    # plus every reason-coded delay logged against the machine.
    master["Total_Idle_Min"] = master["Cycle_Idle_Min"] + master.get(
        "Delay_Min", pd.Series(0.0, index=master.index)
    )
    master["Operating_Hours"] = master["Cycle_Min"] / 60.0
    # Every ratio below divides by a quantity that is legitimately zero on a
    # zero-cycle shift, so each one is guarded to yield NaN rather than inf.
    operating = master["Operating_Hours"].to_numpy(dtype="float64")
    cycle_min = master["Cycle_Min"].to_numpy(dtype="float64")
    master["Idle_Min_Per_Operating_Hour"] = np.where(
        operating > 0, master["Cycle_Idle_Min"] / master["Operating_Hours"], np.nan
    )
    master["Idle_Share"] = np.where(
        cycle_min > 0,
        (master["Cycle_Idle_Min"] / master["Cycle_Min"]).clip(0, 1),
        np.nan,
    )
    master["Tonnes_Per_Hour"] = np.where(
        operating > 0, master["Payload_Tonnes"] / master["Operating_Hours"], np.nan
    )
    master["Day_Of_Week"] = master["Shift_Date"].dt.day_name()
    master["Day"] = master["Shift_Date"].dt.day
    master["Week"] = master["Shift_Date"].dt.isocalendar().week.astype("int64")
    master["Is_Weekend"] = master["Shift_Date"].dt.dayofweek.isin([5, 6])
    master["Equipment_Fleet"] = master["Equipment_ID"].str.extract(
        r"^([A-Z]+)", expand=False
    )
    return master.sort_values(["Shift_Date", "Shift", "Equipment_ID"]).reset_index(drop=True)


def build_hourly_master(
    idle_events: pd.DataFrame, delay_events: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Build an hour-of-day profile of idle and reason-coded delay.

    Only the two reports carrying real clock times are used, because the cycle
    report's timestamps are unreliable.
    """
    frames: list[pd.DataFrame] = []

    if idle_events is not None and not idle_events.empty:
        frames.append(
            idle_events.groupby(["Shift_Date", "Shift", "Hour"], as_index=False, observed=True)
            .agg(Idle_Min=("Idle_Min", "sum"),
                 Idle_Events=("Idle_Min", "size"),
                 Dumpers=("Equipment_ID", "nunique"))
        )

    if delay_events is not None and not delay_events.empty:
        dumper_delays = delay_events[delay_events["Equipment_Class"] == "Dumper"]
        frames.append(
            dumper_delays.groupby(["Shift_Date", "Shift", "Hour"], as_index=False, observed=True)
            .agg(Delay_Min=("Delay_Min", "sum"),
                 Delay_Events=("Delay_Min", "size"))
        )

    if not frames:
        return pd.DataFrame()

    hourly = frames[0]
    for extra in frames[1:]:
        hourly = hourly.merge(extra, on=["Shift_Date", "Shift", "Hour"], how="outer")

    numeric = hourly.select_dtypes(include="number").columns
    hourly[numeric] = hourly[numeric].fillna(0.0)
    hourly["Day_Of_Week"] = hourly["Shift_Date"].dt.day_name()
    return hourly.sort_values(["Shift_Date", "Hour"]).reset_index(drop=True)


def build_reason_master(
    delay_events: pd.DataFrame, status_summary: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Rank every idle reason by hours lost, with its management class.

    ``Status and Sub-Status by Hauling Unit`` is used to cross-check the delay
    totals; a large disagreement is surfaced in the data-quality panel.
    """
    if delay_events is None or delay_events.empty:
        return pd.DataFrame()

    dumper_delays = delay_events[delay_events["Equipment_Class"] == "Dumper"]
    reasons = (
        dumper_delays.groupby(["Reason", "Reason_Class", "Addressable"], as_index=False, observed=True)
        .agg(Hours=("Delay_Min", lambda s: s.sum() / 60.0),
             Events=("Delay_Min", "size"),
             Mean_Min=("Delay_Min", "mean"),
             Median_Min=("Delay_Min", "median"),
             Dumpers=("Equipment_ID", "nunique"))
    )
    total = reasons["Hours"].sum()
    reasons["Share_Pct"] = np.where(total > 0, reasons["Hours"] / total * 100, 0.0)

    if status_summary is not None and not status_summary.empty:
        cross = (
            status_summary.groupby("Reason", as_index=False, observed=True)
            .agg(Hours_Cross_Check=("Hours", "sum"))
        )
        reasons = reasons.merge(cross, on="Reason", how="left")

    reasons["Lever"] = reasons["Reason"].map(
        lambda r: config.IDLE_LEVERS.get(r, {}).get("lever", "")
    )
    return reasons.sort_values("Hours", ascending=False).reset_index(drop=True)


def build_group_summary(shifts: pd.DataFrame, key: str, min_shifts: int = 5) -> pd.DataFrame:
    """Aggregate idle metrics for any grouping column (dumper, shovel, route, operator).

    Shared by the Fleet & risk ranking and Action playbook pages so both
    present identical numbers for the same filter selection.
    """
    if shifts.empty or key not in shifts.columns:
        return pd.DataFrame()

    frame = shifts.dropna(subset=[key])
    grouped = frame.groupby(key, as_index=False).agg(
        Dumper_Shifts=("Total_Idle_Min", "size"),
        Cycles=("Cycles", "sum"),
        Cycle_Min=("Cycle_Min", "sum"),
        Idle_Min=("Total_Idle_Min", "sum"),
        Cycle_Idle_Min=("Cycle_Idle_Min", "sum"),
        Queue_Min=("Queue_Min", "sum"),
        Tonnes=("Payload_Tonnes", "sum"),
    )
    grouped = grouped[grouped["Dumper_Shifts"] >= min_shifts]
    grouped["Idle hours"] = grouped["Idle_Min"] / 60
    grouped["Idle h per shift"] = grouped["Idle_Min"] / grouped["Dumper_Shifts"] / 60
    grouped["Idle % of cycle"] = (
        grouped["Cycle_Idle_Min"] / grouped["Cycle_Min"].replace(0, np.nan) * 100
    )
    grouped["Queue min per cycle"] = grouped["Queue_Min"] / grouped["Cycles"].replace(0, np.nan)
    grouped["Tonnes per op hour"] = grouped["Tonnes"] / (
        grouped["Cycle_Min"] / 60
    ).replace(0, np.nan)
    return grouped.sort_values("Idle hours", ascending=False).reset_index(drop=True)


def build_equipment_master(
    shifts: pd.DataFrame,
    fuel: pd.DataFrame | None = None,
    tkph: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Per-dumper league table for the month, including measured fuel burn."""
    if shifts.empty:
        return pd.DataFrame()

    agg = {
        "Shifts_Worked": ("Shift_Date", "nunique"),
        "Cycles": ("Cycles", "sum"),
        "Cycle_Min": ("Cycle_Min", "sum"),
        "Cycle_Idle_Min": ("Cycle_Idle_Min", "sum"),
        "Queue_Min": ("Queue_Min", "sum"),
        "Stopped_Min": ("Stopped_Min", "sum"),
        "Payload_Tonnes": ("Payload_Tonnes", "sum"),
        "Site": ("Site", _mode),
    }
    for col in ("Delay_Min", "Addressable_Delay_Min", "Measured_Idle_Min",
                "Breakdown_Hours", "Available_Hours"):
        if col in shifts.columns:
            agg[col] = (col, "sum")

    equipment = shifts.groupby("Equipment_ID", as_index=False, observed=True).agg(**agg)
    equipment["Idle_Share"] = (
        equipment["Cycle_Idle_Min"] / equipment["Cycle_Min"].replace(0, np.nan)
    )
    equipment["Idle_Min_Per_Cycle"] = (
        equipment["Cycle_Idle_Min"] / equipment["Cycles"].replace(0, np.nan)
    )
    equipment["Tonnes_Per_Operating_Hour"] = (
        equipment["Payload_Tonnes"] / (equipment["Cycle_Min"] / 60).replace(0, np.nan)
    )

    if fuel is not None and not fuel.empty:
        equipment = equipment.merge(
            fuel[["Equipment_ID", "Fuel_Litres"]], on="Equipment_ID", how="left"
        )
        equipment["Litres_Per_Tonne"] = (
            equipment["Fuel_Litres"] / equipment["Payload_Tonnes"].replace(0, np.nan)
        )

    if tkph is not None and not tkph.empty:
        equipment = equipment.merge(
            tkph[["Equipment_ID", "Km_Per_Cycle", "Avg_Speed_Kmph", "Total_Km"]],
            on="Equipment_ID", how="left",
        )

    return equipment.sort_values("Cycle_Idle_Min", ascending=False).reset_index(drop=True)


# ==========================================================================  #
# 8. Feature engineering
# ==========================================================================  #
NUMERIC_FEATURES = [
    # Haul geometry: a per-dumper monthly constant from the TKPH report, not a
    # same-shift outcome, so it is safe to use.
    "Km_Per_Cycle", "Avg_Speed_Kmph",
    # Congestion. Queue idle is a supply/demand problem between trucks and shovels.
    "Dumpers_Per_Loader", "Loader_Cycles", "Shift_Load_Dumpers",
    "Loader_Share_Of_Fleet",
    # The shovel's own lost time, which pushes its trucks into queue.
    "Loader_Delay_Min", "Loader_Delay_Events",
    # Calendar position within the month.
    "Day", "Week",
    # History of the target, strictly past-only.
    "Idle_Lag_1", "Idle_Lag_2", "Idle_Roll_3", "Idle_Roll_7",
    "Equipment_Idle_Expanding", "Loader_Idle_Expanding", "Route_Idle_Expanding",
]

# ``Shift_Cat`` rather than ``Shift``: the encoder needs a string, but ``Shift``
# must stay numeric so the dashboard filters keep working on the same frame.
CATEGORICAL_FEATURES = [
    "Shift_Cat", "Day_Of_Week", "Site", "Material", "Equipment_Fleet", "Loading_Unit",
]

HISTORY_FEATURES = [
    "Idle_Lag_1", "Idle_Lag_2", "Idle_Roll_3", "Idle_Roll_7",
    "Equipment_Idle_Expanding", "Loader_Idle_Expanding", "Route_Idle_Expanding",
]


def engineer_features(shifts: pd.DataFrame, target: str | None = None) -> pd.DataFrame:
    """Add history and congestion features to the shift master.

    Every history feature is shifted by one shift so the model can only ever see
    the past. Without the shift, a group mean would contain the very row it is
    used to predict and the score would be meaningless.

    Args:
        shifts: Output of ``build_shift_master``.
        target: Column whose history is summarised. Defaults to
            ``config.TARGET_SHIFT``.
    """
    if shifts.empty:
        return shifts

    target = target or config.TARGET_SHIFT
    frame = shifts.sort_values(["Equipment_ID", "Shift_Date", "Shift"]).copy()

    if target in frame.columns:
        by_equipment = frame.groupby("Equipment_ID", observed=True)[target]
        frame["Idle_Lag_1"] = by_equipment.shift(1)
        frame["Idle_Lag_2"] = by_equipment.shift(2)
        # transform keeps each window inside one dumper's own history; calling
        # .rolling() on a plain shifted Series would bleed across dumpers.
        frame["Idle_Roll_3"] = by_equipment.transform(
            lambda s: s.shift(1).rolling(3, min_periods=1).mean()
        )
        frame["Idle_Roll_7"] = by_equipment.transform(
            lambda s: s.shift(1).rolling(7, min_periods=1).mean()
        )

        chronological = frame.sort_values(["Shift_Date", "Shift"])
        for group, name in (
            ("Equipment_ID", "Equipment_Idle_Expanding"),
            ("Loading_Unit", "Loader_Idle_Expanding"),
            ("Route", "Route_Idle_Expanding"),
        ):
            if group not in frame.columns:
                continue
            frame[name] = (
                chronological.groupby(group, observed=True)[target]
                .transform(lambda s: s.shift(1).expanding().mean())
                .reindex(frame.index)
            )

    frame["Shift_Load_Dumpers"] = frame.groupby(
        ["Shift_Date", "Shift"], observed=True
    )["Equipment_ID"].transform("nunique")
    if "Dumpers_Per_Loader" in frame.columns:
        frame["Loader_Share_Of_Fleet"] = (
            frame["Dumpers_Per_Loader"] / frame["Shift_Load_Dumpers"].replace(0, np.nan)
        )

    if "Shift" in frame.columns:
        frame["Shift_Cat"] = frame["Shift"].astype("object").map(
            lambda s: f"Shift {int(s)}" if pd.notna(s) else "Unknown"
        )

    for col in NUMERIC_FEATURES:
        if col not in frame.columns:
            frame[col] = np.nan
    for col in CATEGORICAL_FEATURES:
        if col not in frame.columns:
            frame[col] = "Unknown"
        frame[col] = frame[col].astype("object").fillna("Unknown").astype(str)

    return frame.reset_index(drop=True)


def feature_matrix(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Return the model input frame plus the numeric and categorical column lists.

    Anything on ``config.LEAKY_COLUMNS`` is refused even if it somehow appears in
    the feature lists, so the model can never re-derive its own target.
    """
    banned = set(config.LEAKY_COLUMNS) - set(HISTORY_FEATURES)
    numeric = [c for c in NUMERIC_FEATURES if c in frame.columns and c not in banned]
    categorical = [c for c in CATEGORICAL_FEATURES if c in frame.columns and c not in banned]
    return frame[numeric + categorical], numeric, categorical


# ==========================================================================  #
# 9. Modelling
# ==========================================================================  #
@dataclass
class ModelBundle:
    """A trained idle-time model plus everything needed to reuse and explain it.

    Two models are trained and shipped together because they answer different
    questions honestly:

    - ``pipeline`` (regression) estimates idle minutes. Its R2 is low by the
      nature of the problem: more than half the variance in idle time comes
      from stochastic mechanical breakdowns that cannot be predicted from
      schedule or workload data alone, only from maintenance history this
      project does not have.
    - ``risk_pipeline`` (classification) predicts whether a shift will land in
      the worst third of the month for idle time. This is a coarser question
      but one the same features answer well (AUC ~0.78), and it is what a
      supervisor actually needs: which shifts deserve attention.
    """

    pipeline: Any
    model_name: str
    target: str
    numeric_features: list[str]
    categorical_features: list[str]
    metrics: dict[str, float]
    leaderboard: pd.DataFrame
    importances: pd.DataFrame
    trained_at: pd.Timestamp
    n_train: int
    n_test: int
    risk_pipeline: Any = None
    risk_model_name: str = ""
    risk_threshold: float = 0.0
    risk_metrics: dict[str, float] | None = None
    risk_leaderboard: pd.DataFrame | None = None
    risk_importances: pd.DataFrame | None = None
    segment_metrics: dict[str, dict[str, float]] | None = None


def _build_pipeline(estimator: Any, numeric: list[str], categorical: list[str]) -> Any:
    """Wrap an estimator in imputation, scaling and one-hot encoding."""
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    numeric_steps = Pipeline(
        [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
    )
    categorical_steps = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("encode", OneHotEncoder(handle_unknown="ignore", min_frequency=5, sparse_output=False)),
        ]
    )
    pre = ColumnTransformer(
        [("num", numeric_steps, numeric), ("cat", categorical_steps, categorical)],
        remainder="drop",
    )
    return Pipeline([("pre", pre), ("model", estimator)])


def _candidate_models() -> dict[str, Any]:
    """The three estimators compared on every retrain."""
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import Ridge

    return {
        "Random Forest": RandomForestRegressor(
            n_estimators=300, min_samples_leaf=2, n_jobs=-1,
            random_state=config.RANDOM_STATE,
        ),
        "Gradient Boosting": GradientBoostingRegressor(random_state=config.RANDOM_STATE),
        "Ridge": Ridge(alpha=1.0, random_state=config.RANDOM_STATE),
    }


def _candidate_classifiers() -> dict[str, Any]:
    """The three classifiers compared for the high-idle-risk flag."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression

    return {
        "Random Forest": RandomForestClassifier(
            n_estimators=400, min_samples_leaf=3, n_jobs=-1,
            random_state=config.RANDOM_STATE, class_weight="balanced",
        ),
        "Logistic Regression": LogisticRegression(
            max_iter=2000, class_weight="balanced", random_state=config.RANDOM_STATE
        ),
    }


def _extract_importances(pipeline: Any, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    """Pull feature importances (or absolute coefficients) out of a fitted pipeline."""
    model = pipeline.named_steps["model"]
    try:
        names = list(pipeline.named_steps["pre"].get_feature_names_out())
    except Exception:  # pragma: no cover - defensive
        names = numeric + categorical

    if hasattr(model, "feature_importances_"):
        values = model.feature_importances_
    elif hasattr(model, "coef_"):
        values = np.abs(np.ravel(model.coef_))
    else:
        return pd.DataFrame(columns=["Feature", "Importance"])

    size = min(len(names), len(values))
    frame = pd.DataFrame({"Feature": names[:size], "Importance": values[:size]})
    frame["Feature"] = (
        frame["Feature"].str.replace(r"^(num|cat)__", "", regex=True).str.replace("_", " ")
    )
    return frame.sort_values("Importance", ascending=False).reset_index(drop=True)


def retrain_model(
    shifts: pd.DataFrame,
    target: str | None = None,
    save_path: Path | None = None,
) -> ModelBundle:
    """Train the idle-minutes regressor and the high-idle-risk classifier.

    Both use the same chronological split: fit on the earlier part of July,
    score on the later part. This is a genuine forward-prediction test, not
    interpolation between neighbouring shifts of the same dumper.

    The regressor's R2 is modest by design. ``Cycles`` and ``Payload_Tonnes``
    were removed from the feature set (see ``config.LEAKY_COLUMNS``) after
    testing showed they are a near-deterministic readout of the target
    (r=0.95 with the delay component) through the fixed 8-hour shift budget,
    not a genuine predictor. What remains is honest but weaker, because most
    of the unexplained variance is stochastic mechanical breakdown, which
    schedule and workload data cannot predict without maintenance history.

    The classifier answers a coarser, more decision-relevant question --
    will this shift land in the worst third of the month for idle time -- and
    reaches a materially better AUC on the same honest feature set, because
    ranking shifts by risk is an easier problem than predicting their exact
    minute count.

    IMPORTANT -- read the pooled metrics with care. The target is bimodal:
    shifts where the truck hauled average ~238 idle minutes, while shifts
    where it was down all eight hours average ~444. A model that merely
    separates those two populations scores a high pooled R2 without being any
    better at the question that matters, which is how much idle time a
    *working* truck will accumulate. ``segment_metrics`` therefore reports the
    two groups separately, and the working-shift figure is the one to quote.

    Args:
        shifts: Output of ``build_shift_master``.
        target: Column to predict. Defaults to ``config.TARGET_SHIFT``.
        save_path: Where to persist the bundle. Defaults to ``config.MODEL_PATH``.

    Raises:
        ValueError: If there is not enough labelled data to train on.
    """
    from sklearn.metrics import (
        accuracy_score, f1_score, mean_absolute_error, r2_score, roc_auc_score,
    )

    target = target or config.TARGET_SHIFT
    if shifts.empty or target not in shifts.columns:
        raise ValueError(f"Cannot train: target '{target}' is missing from the shift master.")

    frame = engineer_features(shifts, target=target)
    frame = frame.dropna(subset=[target])
    frame = frame[np.isfinite(frame[target])]
    if len(frame) < 100:
        raise ValueError(
            f"Only {len(frame)} labelled shift records available; need at least 100 to train."
        )

    frame = frame.sort_values(["Shift_Date", "Shift"]).reset_index(drop=True)
    features, numeric, categorical = feature_matrix(frame)
    labels = frame[target].to_numpy()

    split = int(len(frame) * (1 - config.TEST_SIZE))
    x_train, x_test = features.iloc[:split], features.iloc[split:]
    y_train, y_test = labels[:split], labels[split:]

    # --- Regression: idle minutes ------------------------------------------------
    rows: list[dict[str, Any]] = []
    fitted: dict[str, Any] = {}
    for name, estimator in _candidate_models().items():
        pipeline = _build_pipeline(estimator, numeric, categorical)
        try:
            pipeline.fit(x_train, y_train)
        except Exception as exc:  # pragma: no cover - defensive
            rows.append({"Model": name, "R2": np.nan, "MAE": np.nan, "Error": str(exc)})
            continue
        predicted = pipeline.predict(x_test)
        fitted[name] = pipeline
        rows.append(
            {
                "Model": name,
                "R2": float(r2_score(y_test, predicted)),
                "MAE": float(mean_absolute_error(y_test, predicted)),
                "Error": "",
            }
        )

    leaderboard = pd.DataFrame(rows).sort_values("R2", ascending=False).reset_index(drop=True)
    if leaderboard.empty or leaderboard["R2"].isna().all():
        raise ValueError("Every candidate model failed to fit.")

    best_name = str(leaderboard.iloc[0]["Model"])
    best_pipeline = fitted[best_name]

    # --- Classification: high-idle-risk flag --------------------------------------
    # The classifier is an idle-management tool, not a breakdown predictor.
    # Fully-down shifts (Zero_Cycle_Shift == True) are excluded from both
    # training and evaluation so the classifier learns to flag shifts where a
    # *working* truck accumulated excessive idle, not shifts where it never
    # moved at all.
    risk_mask = np.ones(len(frame), dtype=bool)
    if "Zero_Cycle_Shift" in frame.columns:
        risk_mask = ~frame["Zero_Cycle_Shift"].to_numpy(dtype=bool)
    risk_frame = frame[risk_mask].reset_index(drop=True)
    risk_features, risk_numeric, risk_categorical = feature_matrix(risk_frame)
    risk_labels = risk_frame[target].to_numpy()
    risk_split = int(len(risk_frame) * (1 - config.TEST_SIZE))
    risk_x_train = risk_features.iloc[:risk_split]
    risk_x_test = risk_features.iloc[risk_split:]
    risk_y_train = risk_labels[:risk_split]
    risk_y_test = risk_labels[risk_split:]

    threshold = float(np.quantile(risk_y_train, config.HIGH_IDLE_PERCENTILE))
    y_train_risk = (risk_y_train > threshold).astype(int)
    y_test_risk = (risk_y_test > threshold).astype(int)

    risk_rows: list[dict[str, Any]] = []
    risk_fitted: dict[str, Any] = {}
    if 0 < y_train_risk.sum() < len(y_train_risk):
        for name, estimator in _candidate_classifiers().items():
            pipeline = _build_pipeline(estimator, risk_numeric, risk_categorical)
            try:
                pipeline.fit(risk_x_train, y_train_risk)
            except Exception as exc:  # pragma: no cover - defensive
                risk_rows.append({"Model": name, "AUC": np.nan, "Accuracy": np.nan,
                                   "F1": np.nan, "Error": str(exc)})
                continue
            proba = pipeline.predict_proba(risk_x_test)[:, 1]
            predicted = pipeline.predict(risk_x_test)
            risk_fitted[name] = pipeline
            risk_rows.append(
                {
                    "Model": name,
                    "AUC": float(roc_auc_score(y_test_risk, proba)),
                    "Accuracy": float(accuracy_score(y_test_risk, predicted)),
                    "F1": float(f1_score(y_test_risk, predicted)),
                    "Error": "",
                }
            )

    risk_leaderboard = (
        pd.DataFrame(risk_rows).sort_values("AUC", ascending=False).reset_index(drop=True)
        if risk_rows else pd.DataFrame()
    )
    # Segment the held-out scores. Knowing a truck is in the workshop needs no
    # model, so pooled accuracy that leans on those shifts overstates the tool.
    segment_metrics: dict[str, dict[str, float]] = {}
    test_frame = frame.iloc[split:]
    if "Zero_Cycle_Shift" in test_frame.columns:
        best_predictions = best_pipeline.predict(x_test)
        down = test_frame["Zero_Cycle_Shift"].to_numpy(dtype=bool)
        for label, mask in (("working_shifts", ~down), ("zero_cycle_shifts", down)):
            if mask.sum() < 20:
                continue
            segment_metrics[label] = {
                "r2": float(r2_score(y_test[mask], best_predictions[mask])),
                "mae": float(mean_absolute_error(y_test[mask], best_predictions[mask])),
                "n": int(mask.sum()),
                "mean_actual": float(y_test[mask].mean()),
            }

    risk_pipeline = risk_model_name = risk_metrics = risk_importances = None
    if not risk_leaderboard.empty and not risk_leaderboard["AUC"].isna().all():
        risk_model_name = str(risk_leaderboard.iloc[0]["Model"])
        risk_pipeline = risk_fitted[risk_model_name]
        risk_metrics = {
            "auc": float(risk_leaderboard.iloc[0]["AUC"]),
            "accuracy": float(risk_leaderboard.iloc[0]["Accuracy"]),
            "f1": float(risk_leaderboard.iloc[0]["F1"]),
        }
        risk_importances = _extract_importances(risk_pipeline, risk_numeric, risk_categorical)

    bundle = ModelBundle(
        pipeline=best_pipeline,
        model_name=best_name,
        target=target,
        numeric_features=numeric,
        categorical_features=categorical,
        metrics={
            "r2": float(leaderboard.iloc[0]["R2"]),
            "mae": float(leaderboard.iloc[0]["MAE"]),
        },
        leaderboard=leaderboard,
        importances=_extract_importances(best_pipeline, numeric, categorical),
        trained_at=pd.Timestamp.now(),
        n_train=int(len(x_train)),
        n_test=int(len(x_test)),
        risk_pipeline=risk_pipeline,
        risk_model_name=risk_model_name or "",
        risk_threshold=threshold,
        risk_metrics=risk_metrics,
        risk_leaderboard=risk_leaderboard,
        risk_importances=risk_importances,
        segment_metrics=segment_metrics or None,
    )

    if save_path is None:
        writable_model = config.ROOT / "models" / "base_model.pkl"
        writable_model.parent.mkdir(parents=True, exist_ok=True)
        save_path = writable_model
    save_model(bundle, save_path)
    return bundle


def save_model(bundle: ModelBundle, path: Path | None = None) -> Path:
    """Persist a trained bundle with compression so it stays well under 100 MB."""
    import joblib

    path = Path(path or config.MODEL_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path, compress=1)
    return path


def load_model(path: Path | None = None) -> ModelBundle | None:
    """Load a saved bundle, returning ``None`` if it is missing or unreadable.

    Checks the writable model directory first (user retrained models), then
    falls back to the bundled read-only model.
    """
    import joblib

    if path is None:
        writable = config.ROOT / "models" / "base_model.pkl"
        if writable.exists():
            path = writable
        else:
            path = config.MODEL_PATH
    path = Path(path)
    if not path.exists():
        return None
    try:
        return joblib.load(path)
    except Exception:
        return None


def predict_idle_time(bundle: ModelBundle, shifts: pd.DataFrame) -> pd.DataFrame:
    """Score shifts with both halves of the bundle: minutes and risk tier.

    ``Excess_Idle_Min`` is the part of the loss the point-estimate regressor
    cannot explain from haul geometry, congestion and history. Given the
    regressor's modest R2, treat this as a rough flag, not a precise number.

    ``High_Idle_Risk_Proba`` is the classifier's probability that the shift
    lands in the worst third of the month for idle time. This is the more
    reliable of the two signals (AUC ~0.78 on held-out data) and is the one
    the dashboard leads with.
    """
    if shifts.empty:
        return shifts

    frame = engineer_features(shifts, target=bundle.target)
    features, _numeric, _categorical = feature_matrix(frame)
    frame["Predicted_Idle_Min"] = bundle.pipeline.predict(features).clip(min=0)

    if bundle.target in frame.columns:
        frame["Idle_Residual"] = frame[bundle.target] - frame["Predicted_Idle_Min"]
        frame["Excess_Idle_Min"] = frame["Idle_Residual"].clip(lower=0)
        # Flag the tail: shifts more than one standard deviation worse than the
        # model expects are the ones worth a conversation.
        threshold = frame["Idle_Residual"].std(ddof=0)
        frame["Idle_Flag"] = frame["Idle_Residual"] > threshold

    if bundle.risk_pipeline is not None:
        frame["High_Idle_Risk_Proba"] = bundle.risk_pipeline.predict_proba(features)[:, 1]
        frame["High_Idle_Risk"] = frame["High_Idle_Risk_Proba"] >= 0.5
        if bundle.target in frame.columns:
            frame["Actually_High_Idle"] = frame[bundle.target] > bundle.risk_threshold
        # The classifier was trained only on working shifts. Fully-down shifts
        # are not "high idle risk" in the management sense — they are breakdowns.
        if "Zero_Cycle_Shift" in frame.columns:
            down = frame["Zero_Cycle_Shift"].to_numpy(dtype=bool)
            frame.loc[down, "High_Idle_Risk_Proba"] = 0.0
            frame.loc[down, "High_Idle_Risk"] = False
    return frame
