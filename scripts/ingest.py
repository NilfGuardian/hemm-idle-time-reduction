"""Build the processed idle-time tables and train the base model.

Run once after dropping new FMS exports into the source folders:

    python scripts/ingest.py

Everything is written to ``data/processed`` as parquet plus a provenance file so
the dashboard starts instantly and every number is traceable to a source CSV.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import data_utils as du  # noqa: E402
from data_upload import DEDUP_KEYS  # noqa: E402

TABLES = ("cycles", "shifts", "hourly", "reasons", "equipment",
          "idle_events", "delay_events", "fuel", "tkph")


def _write(frame: pd.DataFrame, name: str) -> tuple[str, int]:
    """Write a table to parquet and return its name and row count."""
    path = config.PROCESSED_DIR / f"{name}.parquet"
    frame.to_parquet(path, index=False)
    return name, len(frame)


def run(source_dirs: list[Path] | None = None, train: bool = True) -> dict[str, object]:
    """Parse every source report, build the master tables and train the model."""
    config.ensure_dirs()
    started = time.time()

    print("Scanning source folders...")
    discovered = du.discover_files(source_dirs)
    for key in sorted(discovered):
        print(f"  {key:22} {len(discovered[key])} file(s)")

    missing = [key for key in du.CORE_REPORTS if not discovered.get(du.source_key(key))]
    if "cycles" in missing:
        raise SystemExit(
            "No 'Dumper Cycle Time' export found. That report is the base table "
            "and the pipeline cannot run without it."
        )
    if missing:
        print(f"\n! Optional reports not found, continuing without them: {missing}")

    print("\nParsing reports...")
    raw: dict[str, pd.DataFrame] = {}
    for key in du.CORE_REPORTS:
        paths = discovered.get(du.source_key(key), [])
        if not paths:
            raw[key] = pd.DataFrame()
            continue
        try:
            frame = du.LOADERS[key](paths)
        except Exception as exc:
            print(f"  {key:22} FAILED: {exc}")
            raw[key] = pd.DataFrame()
            continue
        dedup_cols = DEDUP_KEYS.get(key)
        if dedup_cols:
            frame = frame.drop_duplicates(subset=dedup_cols).reset_index(drop=True)
        elif key in ("cycles", "idle_events", "delay_events"):
            payload_cols = [c for c in frame.columns if c != "Source_File"]
            frame = frame.drop_duplicates(subset=payload_cols).reset_index(drop=True)
        raw[key] = frame
        print(f"  {key:22} {len(frame):>7,} rows")

    print("\nBuilding master tables...")
    cycles = raw["cycles"]
    shifts = du.build_shift_master(
        cycles,
        delay_events=raw.get("delay_events"),
        idle_events=raw.get("idle_events"),
        dumper_shift=raw.get("dumper_shift"),
        tkph=raw.get("tkph"),
    )
    hourly = du.build_hourly_master(raw.get("idle_events"), raw.get("delay_events"))
    reasons = du.build_reason_master(raw.get("delay_events"), raw.get("status_summary"))
    equipment = du.build_equipment_master(shifts, raw.get("fuel"), raw.get("tkph"))

    outputs = {
        "cycles": cycles,
        "shifts": shifts,
        "hourly": hourly,
        "reasons": reasons,
        "equipment": equipment,
        "idle_events": raw.get("idle_events", pd.DataFrame()),
        "delay_events": raw.get("delay_events", pd.DataFrame()),
        "fuel": raw.get("fuel", pd.DataFrame()),
        "tkph": raw.get("tkph", pd.DataFrame()),
        "dumper_shift": raw.get("dumper_shift", pd.DataFrame()),
        "status_summary": raw.get("status_summary", pd.DataFrame()),
        "shovel_shift": raw.get("shovel_shift", pd.DataFrame()),
        "hauling_summary": raw.get("hauling_summary", pd.DataFrame()),
        "loading_unit_summary": raw.get("loading_unit_summary", pd.DataFrame()),
        "daily_production": raw.get("daily_production", pd.DataFrame()),
        "loader_profile": raw.get("loader_profile", pd.DataFrame()),
        "operator": raw.get("operator", pd.DataFrame()),
        "payload_cycles": raw.get("payload_cycles", pd.DataFrame()),
        "loading_unit_time": raw.get("loading_unit_time", pd.DataFrame()),
        "loading_routes": raw.get("loading_routes", pd.DataFrame()),
        "status_category": raw.get("status_category", pd.DataFrame()),
        "engine_hours": raw.get("engine_hours", pd.DataFrame()),
    }
    for name, frame in outputs.items():
        if frame is None or frame.empty:
            print(f"  {name:22} skipped (empty)")
            continue
        _, rows = _write(frame, name)
        print(f"  {name:22} {rows:>7,} rows -> data/processed/{name}.parquet")

    metrics: dict[str, object] = {}
    if train and not shifts.empty:
        print("\nTraining idle-time regressor (idle minutes)...")
        try:
            bundle = du.retrain_model(shifts)
            metrics = {
                "model": bundle.model_name,
                "target": bundle.target,
                "r2": round(bundle.metrics["r2"], 4),
                "mae": round(bundle.metrics["mae"], 3),
                "n_train": bundle.n_train,
                "n_test": bundle.n_test,
            }
            print(f"  best: {bundle.model_name}  R2={metrics['r2']}  MAE={metrics['mae']}")
            print(bundle.leaderboard[["Model", "R2", "MAE"]].to_string(index=False))

            # The pooled R2 is flattered by the gap between working and
            # fully-down shifts, so always print the split alongside it.
            if bundle.segment_metrics:
                metrics["segments"] = {
                    name: {k: round(v, 4) if isinstance(v, float) else v
                           for k, v in values.items()}
                    for name, values in bundle.segment_metrics.items()
                }
                print("\n  held-out performance by shift type:")
                for name, values in bundle.segment_metrics.items():
                    print(
                        f"    {name:20s} n={values['n']:5d}  R2={values['r2']:6.3f}  "
                        f"MAE={values['mae']:6.1f}  mean actual={values['mean_actual']:6.1f} min"
                    )
                working = bundle.segment_metrics.get("working_shifts")
                if working:
                    print(
                        f"    -> quote R2={working['r2']:.3f} for working shifts, "
                        "not the pooled figure."
                    )

            if bundle.risk_metrics:
                print("\nTraining high-idle-risk classifier (worst third of shifts)...")
                metrics["risk_model"] = bundle.risk_model_name
                metrics["risk_auc"] = round(bundle.risk_metrics["auc"], 4)
                metrics["risk_accuracy"] = round(bundle.risk_metrics["accuracy"], 4)
                metrics["risk_threshold_min"] = round(bundle.risk_threshold, 1)
                print(
                    f"  best: {bundle.risk_model_name}  "
                    f"AUC={metrics['risk_auc']}  Accuracy={metrics['risk_accuracy']}"
                )
                print(bundle.risk_leaderboard[["Model", "AUC", "Accuracy", "F1"]].to_string(index=False))
            else:
                print("  risk classifier skipped: not enough class balance to train.")
        except Exception as exc:
            print(f"  training skipped: {exc}")

    provenance = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.time() - started, 1),
        "source_dirs": [str(p) for p in (source_dirs or config.DEFAULT_SOURCE_DIRS)],
        "files_used": {
            key: [p.name for p in paths]
            for key, paths in discovered.items()
            if key in du.CORE_REPORTS
        },
        "row_counts": {
            name: int(len(frame))
            for name, frame in outputs.items()
            if frame is not None and not frame.empty
        },
        "coverage": {
            "first_shift_date": str(cycles["Shift_Date"].min().date()) if not cycles.empty else None,
            "last_shift_date": str(cycles["Shift_Date"].max().date()) if not cycles.empty else None,
            # Counted from the shift master, not the cycle report, so dumpers
            # that were down all month are included.
            "dumpers": int(shifts["Equipment_ID"].nunique()) if not shifts.empty else 0,
            "dumper_shifts": int(len(shifts)),
            "zero_cycle_shifts": (
                int(shifts["Zero_Cycle_Shift"].sum())
                if "Zero_Cycle_Shift" in shifts.columns else 0
            ),
        },
        "model": metrics,
    }
    provenance_path = config.PROCESSED_DIR / "provenance.json"
    provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")

    print(f"\nDone in {provenance['elapsed_seconds']}s. Provenance -> {provenance_path}")
    return provenance


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sources", nargs="*", type=Path,
        help="Folders containing FMS CSV exports. Defaults to config.DEFAULT_SOURCE_DIRS.",
    )
    parser.add_argument("--no-train", action="store_true", help="Skip model training.")
    args = parser.parse_args()
    run(args.sources or None, train=not args.no_train)


if __name__ == "__main__":
    main()
