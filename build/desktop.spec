# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building the OptiHaul desktop app (Phase 1).

Produces dist/OptiHaul/OptiHaul.exe — a onedir bundle containing the
Python interpreter, all dependencies, app code, and pre-processed data.

Build:  pyinstaller build/desktop.spec --noconfirm
"""
import glob
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), '..'))

# --- Data files to bundle ---
datas = [
    # Top-level Python files
    (os.path.join(ROOT, 'app.py'), '.'),
    (os.path.join(ROOT, 'config.py'), '.'),
    (os.path.join(ROOT, 'data_utils.py'), '.'),
    (os.path.join(ROOT, 'data_upload.py'), '.'),
    (os.path.join(ROOT, 'column_mapping.json'), '.'),
    (os.path.join(ROOT, 'desktop_app.py'), '.'),

    # .streamlit config
    (os.path.join(ROOT, '.streamlit', 'config.toml'), '.streamlit'),
]

# app_pages/*.py
app_pages = glob.glob(os.path.join(ROOT, 'app_pages', '*.py'))
for f in app_pages:
    datas.append((f, 'app_pages'))

# utils/*.py
utils_files = glob.glob(os.path.join(ROOT, 'utils', '*.py'))
for f in utils_files:
    datas.append((f, 'utils'))

# static files
static_files = glob.glob(os.path.join(ROOT, 'static', '*'))
for f in static_files:
    if os.path.isfile(f):
        datas.append((f, 'static'))

# Pre-processed data (parquet + provenance)
processed_files = glob.glob(os.path.join(ROOT, 'data', 'processed', '*'))
for f in processed_files:
    if os.path.isfile(f):
        datas.append((f, 'data/processed'))

# Model file
model_path = os.path.join(ROOT, 'models', 'base_model.pkl')
if os.path.exists(model_path):
    datas.append((model_path, 'models'))

# --- Collect data files from tricky packages ---
datas += collect_data_files('streamlit')
datas += collect_data_files('plotly')
datas += collect_data_files('xhtml2pdf')

# --- Hidden imports ---
hiddenimports = [
    'streamlit.web.bootstrap',
    'streamlit.runtime.scriptrunner',
    'pyarrow',
    'pyarrow.parquet',
    'sklearn',
    'sklearn.ensemble',
    'sklearn.linear_model',
    'sklearn.metrics',
    'joblib',
    'plotly',
    'xhtml2pdf',
    'xhtml2pdf.default',
    'xhtml2pdf.document',
    'reportlab',
    'PIL',
    'PIL._tkinter_finder',
    'webview',
    'webview.platforms.winforms',
]

# Collect all submodules for packages with dynamic imports
hiddenimports += collect_submodules('streamlit')
hiddenimports += collect_submodules('xhtml2pdf')
hiddenimports += collect_submodules('webview')

a = Analysis(
    [os.path.join(ROOT, 'desktop_app.py')],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['scripts', 'tests', '__pycache__'],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OptiHaul',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OptiHaul',
)
