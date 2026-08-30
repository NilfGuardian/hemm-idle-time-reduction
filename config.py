"""Central configuration for the HEMM Idle Time Reduction proof of concept.

Plain Python so the app has zero extra dependencies (no YAML parser needed) and
runs on this machine with the already-installed packages.

Everything in this file is tuned around the single project goal:
    reduce idle time of hauling units (dumpers) in the QSE/QAB mining sections.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_NAME = "Idle Time Reduction in HEMM"
PROJECT_SUBTITLE = "Tata Steel West Bokaro | QSE / QAB Sections | July 2026"
VERSION = "1.0.0"

if getattr(sys, "frozen", False):
    BUNDLE_ROOT = Path(sys._MEIPASS)
    ROOT = Path(sys.executable).resolve().parent
else:
    BUNDLE_ROOT = ROOT = Path(__file__).resolve().parent

RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = BUNDLE_ROOT / "data" / "processed"
WRITABLE_PROCESSED_DIR = ROOT / "data" / "processed"
MODEL_DIR = BUNDLE_ROOT / "models"
COLUMN_MAPPING_PATH = BUNDLE_ROOT / "column_mapping.json"

MODEL_PATH = MODEL_DIR / "base_model.pkl"

# Folders scanned by scripts/ingest.py when no explicit path is given.
# The pROBElIST folder holds single-day probe exports that the full-month files
# in "new files" supersede, so it is deliberately excluded.
DEFAULT_SOURCE_DIRS = [
    RAW_DIR,
    RAW_DIR / "new files",
]

# --------------------------------------------------------------------------- #
# Shifts
# --------------------------------------------------------------------------- #
# Shift 3 runs 22:00 -> 06:00 and is attributed to the *starting* calendar day,
# which is how the FMS reports its SHIFT_DATE.
SHIFT_WINDOWS: dict[int, tuple[int, int]] = {
    1: (6, 14),
    2: (14, 22),
    3: (22, 6),
}
SHIFT_LENGTH_HOURS = 8.0
SHIFT_LABELS = {1: "Shift 1 (06-14)", 2: "Shift 2 (14-22)", 3: "Shift 3 (22-06)"}

# --------------------------------------------------------------------------- #
# Idle definition  -- the core of the project
# --------------------------------------------------------------------------- #
# IMPORTANT: the raw column names in the FMS "Dumper Cycle Time" report are
# swapped. The FMS labels are backwards — verified by the round-trip argument:
#
# A haul cycle is a round trip (shovel -> dump -> shovel), so the distance is
# the same both ways. A loaded truck is slower, so loaded travel MUST take
# more time than empty travel.
#
#   EMPTY_STOPPED_TIME_NEW    = 9.14 min/cycle -> travel time LOADED (slower)
#   HAULING_STOPPED_TIME_NEW  = 6.49 min/cycle -> travel time EMPTY (faster)
#   EMPTY_TRAVEL              = 4.75 min/cycle -> STOPPED while loaded
#   LOAD_HAUL_TIME            = 0.57 min/cycle -> STOPPED while empty
#
# Implied speeds (using average round-trip distance of 1.85 km/cycle):
#   Loaded: 12.4 km/h  (slower, heavy truck uphill)
#   Empty:  16.9 km/h  (faster, light truck downhill)
#
# The earlier correlation analysis with TKPH distances was misleading because
# TKPH "Empty_Km" includes all empty driving (parking, fuel bay, shovel changes),
# not just the return trip. The round-trip physical constraint is definitive.
#
# column_mapping.json renames them to the canonical names used below.
# CYCLE_TIME is exactly the sum of the ten buckets; all units are minutes.

PRODUCTIVE_BUCKETS = {
    "Travel_Empty": "Travel (empty)",
    "Travel_Loaded": "Travel (loaded)",
    "Loading_Time": "Loading",
    "Dumping_Time": "Dumping",
}

# Idle buckets. Key = canonical column, value = label shown in the UI.
IDLE_BUCKETS = {
    "Queue_Shovel": "Queue at shovel",
    "Queue_Dump": "Queue at dump",
    "Stopped_Empty": "Stopped while empty",
    "Stopped_At_Face": "Stopped at face",
    "Stopped_Loaded": "Stopped while loaded",
}

# Spotting is manoeuvring: necessary, but compressible. Kept out of core idle so
# the headline number is defensible; shown separately as "semi-productive".
SEMI_PRODUCTIVE_BUCKETS = {"Spotting": "Spotting"}
INCLUDE_SPOTTING_IN_IDLE = False

QUEUE_BUCKETS = ["Queue_Shovel", "Queue_Dump"]
STOPPED_BUCKETS = ["Stopped_Empty", "Stopped_At_Face", "Stopped_Loaded"]

ALL_CYCLE_BUCKETS = (
    list(PRODUCTIVE_BUCKETS) + list(IDLE_BUCKETS) + list(SEMI_PRODUCTIVE_BUCKETS)
)

BUCKET_LABELS = {**PRODUCTIVE_BUCKETS, **IDLE_BUCKETS, **SEMI_PRODUCTIVE_BUCKETS}


def idle_bucket_columns() -> list[str]:
    """Return the canonical cycle columns that make up idle time."""
    cols = list(IDLE_BUCKETS)
    if INCLUDE_SPOTTING_IN_IDLE:
        cols += list(SEMI_PRODUCTIVE_BUCKETS)
    return cols


# --------------------------------------------------------------------------- #
# Reason taxonomy -- turns FMS status codes into levers management can pull
# --------------------------------------------------------------------------- #
# Buckets ordered by how much control the mine has over them.
REASON_CLASS_ORDER = ["Organisational", "Operational", "Mechanical", "External"]

REASON_CLASS_COLORS = {
    "Organisational": "#e67e22",  # the addressable one -> Tata orange
    "Operational": "#2e86c1",
    "Mechanical": "#7f8c8d",
    "External": "#95a5a6",
    "Unclassified": "#bdc3c7",
}

# Substring -> (class, addressable?)  matched case-insensitively, first hit wins.
# Order matters: the more specific rule must come before the generic one, e.g.
# "daily service" is planned maintenance and must be caught before "service".
REASON_RULES: list[tuple[str, str, bool]] = [
    # --- Mechanical: planned maintenance. Specific rules first. -------------
    ("daily service", "Mechanical", False),
    ("scheduled service", "Mechanical", False),
    ("shift inspection", "Mechanical", False),
    ("breakdown maintenance", "Mechanical", False),
    # --- Organisational: schedule, coordination and manning. Addressable. ---
    ("shift change", "Organisational", True),
    ("shift chnage", "Organisational", True),
    ("change operator", "Organisational", True),
    ("operator change", "Organisational", True),
    ("crew change", "Organisational", True),
    ("marching", "Organisational", True),
    ("tea", "Organisational", True),
    ("breakfast", "Organisational", True),
    ("snack", "Organisational", True),
    ("lunch", "Organisational", True),
    ("canteen", "Organisational", True),
    ("meal", "Organisational", True),
    ("toilet", "Organisational", True),
    ("no operator", "Organisational", True),
    ("operator not", "Organisational", True),
    ("without operator", "Organisational", True),
    ("incharge instruction", "Organisational", True),
    ("shift incharge", "Organisational", True),
    ("no work", "Organisational", True),
    ("no dumper", "Organisational", True),
    ("no shovel", "Organisational", True),
    ("no load", "Organisational", True),
    ("waiting for", "Organisational", True),
    ("idle", "Organisational", True),
    ("rest", "Organisational", True),
    # --- Operational: process delays inside the haul cycle. Addressable. ----
    ("hopper", "Operational", True),
    ("fuel filling", "Operational", True),
    ("fuelling", "Operational", True),
    ("refuel", "Operational", True),
    ("weigh", "Operational", True),
    ("queue", "Operational", True),
    ("wait at dump", "Operational", True),
    ("wait at lu", "Operational", True),
    ("wait at", "Operational", True),
    ("dozer", "Operational", True),
    ("stuck in mud", "Operational", True),
    ("road", "Operational", True),
    ("dump full", "Operational", True),
    ("dump not", "Operational", True),
    ("face", "Operational", True),
    ("tyre", "Operational", True),
    ("water spray", "Operational", True),
    ("spotting", "Operational", True),
    ("empty stopped", "Operational", True),
    ("hauling", "Operational", True),
    ("empty", "Operational", True),
    ("loading", "Operational", True),
    ("dumping", "Operational", True),
    # --- Mechanical: unplanned. Not addressable by scheduling. --------------
    ("down", "Mechanical", False),
    ("breakdown", "Mechanical", False),
    ("repair", "Mechanical", False),
    ("maintenance", "Mechanical", False),
    ("mechanical", "Mechanical", False),
    ("electrical", "Mechanical", False),
    ("engine", "Mechanical", False),
    ("hydraulic", "Mechanical", False),
    ("service", "Mechanical", False),
    ("inspection", "Mechanical", False),
    # --- External: outside management control. ------------------------------
    ("blast", "External", False),
    ("rain", "External", False),
    ("weather", "External", False),
    ("fog", "External", False),
    ("power", "External", False),
    ("strike", "External", False),
    ("holiday", "External", False),
    ("statutory", "External", False),
    ("safety", "External", False),
    ("accident", "External", False),
    ("survey", "External", False),
]

# The reasons that the recommendations engine targets, with the lever to pull.
# Deliberately framed as scheduling/coordination changes: breaks are a worker
# entitlement and are never proposed for removal, only for staggering so that
# the whole fleet does not stand still at the same moment.
IDLE_LEVERS: dict[str, dict[str, str]] = {
    "Shift Change": {
        "lever": "Hot-seat changeover",
        "detail": (
            "Relieve operators at the machine instead of parking the fleet and "
            "walking crews to the change house. The outgoing operator keeps "
            "hauling until the incoming operator arrives at the equipment."
        ),
        "realistic_reduction": "40%",
    },
    "Tea/ Breakfast / Snacks": {
        "lever": "Staggered break rota",
        "detail": (
            "Keep the full break entitlement, but split the fleet into two or "
            "three break groups so loading units always have trucks presenting. "
            "No operator loses break time."
        ),
        "realistic_reduction": "35%",
    },
    "Marching": {
        "lever": "Pre-positioning between faces",
        "detail": (
            "Sequence face changes so trucks march during their existing break "
            "window or while their shovel is being relocated."
        ),
        "realistic_reduction": "30%",
    },
    "Change Operators": {
        "lever": "Hot-seat changeover",
        "detail": "Same mechanism as shift change, applied to mid-shift relief.",
        "realistic_reduction": "40%",
    },
    "Delay At Hopper": {
        "lever": "Hopper dispatch smoothing",
        "detail": (
            "Cap the number of trucks routed to the hopper per 15-minute window "
            "using the queue pattern the dashboard exposes."
        ),
        "realistic_reduction": "25%",
    },
    "Fuel Filling": {
        "lever": "Refuel during scheduled downtime",
        "detail": "Move refuelling into the daily-service window already booked.",
        "realistic_reduction": "50%",
    },
}

# Status categories in the FMS that represent lost availability, not idle.
DOWN_CATEGORIES = {"DOWN", "Down"}
DELAY_CATEGORIES = {"DELAY", "Delay", "STANDBY", "Standby"}

# --------------------------------------------------------------------------- #
# Costing
# --------------------------------------------------------------------------- #
# Defaults are editable live in the sidebar; every figure in the UI is labelled
# as an assumption unless it is derived from the FMS data itself.
DEFAULT_IDLE_COST_PER_HOUR = 5000.0  # INR, all-in owning + operating cost
DEFAULT_DIESEL_PRICE = 90.0          # INR / litre
DEFAULT_IDLE_FUEL_BURN = 8.0         # litres / hour at idle for a 100 t class dumper
DEFAULT_REVENUE_PER_TONNE = 0.0      # left at 0; production gain shown in tonnes

# --------------------------------------------------------------------------- #
# Sites
# --------------------------------------------------------------------------- #
DEFAULT_SITE = "QSE"
QSE_TOKENS = ["QSE", "SANJAY VAN", "SCC-C", "COAL _STOCK_DFP", "MD_COAL", "BELT LINE", "N2 STOCK"]
QAB_TOKENS = ["QAB"]

# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
RANDOM_STATE = 42
TEST_SIZE = 0.2

# Total idle minutes lost by one dumper in one shift: in-cycle stand-still plus
# every reason-coded delay logged against the machine. Chosen over the ratio
# form because it is what the mine actually loses and what the cost model needs.
TARGET_SHIFT = "Total_Idle_Min"
# Normalised view used for fair comparison between dumpers with different
# workloads. Reported in the dashboard, not used as the model target.
TARGET_NORMALISED = "Idle_Min_Per_Operating_Hour"
TARGET_CYCLE = "Idle_Min"

# Columns that are components of the target and must never become features,
# otherwise the model simply re-derives its own label.
#
# IMPORTANT: ``Cycles`` and ``Payload_Tonnes`` are ALSO banned even though they
# look like ordinary workload features. Verified empirically: in a fixed
# 8-hour shift, Cycle_Min = 480 - Delay_Min (approximately), so
# Cycles = Cycle_Min / Cycle_Min_Mean is a near-deterministic readout of
# Delay_Min (r=0.95). A model that uses "how many cycles were completed this
# shift" to predict "how much this shift was delayed" is not forecasting
# anything — it is reading the target off a mechanically related quantity.
# The same reasoning applies to any same-shift outcome measured on the dumper
# itself. Same-shift figures for OTHER equipment (e.g. the shovel's own delay)
# are kept because they are not mechanically tied to this dumper's target.
LEAKY_COLUMNS = [
    "Total_Idle_Min", "Cycle_Idle_Min", "Delay_Min", "Measured_Idle_Min",
    "Idle_Share", "Idle_Min_Per_Operating_Hour", "Productive_Min", "Spotting_Min",
    "Cycle_Min", "Cycle_Min_Mean", "Operating_Hours", "Queue_Min", "Stopped_Min",
    "Addressable_Delay_Min", "Idle_Hours_Reported", "Run_Hours",
    "Delay_Organisational_Min", "Delay_Operational_Min", "Delay_Mechanical_Min",
    "Delay_External_Min", "Delay_Unclassified_Min", "Delay_Events", "Idle_Events",
    "Longest_Idle_Min", "Breakdown_Hours", "Available_Hours", "Canteen_Break_Min",
    "Cycles", "Payload_Tonnes", "Tonnes_Per_Hour",
    # A shift with no cycles at all is a machine that was unavailable for the
    # whole eight hours. The flag is therefore a near-perfect proxy for a very
    # high idle total and would let the model read its own label.
    "Zero_Cycle_Shift",
]

# Percentile used to split shifts into "high idle risk" vs not, for the
# classifier. 0.67 means the worst third of shifts are the positive class.
HIGH_IDLE_PERCENTILE = 0.67

# --------------------------------------------------------------------------- #
# Branding — neo-brutalist / retro-futurist dark/lime system
# --------------------------------------------------------------------------- #
BG_DARK = "#1B1A1D"
BG_CARD = "#232226"
TEXT_LIGHT = "#F2F1ED"
TEXT_MUTED = "#858388"
BORDER_DARK = "#2A292D"
BORDER_MUTED = "#3E3D41"
GRID_DARK = "#2A292D"
LIME = "#C8E600"
LIME_LIGHT = "#D7F000"
LIME_GLOW = "rgba(200, 230, 0, 0.18)"
DANGER = "#FF4D4D"

# Legacy aliases preserved so the rest of the codebase keeps working
TATA_BLUE = LIME
TATA_BLUE_LIGHT = LIME_LIGHT
TATA_ORANGE = TEXT_LIGHT
TATA_GREY = TEXT_MUTED
OK_GREEN = LIME
WARN_RED = DANGER

REASON_CLASS_COLORS = {
    "Organisational": LIME,
    "Operational": TEXT_LIGHT,
    "Mechanical": TEXT_MUTED,
    "External": "#5F5E62",
}

SEQUENTIAL_SCALE = [[0.0, "#2A292D"], [0.5, "#5F5E62"], [1.0, LIME]]
IDLE_SCALE = [[0.0, "#2A292D"], [0.5, "#5F5E62"], [1.0, LIME]]


def ensure_dirs() -> None:
    """Create the data/model folders if they do not already exist."""
    for path in (RAW_DIR, PROCESSED_DIR, WRITABLE_PROCESSED_DIR, MODEL_DIR):
        path.mkdir(parents=True, exist_ok=True)
