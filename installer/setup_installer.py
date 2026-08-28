"""OptiHaul themed installer.

Bundled as ``OptiHaul-Setup.exe`` via PyInstaller onefile mode.  Opens a
pywebview window with a retro-terminal industrial UI, extracts the bundled
app payload to ``%LOCALAPPDATA%\\OptiHaul``, creates desktop/Start Menu
shortcuts, and registers an uninstaller entry.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

import webview


class InstallerAPI:
    """Python API exposed to the installer HTML/JS via ``window.pywebview.api``."""

    def __init__(self) -> None:
        self._install_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OptiHaul"

    # ------------------------------------------------------------------ #
    # Public methods called from JS
    # ------------------------------------------------------------------ #

    def get_install_dir(self) -> str:
        """Return the target installation directory."""
        return str(self._install_dir)

    def get_payload_size(self) -> int:
        """Return the total size of the bundled payload in bytes."""
        payload = self._payload_dir()
        if not payload.exists():
            return 0
        return sum(f.stat().st_size for f in payload.rglob("*") if f.is_file())

    def install(self) -> dict:
        """Extract the bundled app to the install directory.

        Returns a dict with keys: ``ok`` (bool), ``path`` (str), ``error`` (str).
        """
        try:
            payload = self._payload_dir()
            if not payload.exists():
                return {"ok": False, "path": "", "error": "Payload directory not found."}

            self._install_dir.mkdir(parents=True, exist_ok=True)

            # Copy everything from payload/ to the install directory
            for item in payload.iterdir():
                dest = self._install_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)

            return {"ok": True, "path": str(self._install_dir), "error": ""}
        except Exception as exc:
            return {"ok": False, "path": "", "error": str(exc)}

    def create_shortcuts(self) -> dict:
        """Create desktop and Start Menu shortcuts + uninstaller registry entry."""
        try:
            exe_path = self._install_dir / "OptiHaul.exe"
            if not exe_path.exists():
                return {"ok": False, "error": "OptiHaul.exe not found in install directory."}

            self._create_shortcut(
                target=str(exe_path),
                shortcut_path=str(Path.home() / "Desktop" / "OptiHaul.lnk"),
                description="OptiHaul — Idle Time Reduction in HEMM",
            )

            start_menu_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "OptiHaul"
            start_menu_dir.mkdir(parents=True, exist_ok=True)
            self._create_shortcut(
                target=str(exe_path),
                shortcut_path=str(start_menu_dir / "OptiHaul.lnk"),
                description="OptiHaul — Idle Time Reduction in HEMM",
            )

            self._register_uninstaller(exe_path)

            return {"ok": True, "error": ""}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def launch_app(self) -> dict:
        """Launch OptiHaul.exe and return immediately."""
        try:
            exe_path = self._install_dir / "OptiHaul.exe"
            if not exe_path.exists():
                return {"ok": False, "error": "OptiHaul.exe not found."}
            subprocess.Popen([str(exe_path)], cwd=str(self._install_dir))
            return {"ok": True, "error": ""}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def close_window(self) -> None:
        """Close the installer window."""
        for win in webview.windows:
            win.destroy()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _payload_dir(self) -> Path:
        """Return the directory containing the bundled app payload."""
        if getattr(sys, "frozen", False):
            return Path(sys._MEIPASS) / "installer" / "payload"
        return Path(__file__).resolve().parent.parent / "dist" / "OptiHaul"

    def _ui_path(self) -> Path:
        """Return the path to the installer HTML."""
        if getattr(sys, "frozen", False):
            return Path(sys._MEIPASS) / "installer" / "installer_ui.html"
        return Path(__file__).resolve().parent / "installer_ui.html"

    def _create_shortcut(self, target: str, shortcut_path: str, description: str = "") -> None:
        """Create a Windows .lnk shortcut via PowerShell COM."""
        ps_script = (
            f'$ws = New-Object -ComObject WScript.Shell; '
            f'$sc = $ws.CreateShortcut(\'{shortcut_path}\'); '
            f'$sc.TargetPath = \'{target}\'; '
            f'$sc.WorkingDirectory = \'{Path(target).parent}\'; '
            f'$sc.Description = \'{description}\'; '
            f'$sc.Save()'
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            check=True,
            capture_output=True,
            text=True,
        )

    def _register_uninstaller(self, exe_path: Path) -> None:
        """Register the app in Add or Remove Programs."""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\OptiHaul"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "OptiHaul — Idle Time Reduction in HEMM")
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, "1.0.0")
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "Tata Steel West Bokaro")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(self._install_dir))
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(exe_path))
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{exe_path}" --uninstall')
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def main() -> None:
    api = InstallerAPI()
    ui_path = api._ui_path()
    html_content = ui_path.read_text(encoding="utf-8") if ui_path.exists() else "<h1>Installer UI not found</h1>"

    window = webview.create_window(
        title="OptiHaul Setup",
        html=html_content,
        width=700,
        height=550,
        resizable=False,
        js_api=api,
    )
    webview.start()


if __name__ == "__main__":
    main()
