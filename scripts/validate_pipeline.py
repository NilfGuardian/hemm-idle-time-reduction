"""Data and model invariants for the HEMM idle-time pipeline.

These are the assertions that would have caught the survivorship bias in the
shift spine, so they run over the generated parquet tables rather than over
mocked data. Run after any change to ``data_utils`` or after ``ingest.py``:

    python scripts/validate_pipeline.py

Exits non-zero if any invariant fails, so it can gate a commit.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

import config
import data_utils as du

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, passed: bool, detail: str = "") -> None:
    """Record the outcome of a single invariant."""
    global CHECKS
    CHECKS += 1
    if passed:
        print(f"  ok    {name}" + (f"  ({detail})" if detail else ""))
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def load(name: str) -> pd.DataFrame:
    """Read a processed table."""
    return pd.read_parquet(config.PROCESSED_DIR / f"{name}.parquet")


def main() -> int:
    """Run every invariant and report."""
    cycles = load("cycles")
    shifts = load("shifts")
    delays = load("delay_events")

    print("Cycle decomposition...")
    buckets = [b for b in config.ALL_CYCLE_BUCKETS if b in cycles.columns]
    residual = (cycles["Cycle_Time"] - cycles[buckets].sum(axis=1)).abs()
    check(
        "Cycle_Time equals the sum of its buckets",
        bool((residual < 0.01).all()),
        f"max residual {residual.max():.2e}",
    )

    print("\nShift spine completeness...")
    dumper_delays = delays[delays["Equipment_Class"] == "Dumper"]
    keys = ["Equipment_ID", "Shift_Date", "Shift"]

    # The bug this suite exists for: every dumper-shift that logged delay must
    # have a row in the shift master, whether or not it completed a cycle.
    orphans = dumper_delays[keys].drop_duplicates().merge(
        shifts[keys], on=keys, how="left", indicator=True
    )
    orphan_count = int((orphans["_merge"] == "left_only").sum())
    check(
        "every dumper-shift with delay has a shift row",
        orphan_count == 0,
        f"{orphan_count} orphaned",
    )

    missing_equipment = set(dumper_delays["Equipment_ID"]) - set(shifts["Equipment_ID"])
    check(
        "every dumper in delay_events appears in shifts",
        not missing_equipment,
        f"missing {sorted(missing_equipment)}",
    )

    print("\nDelay conservation...")
    # Total reason-coded dumper delay must be fully accounted for in the master.
    delay_hours = dumper_delays["Delay_Min"].sum() / 60
    shift_hours = shifts["Delay_Min"].sum() / 60
    check(
        "dumper delay hours are conserved into shifts",
        abs(delay_hours - shift_hours) < 1.0,
        f"delay_events {delay_hours:,.1f} h vs shifts {shift_hours:,.1f} h",
    )

    print("\nNumeric hygiene...")
    numeric = shifts.select_dtypes(include=[np.number])
    inf_count = int(np.isinf(numeric).sum().sum())
    check("no infinite values in any numeric column", inf_count == 0, f"{inf_count} found")

    zero_cycle = shifts["Zero_Cycle_Shift"]
    check(
        "zero-cycle shifts carry no cycle time",
        bool((shifts.loc[zero_cycle, "Cycle_Min"] == 0).all()),
    )
    check(
        "zero-cycle shifts carry delay time",
        bool((shifts.loc[zero_cycle, "Delay_Min"] > 0).all()),
    )
    check(
        "ratios are null (not zero) where undefined",
        bool(shifts.loc[zero_cycle, "Idle_Share"].isna().all()),
    )

    print("\nStacked-section parsing...")
    # Several FMS exports put two or three unrelated tables in one CSV. Merging
    # them positionally is silent and produces plausible-looking nonsense, so
    # each derived table is checked against a property only the correct section
    # can satisfy.
    lus = load("loading_unit_summary")
    check(
        "loading_unit_summary keeps its tonnage columns",
        {"Coal_Quantity", "OB_Quantity"} <= set(lus.columns),
        f"columns {sorted(set(lus.columns))}",
    )
    check(
        "loading_unit_summary holds only the trip section",
        bool(lus["Shift_Date"].notna().all() and lus["Shift"].isin([1, 2, 3]).all()),
        f"{int(lus['Shift_Date'].isna().sum())} undated rows",
    )
    check(
        "loading_unit_summary trips are whole numbers",
        bool((lus["Total_Trips"].dropna() % 1 == 0).all()),
    )

    lu_time = load("loading_unit_time")
    share = lu_time["Waiting_Share"].dropna()
    check(
        "loading_unit_time waiting share is a fraction",
        bool(share.between(0, 1).all()),
        f"range {share.min():.2f}-{share.max():.2f}",
    )
    check(
        "loading_unit_time is one row per loader-shift",
        not lu_time.duplicated(["Equipment_ID", "Shift"]).any(),
    )

    routes = load("loading_routes")
    check(
        "loading_routes lead distance is plausible",
        bool(routes["Lead_Km"].dropna().between(0, 60).all()),
        f"max {routes['Lead_Km'].max():.1f} km",
    )

    operators = load("operator")
    loading_side = operators[operators["Operator_Class"] == "Loading unit"]
    check(
        "operator loading-unit rows carry no haul distance",
        bool(loading_side["Haul_Km"].isna().all()),
        "that column holds a productivity rate in the loading section",
    )
    check(
        "operator shifts are 1-3 only",
        bool(operators["Shift"].isin([1, 2, 3]).all()),
    )

    production = load("daily_production")
    per_machine = production.groupby("Equipment_ID")["Shift"].nunique()
    check(
        "daily_production is one row per machine per shift",
        bool((per_machine == 3).all())
        and not production.duplicated(["Equipment_ID", "Shift"]).any(),
        f"{len(per_machine)} machines",
    )
    check(
        "daily_production classifies every machine",
        bool(production["Equipment_Class"].isin(["Dumper", "Shovel", "Payloader"]).all()),
    )

    hauling = load("hauling_summary")
    check(
        "hauling_summary has no blank padding rows",
        bool(hauling["Equipment_ID"].notna().all() and hauling["Shift_Date"].notna().all()),
    )

    status_cat = load("status_category")
    check(
        "status_category has one row per equipment",
        not status_cat.duplicated(["Equipment_ID"]).any(),
        f"{len(status_cat)} rows",
    )
    check(
        "status_category duration is plausible",
        bool(status_cat["Duration_Hours"].dropna().between(0, 750).all()),
        f"{status_cat['Duration_Hours'].min()}-{status_cat['Duration_Hours'].max()}",
    )
    check(
        "status_category status hours are non-negative",
        bool((status_cat[["Delay_Hours", "Down_Hours", "Standby_Hours", "GOH_Hours", "NOH_Hours"]] >= 0).all().all()),
    )

    engine = load("engine_hours")
    check(
        "engine_hours intervals have positive duration",
        bool((engine["Last_Entry"] > engine["First_Entry"]).all())
        and bool((engine["Net_Hours"] > 0).all()),
        f"{len(engine)} intervals",
    )
    check(
        "engine_hours net hours matches cumulative difference",
        bool(np.isclose(
            engine["End_Cumulative_Hours"] - engine["Start_Cumulative_Hours"],
            engine["Net_Hours"],
            rtol=1e-3,
        ).all()),
        f"max diff {(engine['End_Cumulative_Hours'] - engine['Start_Cumulative_Hours'] - engine['Net_Hours']).abs().max():.2f}",
    )

    print("\nLeakage...")
    frame = du.engineer_features(shifts, target=config.TARGET_SHIFT)
    _features, numeric_features, categorical_features = du.feature_matrix(frame)
    used = set(numeric_features) | set(categorical_features)
    banned = set(config.LEAKY_COLUMNS) - set(du.HISTORY_FEATURES)
    leaked = used & banned
    check("no leaky column is used as a feature", not leaked, f"leaked {sorted(leaked)}")
    check(
        "Zero_Cycle_Shift is not a feature",
        "Zero_Cycle_Shift" not in used,
    )

    # A feature that is missing exactly when a shift is fully down lets the
    # imputer smuggle the target in through the missingness pattern.
    print("\nMissingness proxies...")
    proxies: list[str] = []
    for column in sorted(used):
        null = frame[column].isna().to_numpy()
        if null.sum() == 0:
            continue
        agreement = (null == zero_cycle.to_numpy()).mean()
        if agreement > 0.99:
            proxies.append(f"{column} ({agreement:.3f})")
    check(
        "no feature's missingness mirrors Zero_Cycle_Shift",
        not proxies,
        "; ".join(proxies),
    )

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} invariants passed.")
    if FAILURES:
        print("Failed: " + ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
