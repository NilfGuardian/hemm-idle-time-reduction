@echo off
REM ============================================================
REM  OptiHaul Desktop App — One-Click Build Script
REM  Produces dist/OptiHaul-Setup.exe
REM ============================================================

setlocal
cd /d "%~dp0\.."

echo.
echo  ============================================
echo   OptiHaul Build — Phase 1: Desktop App
echo  ============================================
echo.

REM Install build dependencies
pip install pywebview pyinstaller --quiet

REM Phase 1: Build the desktop app (onedir)
pyinstaller build\desktop.spec --noconfirm
if errorlevel 1 (
    echo.
    echo  ERROR: Phase 1 build failed.
    exit /b 1
)

echo.
echo  ============================================
echo   OptiHaul Build — Phase 2: Installer
echo  ============================================
echo.

REM Phase 2: Build the installer (onefile, bundles Phase 1 output)
pyinstaller build\installer.spec --noconfirm
if errorlevel 1 (
    echo.
    echo  ERROR: Phase 2 build failed.
    exit /b 1
)

echo.
echo  ============================================
echo   Build complete!
echo   Output: dist\OptiHaul-Setup.exe
echo  ============================================
echo.

endlocal
