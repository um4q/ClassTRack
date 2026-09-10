"""
Shared pytest fixtures for the whole suite.

SAFETY: data/study_tracker.xlsx is the user's real supplied semester data,
not sample data - no test may ever load, mutate, or save it directly. Every
fixture here that needs a workbook copies it into pytest's own tmp_path
first (see `workbook_path`/`store`), and `_guard_real_workbook` below is an
autouse, session-scoped hard check that fails the whole run loudly if the
real file's bytes change for any reason.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

import pytest

# Must happen before any PySide6 import (directly here or transitively via
# app.* imports in a test module) - Qt reads this at platform-plugin load.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_WORKBOOK = REPO_ROOT / "data" / "study_tracker.xlsx"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def _guard_real_workbook():
    """Hard safety net around the ENTIRE test session: if the real supplied
    workbook's bytes change for any reason, fail loudly instead of letting
    it slide - a test bug here is worse than a missed test."""
    before = _hash(REAL_WORKBOOK)
    yield
    after = _hash(REAL_WORKBOOK)
    assert before == after, (
        "data/study_tracker.xlsx changed during the test run! No test may "
        "write to the real supplied workbook - use the `store` / "
        "`workbook_path` fixtures (private tmp copies) instead. Restore it "
        "immediately with: git checkout HEAD -- data/study_tracker.xlsx"
    )


@pytest.fixture
def workbook_path(tmp_path) -> Path:
    """A private tmp copy of the REAL supplied workbook (with actual Fall
    2026 semester data) - safe to load, edit, and save; the original is
    never touched. Use this whenever a test needs realistic data (e.g. six
    real courses, the actual grade weights, real topic counts)."""
    if not REAL_WORKBOOK.exists():
        pytest.skip(f"supplied workbook not found at {REAL_WORKBOOK}")
    dest = tmp_path / "study_tracker.xlsx"
    shutil.copy2(REAL_WORKBOOK, dest)
    return dest


@pytest.fixture
def store(qapp, workbook_path):
    """An ExcelStore loaded from a private tmp copy of the real workbook.
    Depends on pytest-qt's `qapp` fixture - a QObject subclass with Signals
    (ExcelStore) can't be constructed before a QApplication exists."""
    from app.excel_store import ExcelStore
    s = ExcelStore(workbook_path)
    s.load()
    return s


@pytest.fixture
def empty_store(qapp, tmp_path):
    """A store backed by a brand-new EMPTY workbook (exercises ExcelStore's
    own _create_empty_workbook path) - for edge-case tests that want a
    clean slate (zero courses, zero rows) rather than the real semester
    data. Never touches data/study_tracker.xlsx."""
    from app.excel_store import ExcelStore
    s = ExcelStore(tmp_path / "empty_workbook.xlsx")
    s.load()
    return s
