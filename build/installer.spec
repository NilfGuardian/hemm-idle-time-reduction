# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building the OptiHaul installer (Phase 2).

Produces dist/OptiHaul-Setup.exe — a onefile bundle containing the
installer logic, the installer UI HTML, and the entire Phase 1 app
directory as payload.

Build:  pyinstaller build/installer.spec --noconfirm
"""
import os
import glob
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

ROOT = os.path.abspath(os.path.join(os.path.dirname(SPEC), '..'))
DIST_OPTIHAUL = os.path.join(ROOT, 'dist', 'OptiHaul')

# --- Data files ---
datas = [
    # Installer UI
    (os.path.join(ROOT, 'installer', 'installer_ui.html'), 'installer'),
]

# Bundle the entire Phase 1 output (dist/OptiHaul/) as payload
if os.path.isdir(DIST_OPTIHAUL):
    for item in os.listdir(DIST_OPTIHAUL):
        src = os.path.join(DIST_OPTIHAUL, item)
        if os.path.isdir(src):
            # Recursively add directory contents
            for dirpath, dirnames, filenames in os.walk(src):
                for filename in filenames:
                    src_file = os.path.join(dirpath, filename)
                    rel_dir = os.path.relpath(dirpath, DIST_OPTIHAUL)
                    dest_dir = os.path.join('installer', 'payload', rel_dir)
                    datas.append((src_file, dest_dir))
        elif os.path.isfile(src):
            datas.append((src, os.path.join('installer', 'payload')))
else:
    print("WARNING: dist/OptiHaul/ not found. Run Phase 1 (desktop.spec) first.")

# --- Hidden imports ---
hiddenimports = [
    'pywebview',
    'pywebview.platforms.winforms',
    'winreg',
]

hiddenimports += collect_submodules('pywebview')

a = Analysis(
    [os.path.join(ROOT, 'installer', 'setup_installer.py')],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['streamlit', 'sklearn', 'plotly', 'pyarrow', 'pandas', 'numpy'],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='OptiHaul-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=None,
    disable_windowed_traceback=False,
)
