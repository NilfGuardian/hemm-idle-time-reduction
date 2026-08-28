# Idle Time Reduction in HEMM

A Streamlit application that turns raw Fleet Management System exports from
Tata Steel West Bokaro into a quantified, costed and actionable picture of dumper
idle time.

**Live dashboard:** [optihaul.streamlit.app](https://optihaul.streamlit.app)

**Desktop app:** [Download OptiHaul-Setup.exe](https://github.com/NilfGuardian/hemm-idle-time-reduction/releases/latest/download/OptiHaul-Setup.exe) (Windows 10/11, no Python required)

**Source code:** [github.com/NilfGuardian/hemm-idle-time-reduction](https://github.com/NilfGuardian/hemm-idle-time-reduction)

**The problem in one line:** across July 2026 the 69-dumper fleet lost roughly
**4 hours of every 8-hour shift** to idle time, and most of it is a scheduling
decision rather than a broken machine.

---

## Quick start

### Option A — Use the live dashboard (no setup)

Open the live dashboard link above in any browser. All data and the trained
model are bundled in the repository, so the app works immediately with no
coding or installation required.

### Option B — Download the desktop app (no Python needed)

Download [OptiHaul-Setup.exe](https://github.com/NilfGuardian/hemm-idle-time-reduction/releases/latest/download/OptiHaul-Setup.exe)
(~275 MB), double-click to install, and launch from the desktop shortcut.
The installer uses a retro-terminal industrial theme and handles everything —
no Python, no dependencies, no terminal required.

### Option C — Run locally

Python 3.10+ is required.

```bash
pip install -r requirements.txt

python scripts/ingest.py     # parse the FMS exports, build tables, train the model
streamlit run app.py         # open the dashboard at http://localhost:8501
```

`scripts/ingest.py` takes about 15 seconds. It reads the folders listed in
`config.DEFAULT_SOURCE_DIRS`, writes parquet tables to `data/processed/` and
saves the trained model to `models/base_model.pkl`. To point it at a different
folder:

```bash
python scripts/ingest.py "D:\some\other\folder"
```

---

## What it does

1. **Parses** 25 messy SSRS-style FMS CSV exports without manual cleanup.
2. **Quantifies** idle time three independent ways and cross-checks them.
3. **Explains** the idle using the FMS reason codes, grouped by who can actually
   change them.
4. **Models** idle minutes per dumper-shift so abnormal shifts can be separated
   from shifts that were simply hard.
5. **Simulates** scheduling changes with every assumption exposed as a slider.
6. **Exports** a management summary and the underlying tables.

### Upload new FMS reports directly in the app

Once the dashboard is running, use the **Upload new FMS reports** panel in the
sidebar to add more data without touching the terminal:

1. Drop one or more FMS CSV exports into the file uploader.
2. The app classifies each file, checks the required columns and reports any
   missing or irrelevant files.
3. If the batch is valid, click **Add to dataset & retrain**.
4. The app appends the new rows to the existing processed data, rebuilds the
   master tables, and retrains the model on the combined dataset.

**Hard requirements** for a valid batch:

- at least one `Dumper Cycle Time` report, and
- at least one `Delays and Downs` report.

Optional reports improve the dashboard but are not required: `Dumper Idle Time`,
`Productivity TKPH-TMPH`, `Dumper_QSE_Report`, `Fuel Consumption` and
`Status and Sub-Status by Hauling Unit`.

The full list of accepted reports, their required columns and the relationships
between those columns is documented in `docs/report_catalogue.md`.

Verify every page still renders after a change:

```bash
python scripts/smoke_test.py
```

---

## The pages

Navigation is a top bar grouped by what a reviewer actually wants to do:
**Analyse**, **Plan**, **Export**.

| Group | Page | Question it answers |
| --- | --- | --- |
| — | **Overview** | How much are we losing, what does it cost, what are the top four actions? |
| Analyse | **Idle breakdown** | Where does a haul cycle actually go, and when does idle happen? |
| Analyse | **Fleet & risk ranking** | Which dumpers, shovels and routes lose most — and which are flagged as high risk by the model? |
| Analyse | **Root cause explorer** | What is the fleet waiting for, and who controls it? |
| Plan | **Action playbook** | For each cause, exactly what to do, priced live from the current filters — scenario checklists, a reliability program for breakdowns, and dispatch fixes for shovel congestion. |
| Plan | **Scenario simulator** | If we change the schedule, what do we get back? |
| Export | **Reports & exports** | Management summary, data exports, model card, limitations. |

---

## What the data showed

### 1. The FMS column names are wrong, and it matters enormously

The `Dumper Cycle Time` report has ten time buckets that sum exactly to
`CYCLE_TIME`. Four of them are misnamed:

| Raw column | What it actually contains |
| --- | --- |
| `EMPTY_STOPPED_TIME_NEW` | Travel time **empty** |
| `HAULING_STOPPED_TIME_NEW` | Travel time **loaded** |
| `EMPTY_TRAVEL` | **Stopped** time while empty |
| `LOAD_HAUL_TIME` | **Stopped** time while loaded |

This was established by correlating each column against the per-dumper haul and
empty distances in the `Productivity TKPH-TMPH` report across 63 dumpers:

- Corrected reading: r = 0.88 and 0.96 against distance, implying **16.2 km/h
  empty and 15.7 km/h loaded** — realistic, and loaded is slower than empty.
- Literal reading: implies **179 km/h loaded** — impossible.

Taking the names at face value would have reported 64% of cycle time as idle.
The correct figure is **27.5%**.

### 2. Three independent measurements agree

| Measure | Source report | July total |
| --- | --- | --- |
| In-cycle idle | Dumper Cycle Time | 6,982 h |
| Reason-coded delay | Delays and Downs | 28,108 h |
| Logged stand-still | Dumper Idle Time | 12,145 h |

The idle log records 18,640 discrete events averaging 39 minutes, and the status
is `ON` in every single one — **idle time burns diesel**.

### 3. The biggest addressable loss is shift changeover

| Reason | Hours (July) | Class | Addressable |
| --- | --- | --- | --- |
| Down | 14,412 | Mechanical | No |
| **Shift Change** | **8,103** | **Organisational** | **Yes** |
| **Tea / Breakfast / Snacks** | **2,686** | **Organisational** | **Yes** |
| Daily Service | 1,732 | Mechanical (planned) | No |
| Marching | 321 | Organisational | Yes |
| Change Operators | 314 | Organisational | Yes |

Shift Change alone is **8,103 hours**, about **1.26 hours per dumper per
changeover**. That is not operators working slowly; it is the entire fleet
stopping at the same moment while crews travel to and from the change house.

### 4. Framing the recommendations

Break entitlements are **never** proposed for reduction. Tea, breakfast and
toilet breaks are a worker right, and the analysis says so explicitly. The lever
attached to them is **staggering**: split the fleet into break groups so shovels
always have trucks presenting. Operators keep every minute of their break.

The same logic drives the changeover recommendation: **hot-seat changeover**,
where the relief operator comes to the machine, rather than parking the fleet.

---

## The model

Two models are trained on the same honest feature set, because they answer
different questions and the difference matters for trust:

| Model | Question | Metric | Score |
| --- | --- | --- | --- |
| **Risk classifier** (Random Forest) | Will this dumper-shift land in the worst third of the month for idle? | Test AUC | **0.77** |
| **Minutes regressor** (Ridge) | Exactly how many minutes will this shift lose? | Test R² | **0.21** |

Both use a chronological split — trained on early July, scored on late July —
so the numbers reflect genuine forward prediction, not interpolation between
neighbouring shifts of the same dumper.

**An earlier version reported R² = 0.50, and it was wrong.** `Cycles` was
included as a feature because it looks like an ordinary workload measure. It
is not: in a fixed 8-hour shift, `Cycle_Min = 480 - Delay_Min`, so `Cycles`
(cycle minutes divided by the average cycle length) correlates at **r = 0.95**
with a value derived directly from the delay component of the target. The
model was not predicting idle time from `Cycles`, it was reading the target
off a mechanically related quantity — the same category of mistake as an even
earlier version that used `Productive_Min` (a literal component of idle) and
scored R² = 0.74. Both are now blacklisted in `config.LEAKY_COLUMNS`, along
with `Payload_Tonnes` for the same reason.

**Why the honest R² is still only 0.21.** With the leaky features gone, more
than half of what remains in shift-to-shift idle variance is driven by
*when a specific dumper breaks down*. The per-dumper "Down" hours have a
coefficient of variation of ~1.0 across the fleet — the statistical signature
of a random failure process. No amount of feature engineering on schedule and
workload data predicts that; it needs maintenance and fault-code history,
which is not in the FMS exports collected for this project (see
[Future work](#future-work)).

**Why the classifier is the one to trust.** Asking "is this shift at risk of
being bad" is a coarser, easier question than "exactly how many minutes will
it lose", and the same honest features answer it well: AUC 0.77 on shifts the
model never saw during training. This is what the dashboard leads with —
Fleet Performance's **Idle risk** tab and the Reports model card both present
it first, with the minutes regressor kept as a secondary, explicitly-caveated
estimate.

Top features for the risk classifier: the shovel's own delay minutes, haul
kilometres per cycle, how many dumpers are competing for the same shovel, and
each dumper's own recent idle history.

---

## Project structure

```
config.py                  All constants: idle taxonomy, reason rules, levers, costs, theme
column_mapping.json        ~190 raw FMS column aliases -> canonical names
data_utils.py              Parsing, cleaning, aggregation, features, model
data_upload.py             Upload validation, append & rebuild workflow
app.py                     Router: page config, theme, st.navigation
app_pages/                 Overview + the five analysis/plan/export pages
utils/
    helpers.py             Formatting, Indian number parsing, cost maths
    ui.py                  Shared filters, KPI cards, data loading, theming
    charts.py              Themed Plotly builders
scripts/
    ingest.py              One command to rebuild everything
    validate_pipeline.py   23 data/model invariant checks
    smoke_test.py          Headless render check for all pages
data/raw/                  Raw FMS CSV exports (committed for deployment)
data/processed/            Generated parquet tables + provenance.json
models/base_model.pkl      Trained bundle (compressed, well under 100 MB)
static/                    Three.js animations and CSS
desktop_app.py             Pywebview launcher (streamlit.web.bootstrap in-process)
installer/
    setup_installer.py     Themed installer: extract, shortcuts, registry
    installer_ui.html      Retro terminal UI (black/lime, ASCII, CRT animations)
build/
    desktop.spec           PyInstaller spec — Phase 1 (onedir app bundle)
    installer.spec         PyInstaller spec — Phase 2 (onefile installer)
    build_installer.bat    One-click build script (Phase 1 + Phase 2)
    README.md              Build instructions and notes
requirements-build.txt     Build-time deps (pywebview, pyinstaller)
```

---

## Deployment (Streamlit Community Cloud)

The app is deployed on [Streamlit Community Cloud](https://share.streamlit.io),
which connects directly to the GitHub repo. Both raw CSVs and processed parquet
tables are committed to the repo, so the app starts instantly with no build step.

### Steps to deploy

1. Push the repo to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
3. Click **New app**, select the repo, and set the main file to `app.py`.
4. Set Python version to **3.12**.
5. Click **Deploy**. The app will be live at
   `https://<app-name>.streamlit.app` within a minute.

### Notes

- The upload feature works within a session but resets when the app restarts
  (Streamlit Cloud has an ephemeral filesystem). The committed data always loads
  on startup.
- The model can be retrained live from the sidebar — the retrained model also
  resets on restart, but the committed `base_model.pkl` is always available.

### Parsing notes

FMS exports are SSRS reports saved as CSV, which means:

- A banner row of `textbox1, textbox2, ...` placeholders sits above the real
  header. `detect_header_row` scores candidate rows and discounts placeholder
  names and prose, so the genuine header wins.
- Column names collide only by case: `DURATION` is in **hours**, `duration` is
  in **minutes**. The mapping lookup is therefore case-sensitive first.
- Numbers use Indian digit grouping (`1,07,912.55`).
- Null tokens include `--N.D--`, `#Error` and `#DIV/0!`.
- Five different date formats appear; the parser picks one format per column by
  testing which parses the most values, rather than guessing per row.
- Rows repeating `Total` / `Grand Total` are subtotal labels, not subtotal rows,
  so they are filtered on the identifier column only.

---

## Limitations

- **`START_TIMESTAMP` in the cycle report is unusable.** Its dates and times
  disagree with the shift columns and some rows land in June. Cycles are dated
  from `LOAD_START_SHIFT_DATE` and `LOAD_START_SHIFT_IDENT` only, and all
  hour-of-day analysis comes from the two reports with trustworthy clock times.
- **Sub-status codes are blank** for most Delay and Down events, so the status
  code carries the reason. Sub-status is populated for Net Operating states only.
- **`FUEL_CONSUMED` in the cycle report is empty** throughout. Fuel comes from
  the separate per-dumper monthly report and cannot be split by shift.
- **Availability context is sparse.** `Dumper_QSE_Report` covers roughly half the
  dumper-shifts, so breakdown and availability features are imputed.
- **One month only.** July is monsoon season in Ramgarh; wet haul roads may
  inflate stopped-in-trip time. There is no dry-month comparison available.
- **Idle cost per hour is an assumption**, adjustable in the sidebar, with a
  sensitivity table on the Simulation page. Fuel litres and tonnes are measured.

## Future work

- **Maintenance/fault-code history** (fault codes, hours since last service,
  dumper age) — the single biggest lever on model accuracy, since it would let
  the model predict breakdown risk instead of treating it as noise.
- **Rainfall/weather records** for Ramgarh/Bokaro — haul-road condition is a
  plausible driver of stopped-in-trip time that the FMS does not capture.
- **The FMS dispatch/roster plan** (planned truck-to-shovel assignment) — this
  would convert the congestion features from same-shift measurements into
  genuinely pre-shift-known information, enabling real forecasting rather than
  post-hoc benchmarking.
- A second month of data to separate monsoon effects from structural idle.
- Sub-status codes enabled in the FMS for Delay and Down events, which would
  split "Down" into actionable failure modes.
- Per-shift fuel readings to cost idle directly instead of via a burn-rate
  assumption.
- GPS position data to distinguish queueing at a face from stopping on a ramp.

---

## Demo script

1. **Overview** — lead with total idle hours, the cost, and the fact that
   4 of every 8 shift hours are idle. Point at the four ranked actions.
2. **Analyse → Idle breakdown → Definitions & data quality** — show the
   corrected column semantics and the 179 km/h proof. This is the analytical
   credibility moment.
3. **Analyse → Idle breakdown → When it happens** — the hour-of-day chart with
   06:00, 14:00 and 22:00 highlighted. The changeover signature is visible
   instantly.
4. **Analyse → Root cause explorer** — the Pareto and the donut. State that
   organisational causes dominate the addressable pool, and that breaks are
   staggered, never cut.
5. **Analyse → Fleet & risk ranking → Idle risk (model)** — lead with the
   classifier's 0.77 AUC, then explain honestly why the companion
   minutes-regressor's R² is only 0.21: over half the remaining variance is
   unpredictable machine breakdown, not a modelling shortfall.
6. **Plan → Action playbook** — this is where insight becomes instruction: an
   implementation checklist per cause, priced live, plus the reliability
   program for breakdowns and the dispatch fix for shovel congestion.
7. **Plan → Scenario simulator** — move a slider live. Show the annualised
   value and then the sensitivity table, making clear which numbers are
   measured and which assumed.
8. **Export → Reports & exports** — download the management summary and open
   the model card. The **Desktop app** tab has a one-click download link for the
   standalone Windows installer.

---

## Desktop app (standalone Windows installer)

A self-contained Windows installer is built from the same codebase using
PyInstaller and pywebview. The client experience is:

1. Download `OptiHaul-Setup.exe` from the
   [GitHub Release](https://github.com/NilfGuardian/hemm-idle-time-reduction/releases/latest/download/OptiHaul-Setup.exe).
2. Double-click → retro-terminal installer UI appears.
3. Click **[ Install ]** → app extracted to `%LOCALAPPDATA%\OptiHaul`.
4. Click **[ Launch OptiHaul ]** → dashboard opens in a native window.

No Python, no pip, no terminal. The installer creates desktop and Start Menu
shortcuts and registers an uninstaller in Add or Remove Programs.

### Building the installer

```bash
pip install -r requirements-build.txt
pip install -r requirements.txt   # app deps must be installed too
build\build_installer.bat         # produces dist\OptiHaul-Setup.exe
```

See [`build/README.md`](build/README.md) for details.
