# OptiHaul Desktop App — Build Guide

## Prerequisites

1. **Python 3.12** (must match the target machine's architecture — x64 for 64-bit Windows)
2. **Build dependencies:**
   ```bash
   pip install -r requirements-build.txt
   pip install -r requirements.txt
   ```
3. **Windows 10/11** (PyInstaller cross-compilation is not reliable)

## One-Click Build

```bash
build\build_installer.bat
```

This runs both phases:
- **Phase 1**: `pyinstaller build/desktop.spec` → produces `dist/OptiHaul/` (onedir app bundle)
- **Phase 2**: `pyinstaller build/installer.spec` → produces `dist/OptiHaul-Setup.exe` (onefile installer)

## Manual Build

```bash
# Phase 1: Build the desktop app
pyinstaller build/desktop.spec --noconfirm

# Phase 2: Build the installer (must run after Phase 1)
pyinstaller build/installer.spec --noconfirm
```

## Output

- `dist/OptiHaul/` — the app directory (for testing without the installer)
- `dist/OptiHaul-Setup.exe` — the single-file installer for distribution

## Testing

### Test the app directly (without installer)
```bash
cd dist/OptiHaul
OptiHaul.exe
```
This should open a pywebview window showing the Streamlit app.

### Test the installer
```bash
dist/OptiHaul-Setup.exe
```
The retro-terminal installer UI should appear. Click [ Install ] to extract the app to `%LOCALAPPDATA%\OptiHaul`.

## Publishing to GitHub Releases

```bash
gh release create v1.0.0 dist/OptiHaul-Setup.exe --title "OptiHaul v1.0.0" --notes "Desktop standalone installer"
```

The web app's download link points to:
`https://github.com/NilfGuardian/hemm-idle-time-reduction/releases/latest/download/OptiHaul-Setup.exe`

## Notes

- **Three.js CDN**: The 3D background loads Three.js from a CDN. If the client has no internet, the CSS isometric grid fallback is used. The app works fully without internet.
- **Antivirus**: Unsigned .exe files may trigger antivirus warnings. Instruct the client to allow the file if their AV flags it.
- **Installer startup**: The onefile installer takes 5-10 seconds to extract before showing the UI. This is normal for PyInstaller onefile mode.
- **Rebuilding after code changes**: Just re-run `build_installer.bat`. Both phases will rebuild from scratch.
