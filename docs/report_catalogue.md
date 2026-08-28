# FMS Report Catalogue — What the Idle-Time Dashboard Needs

This catalogue is the borderline filter for the app upload feature. Any uploaded CSV is first classified against the signatures below; if it does not match any useful report, the user is told exactly which reports, columns and relationships are required.

## 1. Relevant reports, ordered by usefulness

| Rank | Report key | FMS report name (typical) | Why it matters | Required for training? |
|---|---|---|---|---|
| 1 | `cycles` | **Dumper Cycle Time** | The base grain. Every cycle is decomposed into productive vs idle minutes (queue, stopped, travel, loading, dumping, spotting). | **Yes** — hard requirement |
| 2 | `delay_events` | **Delays and Downs by Equipment, Shift, Status Code and Sub-Status** | Reason-coded out-of-cycle idle (Shift Change, Tea/Breakfast, Marching, Breakdown, etc.). Drives the root cause explorer and action playbook. | **Yes** — hard requirement |
| 3 | `idle_events` | **Dumper Idle Time** | Explicit idle events with real start/end timestamps. Authoritative stand-still measure; used for hour-of-day profile. | **No** — improves precision, not required for model |
| 4 | `tkph` | **Productivity TKPH-TMPH by Fleet and Hauling Unit** | Per-dumper haul/empty distance and travel hours. Proves cycle-report column swap and gives haul-geometry context. | **No** — improves route/distance features |
| 5 | `dumper_shift` | **Dumper_QSE_Report** | Operator-level shift records: availability, breakdown hours, canteen break, first-load delay, tonnes. Adds shift context the cycle report cannot see. | **No** — enriches model context |
| 6 | `fuel` | **Fuel Consumption by Hauling Unit** | Litres per dumper. Used for fuel-burn cost and efficiency metrics. | **No** — cost/efficiency only |
| 7 | `status_summary` | **Status and Sub-Status by Hauling Unit** | Monthly totals per reason, used to cross-check delay event totals. | **No** — validation/cross-check only |
| 8 | `shovel_shift` | **SHOVEL_QSE_REPORT** | Shovel-level shift records: available/run/breakdown hours, marching, face preparation. Upstream congestion signal, non-leaky. | **No** — enriches model context |
| 9 | `hauling_summary` | **Hauling Unit Summary Report_QSE** | Trips, coal/OB split, first/last load per shift. QSE-site only (38 of 70 dumpers). | **No** — enriches model context |
| 10 | `loading_unit_summary` | **Loading Unit Summary Report** | Loader-side trips/quantity per shift. Stacked SSRS export (3 sections). | **No** — cross-check for shovel loading |
| 11 | `daily_production` | **Daily Production Report QSE** | Independent availability/utilization/breakdown per machine per shift. Cross-check for shift spine. | **No** — validation/cross-check |
| 12 | `loader_profile` | **Loading Unit Profile** | Per-loader event log: status code, duration, hauling unit. Attributes loader stoppages to the waiting truck. | **No** — loader-side analysis |
| 13 | `operator` | **Operator Performance Report** | Operator-grain trips, tonnes, run hours. Stacked: hauling-unit and loading-unit sections. | **No** — operator analysis |
| 14 | `payload_cycles` | **Payload by Hauling Unit** | Cycle-grain payload + duration. Cross-check for the cycle table. | **No** — validation/cross-check |
| 15 | `loading_unit_time` | *(derived from Loading Unit Summary)* | Loader waiting/loading time per shift. Surfaces the shovel-waiting finding. | **No** — derived |
| 16 | `loading_routes` | *(derived from Loading Unit Summary)* | Route/lead-distance detail per loader. | **No** — derived |
| 17 | `status_category` | **Time by Status Category, Fleet and Equipment** | Total hours per equipment in delay/down/standby/GOH/NOH. | **No** — validation/cross-check |
| 18 | `engine_hours` | **Net Engine Hours by Fleet and Hauling Unit** | Per-dumper engine-run intervals with start/end timestamps and cumulative/net hours. | **No** — equipment utilisation |

## 2. Hard requirements for a "useful" upload

A batch is **accepted as relevant** only if it contains at least:
- **One `cycles` file**, and
- **One `delay_events` file**.

These two reports provide the minimum grain: when a dumper cycled, how much idle time was inside the cycle, and what reason-coded delays were logged against the same dumper in the same shift. Everything else is enrichment.

## 3. Report signatures and required columns

### 3.1 `cycles` — Dumper Cycle Time

**Canonical columns used by the loader (after `column_mapping.json` standardisation):**

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity** | `Equipment_ID`, `Loading_Unit`, `Operator`, `Fleet` | `HAULING_UNIT_IDENT`, `LOADING_UNIT_IDENT`, `Operator`, `FLEET_IDENT` | Link cycles to dumper, shovel, operator and fleet. |
| **Shift grain** | `Shift_Date`, `Shift` | `LOAD_START_SHIFT_DATE`, `SHIFT_IDENT` | Aggregate one row per dumper × shift. |
| **Route / material** | `Load_Location`, `Dump_Location`, `Material` | `LOAD_LOCATION_SNAME`, `DUMP_LOCATION_SNAME`, `MATERIAL` | Build route strings and classify QSE/QAB. |
| **Cycle time buckets** | `Cycle_Time`, `Travel_Empty`, `Travel_Loaded`, `Loading_Time`, `Dumping_Time`, `Queue_Shovel`, `Queue_Dump`, `Stopped_Empty`, `Stopped_At_Face`, `Stopped_Loaded`, `Spotting` | `CYCLE_TIME`, `EMPTY_STOPPED_TIME_NEW`, `HAULING_STOPPED_TIME_NEW`, `WAITING_TIME_LU`, `WAITING_TIME_DUMP`, etc. | Decompose every cycle. `Cycle_Time` must equal the sum of the ten buckets. |
| **Payload** | `Payload` | `HAULING_UNIT_PAYLOAD2`, `QUANTITY_REPORTING` | Tonnes moved in that cycle. |

**Key metrics produced:** `Cycle_Min`, `Idle_Min`, `Queue_Min`, `Stopped_Min`, `Productive_Min`, `Spotting_Min`, `Idle_Share`.

**Validation rule:** at least `Equipment_ID`, `Shift_Date` and `Cycle_Time` must be present and `Cycle_Time` must be positive.

**Critical relationship:** `Cycle_Time` should be very close to the sum of the ten bucket columns. If buckets are missing, the loader falls back to zeroing them and the app will flag a data-quality warning.

### 3.2 `delay_events` — Delays and Downs

**Canonical columns used:**

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity / shift** | `Shift_Date`, `Shift`, `Equipment_ID`, `Fleet` | `SHIFT_DATE1`, `EquipmentNo`, `FLEET_IDENT` | Merge with cycles on `(Equipment_ID, Shift_Date, Shift)`. |
| **Reason** | `Status_Desc`, `Sub_Status_Desc` | `STATUS_DESC`, `SUB_STATUS_DESC` | Extract `Reason` and classify as Organisational / Operational / Mechanical / External. |
| **Duration / time** | `Duration_Min` (or `Start_Timestamp` + `End_Timestamp`) | `duration`, `Time Start`, `Time End` | Minutes of delay. |
| **Comment** | `Status_Comment` | `Comments`, `STATUS_COMMENT` | Optional context. |

**Key metrics produced:** `Delay_Min`, `Reason`, `Reason_Class`, `Addressable`.

**Validation rule:** at least `Equipment_ID`, `Shift_Date`, `Status_Desc`, `Duration_Min` (or start/end time) must be present.

**Critical relationship:** must join to `cycles` on `(Equipment_ID, Shift_Date, Shift)` to attribute out-of-cycle delay to the same shift.

### 3.3 `idle_events` — Dumper Idle Time

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity / time** | `Equipment_ID`, `Start_Timestamp`, `End_Timestamp` | `DUMPER NO`, `Start Time`, `End Time` | Real clock time of each idle spell. |
| **Duration** | `Duration` or computed from start/end | `Duration` | Idle minutes. |
| **Engine status** | `Status` | `Status` | Usually `ON` — engine running while idle. |

**Validation rule:** at least `Equipment_ID` and either `Duration` or both start/end timestamps.

**Critical relationship:** timestamps are used to derive `Shift_Date` and `Shift`, so it can stand alone but is also useful for cross-checking `cycles` idle.

### 3.4 `tkph` — Productivity TKPH-TMPH

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity** | `Equipment_ID`, `Fleet` | `NAME`, `FLT_DESC` | One row per dumper per upload period. |
| **Distance / load** | `Haul_Km`, `Empty_Km`, `Loads`, `Tonnes` | `HAUL_DISTANCE`, `EMPTY_DISTANCE`, `NUM_LOADS2`, `Textbox169` | Compute `Km_Per_Cycle` and `Avg_Speed_Kmph`. |

**Validation rule:** `Equipment_ID` and at least one of `Haul_Km` / `Empty_Km` / `Loads`.

**Critical relationship:** one static distance per dumper, merged by `Equipment_ID` into the shift master.

### 3.5 `dumper_shift` — Dumper_QSE_Report

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity / shift** | `Shift_Date`, `Shift`, `Equipment_ID`, `Operator`, `Loading_Equipment` | `SHIFT_DATE1`, `BREAKDOWN_HR`, etc. | Shift-level summary per dumper. |
| **Availability** | `Run_Hours`, `Breakdown_Hours`, `Available_Hours` | `Run_Hours`, `BREAKDOWN_HR`, `AVAILABIL_HR` | Explain idle outside the cycle. |
| **Breaks / delays** | `Canteen_Break_Min`, `First_Load_Delay_Min`, `Last_Load_Delay_Min` | `Canteen_Break`, `First_load_Delay` | Shift changeover and break context. |
| **Distance / tonnes** | `Lead_Distance_Km`, `Tonnes` | `LEAD_DISTANCE`, `Total_OB_Tonnage2`, `Total_Coal_Tonnage` | Extra haul context. |

**Validation rule:** `Equipment_ID` and `Shift_Date`.

**Critical relationship:** merged on `(Equipment_ID, Shift_Date, Shift)` with cycles.

### 3.6 `fuel` — Fuel Consumption by Hauling Unit

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity** | `Equipment_ID`, `Fleet` | `EquipmentNo`, `Fleet` | Aggregate litres per dumper. |
| **Fuel** | `Fuel_Litres` | `FUEL_CONSUMED`, `FUEL_CONSUMPTION2` | Total litres consumed. |

**Validation rule:** `Equipment_ID` and `Fuel_Litres`.

**Critical relationship:** one row per dumper, merged by `Equipment_ID` to `shifts` for fuel-efficiency metrics.

### 3.7 `status_summary` — Status and Sub-Status by Hauling Unit

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity / reason** | `Equipment_ID`, `Status_Category`, `Status_Desc`, `Reason` | `EQUIP_IDENT`, `STATUSCAT_DESCRIP`, `STATUS_DESC` | Monthly totals by reason. |
| **Duration** | `Hours` | `DURATION` | Hours in that status. |

**Validation rule:** `Equipment_ID`, `Status_Desc`, `Hours`.

**Critical relationship:** grouped by `Reason` to cross-check the `delay_events` totals in `build_reason_master`.

### 3.8 `shovel_shift` — SHOVEL_QSE_REPORT

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity / shift** | `Equipment_ID`, `Shift_Date`, `Shift` | `EQUIP_IDENT`, `SHIFT_DATE1`, `SHIFT_IDENT` | Shovel-level shift grain. |
| **Hours** | `Available_Hours`, `Run_Hours`, `Breakdown_Hours`, `Marching_Hours` | `AVAILABIL_HR`, `RUN_HR`, `BREAK_DOWN_HR`, `MARCHING_HR` | Upstream shovel availability. |
| **Face prep** | `Face_Preparation_Min` | `Face_Preparation` | Non-loading time at the face. |

**Validation rule:** `Equipment_ID`, `Shift_Date`, `Available_Hours`.

**Note:** time columns are bare clock times (not timestamps), exposed as `*_Hour` floats.

### 3.9 `hauling_summary` — Hauling Unit Summary Report_QSE

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity / shift** | `Equipment_ID`, `Shift_Date`, `Shift` | `Equipment No`, `Shift Date`, `Shift Ident` | Dumper-shift grain. |
| **Trips / tonnes** | `Total_Trips`, `Coal_Tonnes`, `OB_Tonnes` | `Total_Trips`, `Coal_Quantity`, `OB_Quantity` | Trips and material split. |
| **Timing** | `First_Load_Time`, `Last_Load_Time` | `First_Load`, `Last_Load` | Shift-edge behaviour. |

**Validation rule:** `Equipment_ID`, `Shift_Date`, `Total_Trips`.

**Coverage caveat:** QSE-site only (38 of 70 dumpers), though it covers all 31 days.

### 3.10 `loading_unit_summary` — Loading Unit Summary Report

**Stacked SSRS export** with 3 sections: trips summary (1,263 rows), loader time profile (51 rows), route/lead-distance detail (4,023 rows). Parsed via `read_csv_sections()`.

| Section | Key columns | Purpose |
|---|---|---|
| **Trips** | `Equipment_ID`, `Shift_Date`, `Shift`, `Total_Trips`, `Coal_Quantity`, `OB_Quantity` | Loader trips per shift. |
| **Time profile** *(derived: `loading_unit_time`)* | `Equipment_ID`, `Shift`, `Loading_Min`, `Waiting_Min` | Loader waiting vs loading time. |
| **Routes** *(derived: `loading_routes`)* | `Equipment_ID`, `Shift_Date`, `Load_Location`, `Dump_Location`, `Lead_Km` | Route detail per loader. |

**Note:** `loading_unit_time` and `loading_routes` are period-aggregates by shift number (not per day), so they cannot join the shift master on date.

### 3.11 `daily_production` — Daily Production Report QSE

**Stacked SSRS export** with 3 sections: shovels (6), dumpers (38), payloaders (2). Wide format with `First_Shift_*`, `Second_Shift_*`, `Third_Shift_*` columns, unpivoted to long form.

| Column category | Canonical columns | Purpose |
|---|---|---|
| **Identity** | `Equipment_ID`, `Shift`, `Equipment_Type` | Machine per shift. |
| **Availability** | `Availability`, `Utilization`, `Break_Down_Hours` | Independent cross-check for shift spine. |

### 3.12 `loader_profile` — Loading Unit Profile

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity / date** | `Loading_Unit`, `Shift_Date` | filename, `Timestamp` | Loader event grain. |
| **Event** | `Status`, `Duration_Min` | `Status Code`, `Duration(min)` | Loader stoppage events. |
| **Truck** | `Equipment_ID` | `Hauling Unit` | Attributes stoppage to the waiting truck. |

**Note:** `Timestamp` is date-only. Loading unit ID exists only in the filename.

### 3.13 `operator` — Operator Performance Report

**Stacked SSRS export** with 2 sections: hauling-unit operators (426 rows) and loading-unit operators (111 rows).

| Section | Key columns | Note |
|---|---|---|
| **Hauling** | `Operator_PNo`, `Shift`, `Trips`, `Tonnes`, `Haul_Distance` | HaulDistance is real km. |
| **Loading** | `Operator_PNo`, `Shift`, `Trips`, `Tonnes` | No distance column; the HaulDistance position holds tonnes/run-hour. |

### 3.14 `payload_cycles` — Payload by Hauling Unit

| Column category | Canonical columns | Purpose |
|---|---|---|
| **Identity / shift** | `Equipment_ID`, `Shift_Date`, `Shift` | Cycle grain. |
| **Cycle** | `Cycle_Min`, `Payload` | Cross-check for the cycle table. |

### 3.15 `status_category` — Time by Status Category, Fleet and Equipment

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity** | `Equipment_ID`, `Fleet` | `EQUIP_IDENT`, `firstDayWeek3` | One row per equipment. |
| **Hours** | `Delay_Hours`, `Down_Hours`, `Standby_Hours`, `GOH_Hours`, `NOH_Hours` | `delay`, `down`, `standby`, `goh`, `noh` | Status category totals. |
| **Duration** | `Duration_Hours` | `duration` | Total reporting period. |

**Derived:** `Idle_Hours = Delay_Hours + Standby_Hours`.

### 3.16 `engine_hours` — Net Engine Hours by Fleet and Hauling Unit

| Column category | Canonical columns | Typical raw aliases | Purpose |
|---|---|---|---|
| **Identity** | `Equipment_ID`, `Fleet` | `EQUIP_IDENT`, `FLEET_IDENT` | One row per engine-run interval. |
| **Interval** | `First_Entry`, `Last_Entry` | `ORIGIN_SHORT_NAME2`, `MAX_TIMESTAMP` | Start/end timestamps. |
| **Hours** | `Start_Cumulative_Hours`, `End_Cumulative_Hours`, `Net_Hours` | `DESTINATION_SHORT_NAME2`, `NUM_LOADS2`, `PRODUCTION2` | Cumulative and net engine hours. |

**Validation:** `Net_Hours == End_Cumulative_Hours - Start_Cumulative_Hours` (rtol 1e-3). Rows with `Net_Hours <= 0` or `First_Entry >= Last_Entry` are filtered.

## 4. Inter-report relationships (the join keys)

1. **Cycles + delay events** on `(Equipment_ID, Shift_Date, Shift)` to build `Total_Idle_Min = Cycle_Idle_Min + Delay_Min`.
2. **Cycles + idle events** on the same three keys for cross-check of measured idle.
3. **Cycles + dumper_shift** on the same three keys for shift context (breakdown, availability).
4. **Cycles + tkph** on `Equipment_ID` for per-dumper distance constants.
5. **Cycles + fuel** on `Equipment_ID` for per-dumper fuel totals.
6. **Delay events + status_summary** on `Reason` for monthly cross-check.
7. **Cycles + shovel_shift** on `(Shift_Date, Shift, Loading_Unit)` for upstream congestion.
8. **Cycles + payload_cycles** on `(Equipment_ID, Shift_Date, Shift)` for payload cross-check.
9. **Loading unit summary** sections: trips ↔ loading_unit_time ↔ loading_routes (derived from same file).
10. **Status_category + delay_events** on `Equipment_ID` for status-hours cross-check.
11. **Engine_hours** on `Equipment_ID` for utilisation vs idle comparison.

## 5. Use this catalogue in the upload validator

The validator runs in this order for every uploaded file:

1. **Classify** using `data_utils.classify_file` (filename tokens first, then header signature columns).
2. **If `unknown`**: reject and show the report names and signature columns from section 1 and 3.
3. **If classified as `cycles` or `delay_events`**: load with the canonical loader, check the required columns, report any missing categories.
4. **After all files**: verify that the batch has at least one `cycles` and one `delay_events` file; otherwise explain that the model needs both the in-cycle idle and the reason-coded delay grain.
5. **Warn** (do not reject) if optional reports are missing — the model can still train, but some dashboard pages will be less rich.
