"""Headless render check for every Streamlit page and the upload module.

Uses Streamlit's own AppTest harness so a broken page is caught here instead of
during a live demo:

    python scripts/smoke_test.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = [ROOT / "app.py"] + sorted((ROOT / "app_pages").glob("*.py"))


def _mock_uploaded_file(name: str, path: Path) -> object:
    """Minimal UploadedFile stand-in for data_upload tests."""

    class _Uploaded:
        def __init__(self, name: str, data: bytes):
            self.name = name
            self._data = data

        def getvalue(self) -> bytes:
            return self._data

    return _Uploaded(name, path.read_bytes())


def check(path: Path, timeout: int = 180) -> bool:
    """Run one page and report any exception it raises."""
    app = AppTest.from_file(str(path), default_timeout=timeout)
    try:
        app.run()
    except Exception as exc:
        print(f"  FAIL  {path.name}: {type(exc).__name__}: {exc}")
        return False

    if app.exception:
        for item in app.exception:
            print(f"  FAIL  {path.name}: {item.value}")
        return False

    widgets = len(app.button) + len(app.selectbox) + len(app.multiselect) + len(app.slider)
    print(f"  ok    {path.name}  ({len(app.markdown)} markdown, {widgets} widgets)")
    return True


def check_upload() -> bool:
    """Verify the upload validator accepts the known FMS reports."""
    import data_upload as upload
    import data_utils as du

    source = Path(os.environ.get("HEMM_DATA_DIR", str(ROOT / "data" / "raw")))
    if not source.exists():
        print("  skip  upload test: source folder not found")
        return True

    files = du.discover_files([source])
    candidates = []
    for key in ("cycles", "delay_events"):
        if files.get(key):
            candidates.append(_mock_uploaded_file(files[key][0].name, files[key][0]))
    if len(candidates) < 2:
        print("  skip  upload test: cycles / delay_events not found")
        return True

    try:
        val = upload.validate_uploaded_files(candidates)
    except Exception as exc:
        print(f"  FAIL  upload validation: {exc}")
        return False

    if not val.ok:
        for fv in val.files:
            print(f"  FAIL  {fv.name}: {fv.message}")
            for category, missing in fv.missing_categories.items():
                print(f"        Missing {category}: {missing}")
        return False

    n_rows = sum(len(v) for v in val.raw_tables.values())
    print(f"  ok    upload validation  ({n_rows:,} rows accepted)")
    return True


def main() -> int:
    """Run every page and return a non-zero exit code on any failure."""
    print(f"Rendering {len(PAGES)} pages...")
    results = [check(path) for path in PAGES]
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} pages rendered cleanly.")

    print("\nUpload module...")
    if not check_upload():
        failed += 1

    print(f"\n{len(results) + 1 - failed}/{len(results) + 1} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
