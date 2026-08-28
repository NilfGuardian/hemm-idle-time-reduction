"""Shared Streamlit layer: data loading, sidebar filters, KPI cards and theming.

Every page imports from here so the filters, costing assumptions and styling stay
identical across the dashboard.
"""
from __future__ import annotations

import html
import itertools
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import config
import data_upload as upload
import data_utils as du
from utils.helpers import format_inr, format_number

PROCESSED_TABLES = (
    "cycles", "shifts", "hourly", "reasons", "equipment",
    "idle_events", "delay_events", "fuel", "tkph",
    "dumper_shift", "status_summary",
    "shovel_shift", "hauling_summary", "loading_unit_summary", "daily_production",
    "loader_profile", "operator", "payload_cycles",
    "loading_unit_time", "loading_routes",
    "status_category", "engine_hours",
)


# --------------------------------------------------------------------------- #
# Page setup and styling
# --------------------------------------------------------------------------- #
_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Rajdhani:wght@500;600;700&display=swap');

* {{
    -webkit-font-smoothing: antialiased !important;
    -moz-osx-font-smoothing: grayscale !important;
    text-rendering: optimizeLegibility !important;
}}

:root {{
    --bg: {config.BG_DARK};
    --bg-card: {config.BG_CARD};
    --text: {config.TEXT_LIGHT};
    --muted: {config.TEXT_MUTED};
    --border: {config.BORDER_DARK};
    --border-muted: {config.BORDER_MUTED};
    --accent: {config.LIME};
    --accent-light: {config.LIME_LIGHT};
    --accent-glow: {config.LIME_GLOW};
    --danger: {config.DANGER};
    --font-display: "Rajdhani", "Inter", sans-serif;
    --font-mono: "Inter", "Segoe UI", sans-serif;
}}

/* Root app shell */
.stApp, [data-testid="stAppViewContainer"], .block-container {{
    background: var(--bg) !important;
    color: var(--text) !important;
    font-family: var(--font-mono) !important;
}}

/* Subtle technical grid background */
.stApp::before {{
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    z-index: 0;
    background-image:
        linear-gradient(to right, {config.GRID_DARK} 1px, transparent 1px),
        linear-gradient(to bottom, {config.GRID_DARK} 1px, transparent 1px);
    background-size: 80px 80px;
    opacity: .35;
}}

/* Typography */
.block-container {{ padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1500px; position: relative; z-index: 1; }}
h1, h2, h3, h4, h5, h6 {{
    font-family: var(--font-display);
    text-transform: uppercase;
    letter-spacing: .03em;
    color: var(--text) !important;
    font-weight: 700;
}}
h1 {{ font-size: 3.4rem; line-height: 1.0; margin-bottom: .5rem; }}
h2 {{ font-size: 2.4rem; line-height: 1.05; margin-top: 1.4rem; }}
h3 {{ font-size: 1.7rem; line-height: 1.1; }}
p, li, span, div, label, .stMarkdown {{ color: var(--text); }}

/* Sidebar */
section[data-testid="stSidebar"] {{
    background: var(--bg-card) !important;
    border-right: 1px solid var(--border) !important;
}}
section[data-testid="stSidebar"] .stMarkdown, section[data-testid="stSidebar"] label {{
    color: var(--muted) !important;
    font-family: var(--font-mono) !important;
    letter-spacing: .04em;
    text-transform: uppercase;
    font-size: .72rem !important;
    font-weight: 600 !important;
}}
section[data-testid="stSidebar"] [data-testid="stMetricValue"] {{
    color: var(--accent) !important;
    font-family: var(--font-mono) !important;
}}

/* Top navigation */
div[data-testid="stNavigation"] {{
    background: var(--bg-card) !important;
    border: 1px solid var(--border) !important;
    border-radius: 0 !important;
    padding: .5rem .7rem;
    margin-bottom: 1.4rem;
}}
div[data-testid="stNavigation"] a {{
    font-family: var(--font-mono) !important;
    font-size: .78rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: .06em;
    color: var(--muted) !important;
    border-radius: 0 !important;
    padding: .55rem .8rem !important;
    transition: color .12s ease, background .12s ease;
}}
div[data-testid="stNavigation"] a:hover {{
    background: var(--accent-glow);
    color: var(--accent) !important;
}}
div[data-testid="stNavigation"] a[aria-current="page"] {{
    background: var(--accent) !important;
    color: var(--bg) !important;
}}
div[data-testid="stNavigation"] a[aria-current="page"] span {{ color: var(--bg) !important; }}

/* Buttons */
button[kind="primary"], button[kind="secondary"], .stButton > button {{
    font-family: var(--font-mono) !important;
    text-transform: uppercase;
    letter-spacing: .08em;
    font-size: .78rem !important;
    font-weight: 600;
    border: 1px solid var(--accent) !important;
    background: transparent !important;
    color: var(--accent) !important;
    border-radius: 0 !important;
    padding: .55rem 1.1rem;
    transition: all .15s ease;
}}
button[kind="primary"]:hover, button[kind="secondary"]:hover, .stButton > button:hover {{
    background: var(--accent) !important;
    color: var(--bg) !important;
    box-shadow: 0 0 18px var(--accent-glow);
}}

/* Multi-select pills: neon lime background with black text */
div[data-testid="stMultiSelect"] [data-testid="stMultiSelectTag"],
div[data-testid="stMultiSelect"] [data-testid="stMultiSelectTag"] *,
div[data-testid="stMultiSelect"] [data-baseweb="tag"],
div[data-testid="stMultiSelect"] [data-baseweb="tag"] *,
div[data-testid="stMultiSelect"] > div > div > div > span,
div[data-testid="stMultiSelect"] > div > div > div > span *,
div[data-testid="stMultiSelect"] > div > div > div,
div[data-testid="stMultiSelect"] > div > div > div * {{
    color: var(--bg) !important;
    fill: var(--bg) !important;
    stroke: var(--bg) !important;
    font-family: var(--font-mono) !important;
    font-weight: 600 !important;
    -webkit-text-fill-color: var(--bg) !important;
}}
/* Placeholder remains muted so it is not mistaken for a selected tag */
div[data-testid="stMultiSelect"] [data-testid="stMultiSelectPlaceholder"],
div[data-testid="stMultiSelect"] [data-testid="stMultiSelectEmptyState"],
div[data-testid="stMultiSelect"] [data-testid="stMultiSelectPlaceholder"] *,
div[data-testid="stMultiSelect"] [data-testid="stMultiSelectEmptyState"] * {{
    color: var(--muted) !important;
    fill: var(--muted) !important;
    -webkit-text-fill-color: var(--muted) !important;
}}

/* Data / metric / table */
div[data-testid="stMetricValue"] {{
    color: var(--accent) !important;
    font-family: var(--font-mono) !important;
    font-weight: 700;
}}
div[data-testid="stMetricLabel"] {{
    font-family: var(--font-mono) !important;
    text-transform: uppercase;
    letter-spacing: .06em;
    color: var(--muted) !important;
    font-size: .72rem !important;
}}
[data-testid="stDataFrame"] table, [data-testid="stTable"] table {{
    background: var(--bg-card) !important;
    color: var(--text) !important;
    border: 1px solid var(--border) !important;
    font-family: var(--font-mono) !important;
    font-size: .84rem;
}}
[data-testid="stDataFrame"] th, [data-testid="stTable"] th {{
    background: var(--bg-card) !important;
    color: var(--accent) !important;
    text-transform: uppercase;
    letter-spacing: .04em;
    font-size: .72rem;
    border-bottom: 1px solid var(--border) !important;
}}
[data-testid="stDataFrame"] td, [data-testid="stTable"] td {{
    border-bottom: 1px solid var(--border-muted) !important;
}}

/* Cards */
.hemm-hero {{
    position: relative;
    background: var(--bg-card) !important;
    border: 1px solid var(--border);
    border-left: 4px solid var(--accent);
    color: var(--text);
    padding: 1.6rem 1.8rem;
    margin-bottom: 1.8rem;
}}
.hemm-hero::before {{
    content: "//";
    position: absolute;
    top: .9rem;
    right: 1.2rem;
    font-family: var(--font-mono);
    color: var(--muted);
    font-size: .7rem;
    letter-spacing: .1em;
}}
.hemm-hero h1 {{ color: var(--text); margin: 0 0 .35rem 0; font-family: var(--font-display); font-size: 2.2rem; }}
.hemm-hero p {{ margin: 0; color: var(--muted); font-size: .9rem; font-family: var(--font-mono); }}
.hemm-card {{
    background: var(--bg-card) !important;
    border: 1px solid var(--border);
    border-top: 3px solid var(--accent);
    border-radius: 0 !important;
    padding: 1.1rem 1.3rem;
    height: 100%;
    transition: border-color .15s ease;
}}
.hemm-card:hover {{ border-color: var(--accent); }}
.hemm-card.accent {{ border-top-color: var(--accent-light); }}
.hemm-card.good {{ border-top-color: var(--accent); }}
.hemm-card .label {{
    font-family: var(--font-mono);
    font-size: .7rem; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); font-weight: 600;
}}
.hemm-card .value {{
    font-size: 1.8rem; font-weight: 700; color: var(--accent);
    line-height: 1.15; margin: .25rem 0;
    font-family: var(--font-mono);
}}
.hemm-card.accent .value {{ color: var(--accent-light); }}
.hemm-card .help {{ font-size: .78rem; color: var(--muted); font-family: var(--font-mono); }}
.hemm-note {{
    background: var(--bg-card) !important;
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    padding: .85rem 1.1rem;
    font-size: .86rem;
    color: var(--muted);
    margin: .6rem 0 1.1rem 0;
    font-family: var(--font-mono);
}}
</style>
"""

_CARD_INDEX = itertools.count(1)
_NUM_RE = re.compile(r"([\d,]+(?:\.\d+)?%?)")


def _load_static_css() -> str:
    """Load the shared animation / 3D theme stylesheet."""
    path = config.ROOT / "static" / "animations.css"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _three_html() -> str:
    """Return raw <script> tags for st.html.

    st.html with unsafe_allow_javascript=True executes script tags directly in
    the main Streamlit document, so we just inline cursor.js, interactions.js
    and three_setup.js with no surrounding <html>/<body> wrapper.
    """
    files = ["cursor.js", "interactions.js", "three_setup.js"]
    parts = []
    for name in files:
        path = config.ROOT / "static" / name
        if not path.exists():
            return ""
        parts.append(f"<script>{path.read_text(encoding='utf-8')}</script>")
    return "\n".join(parts)


def apply_theme() -> None:
    """Inject the shared Tata-themed stylesheet plus scroll/3D CSS."""
    st.markdown(_CSS, unsafe_allow_html=True)
    extra_css = _load_static_css()
    if extra_css:
        st.markdown(f"<style>{extra_css}</style>", unsafe_allow_html=True)


def configure_app() -> None:
    """Set the page config once, in the router (``app.py``) only.

    ``st.set_page_config`` may run exactly once per app and must be the first
    Streamlit command executed, so individual pages must call ``apply_theme``
    instead of this function.
    """
    st.set_page_config(
        page_title=config.PROJECT_NAME,
        page_icon=":material/local_shipping:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    # Mount-points for the Three.js scene and cursor spotlight.
    # The JS below is executed in the main document by st.html.
    st.markdown('<div id="hemm-3d-bg"></div><div id="hemm-spotlight"></div>', unsafe_allow_html=True)
    apply_theme()
    three_html = _three_html()
    if three_html:
        components.html(three_html, height=0, width=0)


def hero(title: str, subtitle: str) -> None:
    """Render the page banner."""
    st.markdown(
        f'<div class="hemm-hero"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def kpi_card(label: str, value: str, help_text: str = "", tone: str = "") -> None:
    """Render a single KPI card. ``tone`` may be empty, ``accent`` or ``good``.

    Numbers are wrapped in a live counter span that the JS scroll observer will
    animate from 0 to the final value once the card enters the viewport.
    """
    stagger = next(_CARD_INDEX) % 8 + 1
    match = _NUM_RE.search(value)
    if match:
        token = match.group(1)
        token_body = token[:-1] if token.endswith("%") else token
        sign = "%" if token.endswith("%") else ""
        try:
            target = float(token_body.replace(",", ""))
            decimals = len(token_body.split(".")[1]) if "." in token_body else 0
            value_html = (
                f'<span class="counter" data-target="{target}" '
                f'data-decimals="{decimals}" data-prefix="{html.escape(value[:match.start()])}" '
                f'data-sign="{html.escape(sign)}" data-suffix="{html.escape(value[match.end():])}">'
                f'{html.escape(value[:match.start()])}0{html.escape(sign)}{html.escape(value[match.end():])}</span>'
            )
        except ValueError:
            value_html = html.escape(value)
    else:
        value_html = html.escape(value)

    st.markdown(
        f'<div class="hemm-card reveal {tone}" data-stagger="{stagger}">'
        f'<div class="label">{html.escape(label)}</div>'
        f'<div class="value">{value_html}</div>'
        f'<div class="help">{html.escape(help_text)}</div></div>',
        unsafe_allow_html=True,
    )


def note(text: str) -> None:
    """Render an inline methodology note."""
    st.markdown(f'<div class="hemm-note">{text}</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_processed() -> dict[str, pd.DataFrame]:
    """Load every processed parquet table, returning empty frames when missing."""
    tables: dict[str, pd.DataFrame] = {}
    for name in PROCESSED_TABLES:
        path = config.PROCESSED_DIR / f"{name}.parquet"
        try:
            tables[name] = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        except Exception as exc:
            st.warning(f"Could not read {name}.parquet: {exc}")
            tables[name] = pd.DataFrame()
    return tables


@st.cache_data(show_spinner=False)
def load_provenance() -> dict:
    """Load the ingest provenance record."""
    path = config.PROCESSED_DIR / "provenance.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@st.cache_resource(show_spinner=False)
def load_bundle() -> du.ModelBundle | None:
    """Load the trained model bundle once per session."""
    return du.load_model()


@st.cache_data(show_spinner=False)
def scored_shifts(_bundle_id: str) -> pd.DataFrame:
    """Return the shift master with model predictions and residuals attached.

    ``_bundle_id`` only exists to invalidate the cache when the model is
    retrained; the bundle itself is not hashable.
    """
    tables = load_processed()
    shifts = tables.get("shifts", pd.DataFrame())
    bundle = load_bundle()
    if shifts.empty or bundle is None:
        return shifts
    try:
        return du.predict_idle_time(bundle, shifts)
    except Exception as exc:
        st.warning(f"Scoring failed, showing unscored data: {exc}")
        return shifts


def data_is_ready() -> bool:
    """True when the processed tables exist; otherwise show setup instructions."""
    shifts = config.PROCESSED_DIR / "shifts.parquet"
    if shifts.exists():
        return True
    st.error("No processed data found.")
    st.markdown(
        """
        ### One-time setup
        Run the ingest script from the project folder:

        ```
        python scripts/ingest.py
        ```

        It reads the FMS CSV exports, builds the idle-time tables into
        `data/processed/` and trains the base model. It takes about 15 seconds.
        """
    )
    return False


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
@dataclass
class Filters:
    """The active sidebar selection, shared by every page."""

    start: pd.Timestamp
    end: pd.Timestamp
    sites: list[str]
    shifts: list[int]
    equipment: list[str]
    idle_cost_per_hour: float
    diesel_price: float
    idle_fuel_burn: float

    def apply(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Filter any table that carries the standard columns."""
        if frame is None or frame.empty:
            return frame
        out = frame
        if "Shift_Date" in out.columns:
            out = out[(out["Shift_Date"] >= self.start) & (out["Shift_Date"] <= self.end)]
        if self.sites and "Site" in out.columns:
            out = out[out["Site"].isin(self.sites)]
        if self.shifts and "Shift" in out.columns:
            out = out[out["Shift"].isin(self.shifts)]
        if self.equipment and "Equipment_ID" in out.columns:
            out = out[out["Equipment_ID"].isin(self.equipment)]
        return out


def sidebar_filters(shifts: pd.DataFrame) -> Filters:
    """Render the shared sidebar and return the active selection."""
    st.sidebar.markdown(f":material/local_shipping: **{config.PROJECT_NAME}**")
    st.sidebar.caption(config.PROJECT_SUBTITLE)
    st.sidebar.divider()

    if shifts.empty:
        today = pd.Timestamp.today().normalize()
        return Filters(today, today, [], [], [], config.DEFAULT_IDLE_COST_PER_HOUR,
                       config.DEFAULT_DIESEL_PRICE, config.DEFAULT_IDLE_FUEL_BURN)

    min_date = shifts["Shift_Date"].min().date()
    max_date = shifts["Shift_Date"].max().date()

    st.sidebar.markdown(":material/date_range: **Period**")
    chosen = st.sidebar.date_input(
        "Shift date range", value=(min_date, max_date),
        min_value=min_date, max_value=max_date, label_visibility="collapsed",
    )
    if isinstance(chosen, tuple) and len(chosen) == 2:
        start, end = pd.Timestamp(chosen[0]), pd.Timestamp(chosen[1])
    else:
        start, end = pd.Timestamp(min_date), pd.Timestamp(max_date)

    available_sites = sorted(s for s in shifts["Site"].dropna().unique())
    default_sites = [config.DEFAULT_SITE] if config.DEFAULT_SITE in available_sites else available_sites
    sites = st.sidebar.multiselect("Mining section", available_sites, default=default_sites)

    shift_options = sorted(int(s) for s in shifts["Shift"].dropna().unique())
    selected_shifts = st.sidebar.multiselect(
        "Shift", shift_options, default=shift_options,
        format_func=lambda s: config.SHIFT_LABELS.get(s, f"Shift {s}"),
    )

    scope = shifts[shifts["Site"].isin(sites)] if sites else shifts
    equipment_options = sorted(scope["Equipment_ID"].dropna().unique())
    equipment = st.sidebar.multiselect(
        "Dumper (blank = all)", equipment_options, default=[],
        help="Leave empty to include the whole fleet.",
    )

    st.sidebar.divider()
    st.sidebar.markdown(":material/payments: **Costing assumptions**")
    idle_cost = st.sidebar.number_input(
        "Idle cost (₹ per hour)", min_value=0.0, max_value=50000.0,
        value=config.DEFAULT_IDLE_COST_PER_HOUR, step=250.0,
        help="All-in owning and operating cost of an idle dumper. An assumption, "
             "not an FMS figure. Change it to see the sensitivity.",
    )
    diesel = st.sidebar.number_input(
        "Diesel (₹ per litre)", min_value=0.0, max_value=200.0,
        value=config.DEFAULT_DIESEL_PRICE, step=1.0,
    )
    burn = st.sidebar.number_input(
        "Idle fuel burn (litres per hour)", min_value=0.0, max_value=40.0,
        value=config.DEFAULT_IDLE_FUEL_BURN, step=0.5,
        help="The FMS idle log records every idle event with the engine ON, so "
             "idle time burns diesel.",
    )

    return Filters(start, end, sites, selected_shifts, equipment, idle_cost, diesel, burn)


def sidebar_model_panel() -> None:
    """Show model status and offer a retrain, at the bottom of the sidebar."""
    st.sidebar.divider()
    st.sidebar.markdown(":material/model_training: **Idle-time model**")
    bundle = load_bundle()
    if bundle is None:
        st.sidebar.warning("No model trained yet.")
    else:
        if bundle.risk_metrics:
            st.sidebar.metric("Risk-flag AUC", f"{bundle.risk_metrics['auc']:.3f}")
            st.sidebar.caption(
                f"{bundle.risk_model_name} classifier · flags the worst third of shifts"
            )
        st.sidebar.caption(
            f"Minutes regressor: {bundle.model_name}, R² {bundle.metrics['r2']:.2f}, "
            f"MAE {bundle.metrics['mae']:.0f} min · trained {bundle.trained_at:%d %b %H:%M}"
        )

    if st.sidebar.button("Retrain model", use_container_width=True):
        tables = load_processed()
        shifts = tables.get("shifts", pd.DataFrame())
        if shifts.empty:
            st.sidebar.error("No shift data to train on.")
            return
        with st.spinner("Retraining on the current shift master..."):
            try:
                new_bundle = du.retrain_model(shifts)
            except Exception as exc:
                st.sidebar.error(f"Training failed: {exc}")
                return
        st.cache_resource.clear()
        st.cache_data.clear()
        st.toast(
            f"Retrained: {new_bundle.model_name}, R² {new_bundle.metrics['r2']:.3f}",
        )
        st.rerun()

    _sidebar_upload_panel()


# --------------------------------------------------------------------------- #
# Upload & retrain panel
# --------------------------------------------------------------------------- #
def _upload_key(uploaded_files: list[Any]) -> tuple[tuple[str, int], ...] | None:
    if not uploaded_files:
        return None
    return tuple((f.name, len(f.getvalue())) for f in uploaded_files)


def _render_validation(validation: upload.UploadValidation) -> None:
    """Show per-file validation results in the sidebar."""
    for fv in validation.files:
        if fv.ok:
            st.sidebar.success(f"{fv.name} — {fv.message}")
        else:
            st.sidebar.error(f"{fv.name} — {fv.message}")
            for category, missing in fv.missing_categories.items():
                st.sidebar.caption(
                    f"  Missing {category}: {', '.join(missing)}"
                )
            for failure in fv.relationship_failures:
                st.sidebar.caption(f"  Relationship: {failure}")
            if fv.report_key in upload.RELATIONSHIP_DESCRIPTIONS:
                st.sidebar.caption(
                    f"  Expected relationship: {upload.RELATIONSHIP_DESCRIPTIONS[fv.report_key]}"
                )


def _sidebar_upload_panel() -> None:
    """File uploader, validation and append+retrain workflow in the sidebar."""
    st.sidebar.divider()
    st.sidebar.markdown(":material/upload: **Upload new FMS reports**")
    st.sidebar.caption(
        "Add new CSV exports to grow the dataset. The model will retrain on the "
        "combined old + new data."
    )

    uploaded = st.sidebar.file_uploader(
        "FMS CSV reports",
        type=["csv"],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key="fms_upload",
    )

    if not uploaded:
        st.sidebar.caption(
            "Hard requirements: a `Dumper Cycle Time` report and a "
            "`Delays and Downs` report. See `docs/report_catalogue.md` for details."
        )
        return

    # Re-validate only when the uploaded set changes.
    current_key = _upload_key(uploaded)
    if st.session_state.get("upload_validation_key") != current_key:
        st.session_state["upload_validation"] = upload.validate_uploaded_files(uploaded)
        st.session_state["upload_validation_key"] = current_key

    validation: upload.UploadValidation = st.session_state["upload_validation"]
    _render_validation(validation)

    if validation.ok:
        n_rows = sum(len(v) for v in validation.raw_tables.values())
        st.sidebar.success(
            f"Batch valid: {n_rows:,} new rows across "
            f"{len(validation.raw_tables)} report(s)."
        )
        if st.sidebar.button(
            "Add to dataset & retrain",
            use_container_width=True,
        ):
            with st.spinner("Merging new data and rebuilding tables..."):
                try:
                    tables = upload.append_and_rebuild(validation.raw_tables)
                    new_bundle = upload.retrain_and_save(tables)
                except Exception as exc:
                    st.sidebar.error(f"Pipeline failed: {exc}")
                    return
            st.cache_resource.clear()
            st.cache_data.clear()
            st.toast(
                f"Retrained on {len(tables['shifts']):,} shifts: "
                f"{new_bundle.model_name}, R² {new_bundle.metrics['r2']:.3f}",
            )
            st.rerun()
    else:
        if "cycles" in validation.missing_hard:
            st.sidebar.warning(
                "Missing required report: `Dumper Cycle Time` (cycles)."
            )
        if "delay_events" in validation.missing_hard:
            st.sidebar.warning(
                "Missing required report: `Delays and Downs` (delay_events)."
            )
        st.sidebar.caption(
            "Upload at least one cycles and one delay_events CSV. "
            "See `docs/report_catalogue.md` for the accepted reports, columns and relationships."
        )


# --------------------------------------------------------------------------- #
# Derived idle metrics
# --------------------------------------------------------------------------- #
@dataclass
class IdleSummary:
    """Headline idle numbers for the current filter selection."""

    dumper_shifts: int
    dumpers: int
    days: int
    cycles: int
    cycle_idle_hours: float
    delay_hours: float
    total_idle_hours: float
    measured_idle_hours: float
    idle_share_of_cycle: float
    idle_hours_per_dumper_shift: float
    addressable_hours: float
    tonnes: float
    tonnes_per_operating_hour: float
    cost: float
    addressable_cost: float
    fuel_litres_idle: float
    fuel_cost_idle: float


def summarise_idle(shifts: pd.DataFrame, filters: Filters) -> IdleSummary:
    """Compute the headline idle figures used across the dashboard."""
    if shifts.empty:
        return IdleSummary(0, 0, 0, 0, *([0.0] * 12))

    cycle_idle = float(shifts["Cycle_Idle_Min"].sum())
    delay = float(shifts.get("Delay_Min", pd.Series(dtype=float)).sum())
    total = cycle_idle + delay
    cycle_min = float(shifts["Cycle_Min"].sum())
    addressable = float(shifts.get("Addressable_Delay_Min", pd.Series(dtype=float)).sum())
    tonnes = float(shifts["Payload_Tonnes"].sum())
    operating_hours = cycle_min / 60.0

    total_hours = total / 60.0
    fuel_litres = total_hours * filters.idle_fuel_burn

    return IdleSummary(
        dumper_shifts=len(shifts),
        dumpers=int(shifts["Equipment_ID"].nunique()),
        days=int(shifts["Shift_Date"].nunique()),
        cycles=int(shifts["Cycles"].sum()),
        cycle_idle_hours=cycle_idle / 60.0,
        delay_hours=delay / 60.0,
        total_idle_hours=total_hours,
        measured_idle_hours=float(
            shifts.get("Measured_Idle_Min", pd.Series(dtype=float)).sum()
        ) / 60.0,
        idle_share_of_cycle=(cycle_idle / cycle_min * 100) if cycle_min else 0.0,
        idle_hours_per_dumper_shift=(total / len(shifts) / 60.0) if len(shifts) else 0.0,
        addressable_hours=addressable / 60.0,
        tonnes=tonnes,
        tonnes_per_operating_hour=(tonnes / operating_hours) if operating_hours else 0.0,
        cost=total_hours * filters.idle_cost_per_hour,
        addressable_cost=(addressable / 60.0) * filters.idle_cost_per_hour,
        fuel_litres_idle=fuel_litres,
        fuel_cost_idle=fuel_litres * filters.diesel_price,
    )


def headline_kpis(summary: IdleSummary, filters: Filters) -> None:
    """Render the five headline idle KPI cards."""
    columns = st.columns(5)
    with columns[0]:
        kpi_card(
            "Total idle time",
            f"{format_number(summary.total_idle_hours)} h",
            f"{summary.idle_hours_per_dumper_shift:.1f} h per dumper-shift",
            tone="accent",
        )
    with columns[1]:
        kpi_card(
            "Idle inside the haul cycle",
            f"{summary.idle_share_of_cycle:.1f}%",
            f"{format_number(summary.cycle_idle_hours)} h standing still while on a trip",
        )
    with columns[2]:
        kpi_card(
            "Cost of idle time",
            format_inr(summary.cost),
            f"at ₹{filters.idle_cost_per_hour:,.0f}/h (assumption)",
            tone="accent",
        )
    with columns[3]:
        kpi_card(
            "Addressable by scheduling",
            f"{format_number(summary.addressable_hours)} h",
            format_inr(summary.addressable_cost) + " of the total",
            tone="good",
        )
    with columns[4]:
        kpi_card(
            "Diesel burnt idling",
            f"{format_number(summary.fuel_litres_idle)} L",
            format_inr(summary.fuel_cost_idle) + " · engine ON in every idle event",
        )
