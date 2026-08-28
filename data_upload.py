"""Upload, validation, append and retrain workflow for new FMS reports.

This module turns the Streamlit sidebar into a self-service data pipeline:
users upload one or more FMS CSV exports, the app validates them against the
report catalogue, appends them to the existing processed data, rebuilds the
master tables and retrains the model -- all without touching a terminal.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import config
import data_utils as du


# Required output columns for each report the app can consume.
# These are the columns present AFTER the data_utils loader has run.
# Relationship checks (e.g. cycle time ~ sum of buckets) are handled separately.
REQUIRED_COLUMNS: dict[str, dict[str, list[str]]] = {
    "cycles": {
        "Identity / shift": ["Equipment_ID", "Shift_Date", "Shift"],
        "Cycle time": ["Cycle_Time"],
    },
    "delay_events": {
        "Identity / shift": ["Equipment_ID", "Shift_Date"],
        "Reason / duration": ["Reason", "Delay_Min"],
    },
    "idle_events": {
        "Identity / time": ["Equipment_ID"],
        "Duration": ["Idle_Min"],
    },
    "tkph": {
        "Identity": ["Equipment_ID"],
        "Distance / load": ["Haul_Km", "Empty_Km", "Loads"],
    },
    "dumper_shift": {
        "Identity / shift": ["Equipment_ID", "Shift_Date"],
        "Context": ["Run_Hours", "Breakdown_Hours", "Available_Hours"],
    },
    "fuel": {
        "Identity": ["Equipment_ID"],
        "Fuel": ["Fuel_Litres"],
    },
    "status_summary": {
        "Identity / reason": ["Equipment_ID", "Reason"],
        "Duration": ["Hours"],
    },
    "shovel_shift": {
        "Identity / shift": ["Equipment_ID", "Shift_Date"],
        "Context": ["Available_Hours", "Run_Hours", "Breakdown_Hours"],
    },
    "hauling_summary": {
        "Identity / shift": ["Equipment_ID", "Shift_Date"],
        "Trips / tonnes": ["Total_Trips"],
    },
    "loading_unit_summary": {
        "Identity / shift": ["Equipment_ID", "Shift_Date"],
        "Trips / tonnes": ["Total_Trips"],
    },
    "daily_production": {
        "Identity": ["Equipment_ID"],
        "Totals": ["Shift"],
    },
    "loader_profile": {
        "Identity / date": ["Loading_Unit", "Shift_Date"],
        "Event": ["Status", "Duration_Min"],
    },
    "operator": {
        "Identity": ["Operator_PNo", "Shift"],
        "Output": ["Trips", "Tonnes"],
    },
    "payload_cycles": {
        "Identity / shift": ["Equipment_ID", "Shift_Date"],
        "Cycle": ["Cycle_Min", "Payload"],
    },
    "loading_unit_time": {
        "Identity": ["Equipment_ID", "Shift"],
        "Durations": ["Loading_Min", "Waiting_Min"],
    },
    "loading_routes": {
        "Identity / shift": ["Equipment_ID", "Shift_Date"],
        "Route": ["Load_Location", "Dump_Location", "Lead_Km"],
    },
    "status_category": {
        "Identity": ["Equipment_ID"],
        "Durations": ["Delay_Hours", "Down_Hours", "Standby_Hours", "GOH_Hours", "NOH_Hours"],
    },
    "engine_hours": {
        "Identity": ["Equipment_ID"],
        "Interval": ["First_Entry", "Last_Entry", "Net_Hours"],
    },
}

# Optional but expected columns; missing them does not fail the upload, but the
# user is warned because the dashboard will be less complete.
EXPECTED_COLUMNS: dict[str, dict[str, list[str]]] = {
    "cycles": {
        "Idle buckets": list(config.IDLE_BUCKETS),
        "Productive buckets": list(config.PRODUCTIVE_BUCKETS),
        "Spotting": list(config.SEMI_PRODUCTIVE_BUCKETS),
        "Payload": ["Payload"],
        "Route / material": ["Load_Location", "Dump_Location"],
    },
    "delay_events": {
        "Extra reason": ["Sub_Reason", "Status_Comment"],
        "Clock time": ["Start_Timestamp"],
    },
}


# Description of the cross-column relationships the validator checks.
RELATIONSHIP_DESCRIPTIONS: dict[str, str] = {
    "cycles": "Cycle_Time should equal the sum of the ten bucket columns (idle + productive + spotting).",
    "delay_events": "Reason and Delay_Min must be present and Delay_Min must be positive for most rows.",
    "idle_events": "Idle_Min must be positive for most rows.",
}

# Reports that must be present before the user is allowed to append + retrain.
HARD_REQUIREMENTS = {"cycles", "delay_events"}

# Master grain used for deduplication of each raw table.
DEDUP_KEYS: dict[str, list[str] | None] = {
    "cycles": None,  # drop duplicates on all payload columns
    "idle_events": None,
    "delay_events": None,
    "status_summary": ["Equipment_ID", "Reason", "Status_Category"],
    "fuel": ["Equipment_ID"],
    "dumper_shift": ["Equipment_ID", "Shift_Date", "Shift"],
    "tkph": ["Equipment_ID"],
    "shovel_shift": ["Equipment_ID", "Shift_Date", "Shift"],
    "hauling_summary": ["Equipment_ID", "Shift_Date", "Shift"],
    "loading_unit_summary": ["Equipment_ID", "Shift_Date", "Shift"],
    "daily_production": ["Equipment_ID", "Shift"],
    "loader_profile": None,
    "operator": ["Operator_PNo", "Shift"],
    "payload_cycles": None,
    "loading_unit_time": ["Equipment_ID", "Shift"],
    "loading_routes": ["Equipment_ID", "Shift_Date", "Shift",
                        "Load_Location", "Dump_Location"],
    "status_category": ["Equipment_ID"],
    "engine_hours": ["Equipment_ID", "First_Entry"],
}


@dataclass
class FileValidation:
    """Result of checking a single uploaded CSV."""

    name: str
    report_key: str = "unknown"
    ok: bool = False
    missing_categories: dict[str, list[str]] = field(default_factory=dict)
    missing_expected: dict[str, list[str]] = field(default_factory=dict)
    relationship_failures: list[str] = field(default_factory=list)
    rows: int = 0
    message: str = ""


@dataclass
class UploadValidation:
    """Result of checking the whole batch."""

    files: list[FileValidation] = field(default_factory=list)
    has_required_reports: bool = False
    missing_hard: set[str] = field(default_factory=set)
    ok: bool = False
    raw_tables: dict[str, pd.DataFrame] = field(default_factory=dict)


def _write_uploaded_file(uploaded_file: Any, temp_dir: Path) -> Path:
    """Persist an ``UploadedFile`` to a temporary path the loaders can read."""
    path = temp_dir / uploaded_file.name
    path.write_bytes(uploaded_file.getvalue())
    return path


def _check_required_columns(report_key: str, frame: pd.DataFrame) -> dict[str, list[str]]:
    """Return {category: [missing canonical columns]}."""
    missing: dict[str, list[str]] = {}
    categories = REQUIRED_COLUMNS.get(report_key, {})
    for category, cols in categories.items():
        absent = [c for c in cols if c not in frame.columns]
        if absent:
            missing[category] = absent
    return missing


def _check_expected_columns(report_key: str, frame: pd.DataFrame) -> dict[str, list[str]]:
    """Return optional columns that are missing, grouped by category."""
    missing: dict[str, list[str]] = {}
    categories = EXPECTED_COLUMNS.get(report_key, {})
    for category, cols in categories.items():
        absent = [c for c in cols if c not in frame.columns]
        if absent:
            missing[category] = absent
    return missing


def _check_relationships(report_key: str, frame: pd.DataFrame) -> list[str]:
    """Return human-readable relationship failures for a loaded report."""
    failures: list[str] = []
    if report_key == "cycles":
        bucket_cols = [c for c in config.ALL_CYCLE_BUCKETS if c in frame.columns]
        if not bucket_cols:
            failures.append(
                "No cycle-time bucket columns found; Cycle_Time cannot be decomposed into idle vs productive."
            )
        elif "Cycle_Time" in frame.columns:
            sample = frame.dropna(subset=["Cycle_Time"]).head(200).copy()
            if not sample.empty:
                bucket_sum = sample[bucket_cols].fillna(0).sum(axis=1)
                # Allow a small tolerance for rounding.
                ok = (sample["Cycle_Time"] - bucket_sum).abs().le(5.0).mean() > 0.95
                if not ok:
                    failures.append(
                        "Cycle_Time does not consistently equal the sum of the bucket columns; "
                        "this may not be a Dumper Cycle Time report."
                    )
    elif report_key == "delay_events":
        if "Reason" not in frame.columns:
            failures.append("No reason text could be extracted from Status_Desc.")
        if "Delay_Min" not in frame.columns:
            failures.append("No delay duration could be computed.")
        elif frame["Delay_Min"].dropna().le(0).mean() > 0.5:
            failures.append("More than half of the delay rows have zero or missing duration.")
    elif report_key == "idle_events":
        if "Idle_Min" not in frame.columns:
            failures.append("No idle duration could be computed.")
        elif frame["Idle_Min"].dropna().le(0).mean() > 0.5:
            failures.append("More than half of the idle rows have zero or missing duration.")
    return failures


def validate_uploaded_files(uploaded_files: list[Any]) -> UploadValidation:
    """Classify, validate and load a list of Streamlit UploadedFile objects.

    Returns a dataclass with per-file results, overall batch validity and the
    loaded raw tables grouped by report key.
    """
    result = UploadValidation()
    if not uploaded_files:
        return result

    found_keys: set[str] = set()
    raw_tables: dict[str, list[pd.DataFrame]] = {}

    with tempfile.TemporaryDirectory() as tmp:
        temp_dir = Path(tmp)
        for uploaded_file in uploaded_files:
            path = _write_uploaded_file(uploaded_file, temp_dir)
            report_key = du.classify_file(path)
            file_result = FileValidation(name=uploaded_file.name, report_key=report_key)

            if report_key == "unknown":
                file_result.message = (
                    "Could not identify this as a known FMS report. "
                    "Filename and headers did not match any report in the catalogue."
                )
                result.files.append(file_result)
                continue

            if report_key not in du.LOADERS:
                file_result.message = (
                    f"Identified as `{report_key}`, but that report is not used by "
                    "the dashboard. See `docs/report_catalogue.md` for the accepted list."
                )
                result.files.append(file_result)
                continue

            try:
                loader = du.LOADERS[report_key]
                frame = loader([path])
                file_result.rows = len(frame)
            except Exception as exc:
                file_result.message = f"Failed to parse: {exc}"
                result.files.append(file_result)
                continue

            file_result.missing_categories = _check_required_columns(report_key, frame)
            file_result.missing_expected = _check_expected_columns(report_key, frame)
            file_result.relationship_failures = _check_relationships(report_key, frame)

            has_hard_missing = bool(file_result.missing_categories)
            has_rel_failures = bool(file_result.relationship_failures)
            file_result.ok = not has_hard_missing and not has_rel_failures

            if not file_result.ok:
                parts = []
                if has_hard_missing:
                    parts.append("missing required column groups")
                if has_rel_failures:
                    parts.append("cross-column relationship check failed")
                file_result.message = (
                    "Recognised but " + " and ".join(parts) + ". See details below."
                )
            else:
                file_result.message = (
                    f"Recognised as {report_key} and parsed {file_result.rows:,} rows."
                )
                found_keys.add(report_key)
                raw_tables.setdefault(report_key, []).append(frame)

            result.files.append(file_result)

    # Combine files of the same report type (same logic as _read_many).
    for key, frames in raw_tables.items():
        if not frames:
            continue
        combined = pd.concat(frames, ignore_index=True, sort=False)
        payload_cols = [c for c in combined.columns if c != "Source_File"]
        if key in ("cycles", "idle_events", "delay_events"):
            combined = combined.drop_duplicates(subset=payload_cols).reset_index(drop=True)
        elif DEDUP_KEYS.get(key):
            combined = combined.drop_duplicates(subset=DEDUP_KEYS[key]).reset_index(drop=True)
        result.raw_tables[key] = combined

    result.missing_hard = HARD_REQUIREMENTS - found_keys
    result.has_required_reports = not result.missing_hard
    result.ok = result.has_required_reports and all(f.ok for f in result.files)
    return result


def _load_existing_raw_table(name: str) -> pd.DataFrame:
    """Load an existing processed table if it exists, else an empty frame.

    The processed parquets are the output of the loaders (already cleaned and
    standardised), not raw CSVs.  This is safe because all cleaning functions
    are idempotent — re-applying them to already-clean data is a no-op.
    """
    path = config.WRITABLE_PROCESSED_DIR / f"{name}.parquet"
    if path.exists():
        try:
            return pd.read_parquet(path)
        except Exception:
            pass
    path = config.PROCESSED_DIR / f"{name}.parquet"
    if path.exists():
        try:
            return pd.read_parquet(path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def _combine_with_existing(new: pd.DataFrame, existing: pd.DataFrame, key: str) -> pd.DataFrame:
    """Append new data to existing and deduplicate, keeping the newest upload."""
    if existing.empty:
        return new.copy()
    if new.empty:
        return existing.copy()

    combined = pd.concat([existing, new], ignore_index=True, sort=False)

    if key == "fuel" and "Fuel_Litres" in combined.columns:
        return (
            combined.groupby("Equipment_ID", as_index=False)
            .agg(Fuel_Litres=("Fuel_Litres", "sum"), Fleet=("Fleet", "first"))
        )

    if key == "tkph" and "Loads" in combined.columns:
        grouped = combined.groupby("Equipment_ID", as_index=False).agg(
            Loads=("Loads", "sum"),
            Tonnes=("Tonnes", "sum"),
            Haul_Km=("Haul_Km", "sum"),
            Empty_Km=("Empty_Km", "sum"),
            Travel_Hours=("Travel_Hours", "sum"),
            Fleet=("Fleet", "first"),
        )
        grouped["Total_Km"] = grouped["Haul_Km"] + grouped["Empty_Km"]
        grouped["Km_Per_Cycle"] = grouped["Total_Km"] / grouped["Loads"].replace(0, np.nan)
        grouped["Avg_Speed_Kmph"] = grouped["Total_Km"] / grouped["Travel_Hours"].replace(0, np.nan)
        return grouped

    if key == "status_summary" and "Hours" in combined.columns:
        return (
            combined.groupby(["Equipment_ID", "Reason", "Status_Category"], as_index=False)
            .agg(
                Hours=("Hours", "sum"),
                Reason_Class=("Reason_Class", "first"),
                Addressable=("Addressable", "first"),
            )
        )

    payload_cols = [c for c in combined.columns if c != "Source_File"]
    dedup_cols = DEDUP_KEYS.get(key, payload_cols)
    if dedup_cols is None:
        dedup_cols = payload_cols
    return combined.drop_duplicates(subset=dedup_cols, keep="last").reset_index(drop=True)


def append_and_rebuild(raw_tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Merge uploaded raw tables with existing processed data and rebuild masters.

    Returns the rebuilt master table dict (cycles, shifts, hourly, reasons,
    equipment, plus the raw reports). Persists everything to `data/processed`.
    """
    config.ensure_dirs()

    # Combine each raw report with whatever already exists.
    combined_raw: dict[str, pd.DataFrame] = {}
    for key in du.CORE_REPORTS:
        existing = _load_existing_raw_table(key)
        new = raw_tables.get(key, pd.DataFrame())
        combined = _combine_with_existing(new, existing, key)
        combined_raw[key] = combined

    # Rebuild master tables from the combined raw data.
    cycles = combined_raw["cycles"]
    shifts = du.build_shift_master(
        cycles,
        delay_events=combined_raw.get("delay_events"),
        idle_events=combined_raw.get("idle_events"),
        dumper_shift=combined_raw.get("dumper_shift"),
        tkph=combined_raw.get("tkph"),
    )
    hourly = du.build_hourly_master(
        combined_raw.get("idle_events"), combined_raw.get("delay_events")
    )
    reasons = du.build_reason_master(
        combined_raw.get("delay_events"), combined_raw.get("status_summary")
    )
    equipment = du.build_equipment_master(
        shifts, combined_raw.get("fuel"), combined_raw.get("tkph")
    )

    outputs = {
        "cycles": cycles,
        "shifts": shifts,
        "hourly": hourly,
        "reasons": reasons,
        "equipment": equipment,
        "idle_events": combined_raw.get("idle_events", pd.DataFrame()),
        "delay_events": combined_raw.get("delay_events", pd.DataFrame()),
        "fuel": combined_raw.get("fuel", pd.DataFrame()),
        "tkph": combined_raw.get("tkph", pd.DataFrame()),
        "dumper_shift": combined_raw.get("dumper_shift", pd.DataFrame()),
        "status_summary": combined_raw.get("status_summary", pd.DataFrame()),
        "shovel_shift": combined_raw.get("shovel_shift", pd.DataFrame()),
        "hauling_summary": combined_raw.get("hauling_summary", pd.DataFrame()),
        "loading_unit_summary": combined_raw.get("loading_unit_summary", pd.DataFrame()),
        "daily_production": combined_raw.get("daily_production", pd.DataFrame()),
        "loader_profile": combined_raw.get("loader_profile", pd.DataFrame()),
        "operator": combined_raw.get("operator", pd.DataFrame()),
        "payload_cycles": combined_raw.get("payload_cycles", pd.DataFrame()),
        "loading_unit_time": combined_raw.get("loading_unit_time", pd.DataFrame()),
        "loading_routes": combined_raw.get("loading_routes", pd.DataFrame()),
        "status_category": combined_raw.get("status_category", pd.DataFrame()),
        "engine_hours": combined_raw.get("engine_hours", pd.DataFrame()),
    }

    for name, frame in outputs.items():
        if frame is None or frame.empty:
            continue
        path = config.WRITABLE_PROCESSED_DIR / f"{name}.parquet"
        frame.to_parquet(path, index=False)

    # Update provenance with the upload event.
    provenance_path = config.WRITABLE_PROCESSED_DIR / "provenance.json"
    provenance: dict[str, Any] = {}
    if provenance_path.exists():
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except Exception:
            provenance = {}

    provenance.setdefault("uploads", []).append(
        {
            "at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "new_rows": {k: int(len(v)) for k, v in raw_tables.items()},
            "combined_rows": {k: int(len(v)) for k, v in outputs.items()},
            "coverage": {
                "first_shift_date": str(cycles["Shift_Date"].min().date()) if not cycles.empty else None,
                "last_shift_date": str(cycles["Shift_Date"].max().date()) if not cycles.empty else None,
                "dumpers": int(cycles["Equipment_ID"].nunique()) if not cycles.empty else 0,
            },
        }
    )
    provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")

    return outputs


def retrain_and_save(tables: dict[str, pd.DataFrame]) -> du.ModelBundle:
    """Retrain the model on the rebuilt shift master and persist the bundle."""
    shifts = tables["shifts"]
    if shifts.empty:
        raise ValueError("No shift data available to train on.")
    bundle = du.retrain_model(shifts)
    return bundle
