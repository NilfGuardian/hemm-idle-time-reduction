"""OptiHaul desktop launcher.

Starts the Streamlit server in-process via ``streamlit.web.bootstrap`` and
opens a native desktop window via ``pywebview``.  When the window closes,
the server is shut down and the process exits.

In PyInstaller frozen mode, ``config.BUNDLE_ROOT`` points to the bundled
data directory and ``config.ROOT`` points to the exe directory (writable).
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import config


def _find_free_port(start: int = 8501, end: int = 8511) -> int:
    """Return the first free port in the given range."""
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    """Poll until the Streamlit server responds, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            try:
                s.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.3)
    return False


def _start_streamlit(app_path: str, port: int) -> threading.Thread:
    """Start the Streamlit server in a background thread using the bootstrap API."""

    def _run() -> None:
        from streamlit.web import bootstrap

        sys.argv = [
            "streamlit",
            "run",
            app_path,
            "--server.port",
            str(port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
            "--global.developmentMode",
            "false",
        ]
        bootstrap.run(app_path, "", [], flag_options={})

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def main() -> None:
    app_path = str(config.BUNDLE_ROOT / "app.py")
    if not Path(app_path).exists():
        app_path = str(config.ROOT / "app.py")

    port = _find_free_port()
    print(f"Starting OptiHaul on port {port}...")

    _start_streamlit(app_path, port)

    if not _wait_for_server(port):
        print("ERROR: Streamlit server did not start in time.")
        sys.exit(1)

    url = f"http://localhost:{port}"
    print(f"Server ready at {url}")

    try:
        import webview

        window = webview.create_window(
            title="OptiHaul — Idle Time Reduction",
            url=url,
            width=1000,
            height=700,
            min_size=(800, 600),
            text_select=True,
        )
        webview.start()
        # Window closed — exit
        print("Window closed, shutting down...")
        os._exit(0)
    except Exception as exc:
        print(f"pywebview failed ({exc}), opening in default browser...")
        webbrowser.open(url)
        # Keep the process alive so the server stays up
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            os._exit(0)


if __name__ == "__main__":
    main()
