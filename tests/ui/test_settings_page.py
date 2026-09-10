"""Tests for app.ui.settings_page.SettingsPage - workbook path, semester
dates, theme, notifications, default study block, PDF opener command, and
the page's global action buttons (reload/save/backup, open folders,
extract coursepack zips, export .ics, import syllabus PDF, weekly summary).

Every value asserted against "real data" is read from the same ``store``
fixture the page itself reads from (a private tmp copy of the real Fall
2026 workbook: 6 active courses, 20 assessments, 81 pre-labs, semester
settings), never hardcoded independently of it - except where a concrete
fact about that workbook's known shape (course codes, counts) is asserted
directly.

Dialog safety: several handlers on this page open a QDialog/QMessageBox
that would block forever under .exec() in a headless run. Every test that
triggers one of those code paths monkeypatches the dialog class's `exec`
(or the QMessageBox/QFileDialog static method) before clicking - see the
class docstring rules in the task write-up.
"""
from __future__ import annotations

import zipfile
from datetime import date

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from app import config

from app.excel_store import ExcelStore
from app.ui import settings_page as settings_page_module
from app.ui.settings_page import SettingsPage


# --------------------------------------------------------------------------
# Autouse safety fixtures
# --------------------------------------------------------------------------
# NOTE: ExcelStore.backup_now() used to always resolve its destination from
# the global config.BACKUPS_DIR regardless of store.path, which would have
# made any save/backup test here copy the tmp workbook into the REAL
# project's data/backups/ directory - a real design smell this test suite
# surfaced. It's since been fixed to derive backups from store.path.parent
# (see ExcelStore.backups_dir), so store (a tmp copy) now backs up entirely
# within pytest's own tmp_path with no redirect needed - see
# test_backup_now_button_success_creates_backup_file_and_emits_status below,
# which asserts against store.backups_dir directly.


@pytest.fixture(autouse=True)
def _restore_app_stylesheet():
    """The QApplication instance is shared/session-scoped across the whole
    test run (pytest-qt's qapp). Theme tests below call app.setStyleSheet()
    for real - restore whatever was there before so later tests (in this
    file or others) don't see a leaked stylesheet."""
    app = QApplication.instance()
    before = app.styleSheet() if app is not None else ""
    yield
    if app is not None:
        app.setStyleSheet(before)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _button(page: SettingsPage, text: str) -> QPushButton:
    matches = [b for b in page.findChildren(QPushButton) if b.text() == text]
    assert len(matches) == 1, f"expected exactly one {text!r} button, found {len(matches)}"
    return matches[0]


def _make_zip(path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for n in names:
            zf.writestr(n, "content")


# --------------------------------------------------------------------------
# Baseline: construct with the real store, refresh(), concrete real facts
# --------------------------------------------------------------------------
def test_refresh_shows_real_workbook_path_settings_and_known_buttons(qtbot, store):
    page = SettingsPage(store)
    qtbot.addWidget(page)

    page.refresh()  # must not raise

    assert page.workbook_path_edit.text() == str(store.path)

    # Known real Fall 2026 semester settings (see tests/conftest.py docstring
    # and app.config.SETTINGS_DEFAULTS).
    assert store.setting_date("semester_start") == date(2026, 9, 1)
    ed = page._date_edits["semester_start"]
    assert (ed.date().year(), ed.date().month(), ed.date().day()) == (2026, 9, 1)

    end_ed = page._date_edits["semester_end"]
    end = store.setting_date("semester_end")
    assert (end_ed.date().year(), end_ed.date().month(), end_ed.date().day()) == (
        end.year, end.month, end.day,
    )

    assert page.theme_combo.currentText() == "Dark"
    assert store.get_setting("theme") == "dark"
    assert page.notifications_chk.isChecked() is True
    assert page.study_block_spin.value() == 50
    assert page.pdf_opener_edit.text() == ""

    # Known, concrete buttons this page advertises.
    assert _button(page, "Extract coursepack zips now").isEnabled()
    assert _button(page, "Export .ics").isEnabled()
    assert _button(page, "Show weekly summary now").isEnabled()


def test_reload_button_real_click_picks_up_externally_saved_settings(qtbot, store, workbook_path):
    # Change settings on disk via a SEPARATE store bound to the same tmp
    # workbook file - simulates another process (or a prior session) having
    # saved before this one reloads.
    other = ExcelStore(workbook_path)
    other.load()
    other.set_setting("theme", "light")
    other.set_setting("default_study_block_min", "75")
    other.save(force=True)

    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()
    assert page.theme_combo.currentText() == "Dark"  # store's in-memory copy is still stale

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Reload from Excel"), Qt.LeftButton)
    assert blocker.args == ["Reloaded from Excel."]

    assert page.theme_combo.currentText() == "Light"
    assert page.study_block_spin.value() == 75
    assert store.get_setting("theme") == "light"


# --------------------------------------------------------------------------
# Semester dates: real QDateEdit keyboard interaction
# --------------------------------------------------------------------------
def test_semester_start_date_edit_key_interaction_persists_and_emits_status(qtbot, store, workbook_path):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    ed = page._date_edits["semester_start"]
    before = ed.date()
    ed.setFocus()
    assert ed.currentSection().name == "YearSection"  # yyyy-MM-dd: year is leftmost/default

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.keyClick(ed, Qt.Key_Up)  # increments the focused (year) section by one

    new_year_date = date(before.year() + 1, before.month(), before.day())
    assert blocker.args == [f"semester_start set to {new_year_date.isoformat()}."]
    assert store.get_setting("semester_start") == new_year_date.isoformat()

    # Persisted to disk, not just the in-memory workbook.
    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    assert fresh.setting_date("semester_start") == new_year_date


# --------------------------------------------------------------------------
# Theme: real combo box key-driven selection + QApplication stylesheet
# --------------------------------------------------------------------------
def test_theme_combo_switch_to_light_applies_light_stylesheet_and_setting(qtbot, store):
    dark_qss = settings_page_module._DARK_QSS_PATH.read_text(encoding="utf-8")
    light_qss = settings_page_module._LIGHT_QSS_PATH.read_text(encoding="utf-8")
    assert dark_qss != light_qss  # sanity: the two files really do differ

    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()
    QApplication.instance().setStyleSheet(dark_qss)

    combo = page.theme_combo
    assert combo.currentText() == "Dark"
    combo.setFocus()

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.keyClick(combo, Qt.Key_Down)  # Dark (index 0) -> Light (index 1)

    assert combo.currentText() == "Light"
    assert blocker.args == ["Theme set to Light."]
    assert QApplication.instance().styleSheet() == light_qss
    assert QApplication.instance().styleSheet() != dark_qss
    assert store.get_setting("theme") == "light"


def test_theme_combo_switch_back_to_dark_restores_dark_stylesheet_and_setting(qtbot, store):
    dark_qss = settings_page_module._DARK_QSS_PATH.read_text(encoding="utf-8")
    light_qss = settings_page_module._LIGHT_QSS_PATH.read_text(encoding="utf-8")

    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    combo = page.theme_combo
    combo.setFocus()
    qtbot.keyClick(combo, Qt.Key_Down)  # Dark -> Light
    assert combo.currentText() == "Light"

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.keyClick(combo, Qt.Key_Up)  # Light -> Dark

    assert combo.currentText() == "Dark"
    assert blocker.args == ["Theme set to Dark."]
    assert QApplication.instance().styleSheet() == dark_qss
    assert QApplication.instance().styleSheet() != light_qss
    assert store.get_setting("theme") == "dark"


# --------------------------------------------------------------------------
# Notifications checkbox: real mouse click
# --------------------------------------------------------------------------
def test_notifications_checkbox_real_click_toggles_off_and_persists(qtbot, store, workbook_path):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    chk = page.notifications_chk
    assert chk.isChecked() is True  # real workbook default

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(chk, Qt.LeftButton)

    assert chk.isChecked() is False
    assert blocker.args == ["Notifications disabled."]
    assert store.setting_bool("notifications", True) is False

    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    assert fresh.setting_bool("notifications", True) is False


def test_notifications_checkbox_clicked_twice_returns_to_enabled(qtbot, store):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    chk = page.notifications_chk
    qtbot.mouseClick(chk, Qt.LeftButton)
    assert chk.isChecked() is False
    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(chk, Qt.LeftButton)
    assert chk.isChecked() is True
    assert blocker.args == ["Notifications enabled."]
    assert store.setting_bool("notifications", False) is True


# --------------------------------------------------------------------------
# Study block spin box: real keyboard step
# --------------------------------------------------------------------------
def test_study_block_spinbox_key_up_increments_value_and_persists(qtbot, store):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    spin = page.study_block_spin
    assert spin.value() == 50
    spin.setFocus()
    qtbot.keyClick(spin, Qt.Key_Up)

    assert spin.value() == 51
    assert store.setting_int("default_study_block_min", 0) == 51


# --------------------------------------------------------------------------
# PDF opener command: real typing + Enter (editingFinished)
# --------------------------------------------------------------------------
def test_pdf_opener_edit_typed_and_confirmed_persists_command(qtbot, store):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    edit = page.pdf_opener_edit
    edit.setFocus()
    qtbot.keyClicks(edit, '-page {page} "{path}"')

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.keyClick(edit, Qt.Key_Return)

    assert blocker.args == ["PDF opener command saved."]
    assert store.get_setting("pdf_opener_cmd") == '-page {page} "{path}"'


# --------------------------------------------------------------------------
# Workbook path "Change...": both dialogs monkeypatched (would block)
# --------------------------------------------------------------------------
def test_change_workbook_button_shows_info_and_never_changes_the_path(qtbot, store, monkeypatch):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    open_calls = []
    monkeypatch.setattr(
        settings_page_module.QFileDialog, "getOpenFileName",
        lambda *a, **k: (open_calls.append((a, k)) or ("", ""))
    )
    info_calls = []
    monkeypatch.setattr(
        settings_page_module.QMessageBox, "information",
        lambda *a, **k: info_calls.append(a)
    )

    qtbot.mouseClick(_button(page, "Change..."), Qt.LeftButton)

    assert len(open_calls) == 1
    assert len(info_calls) == 1
    _self, title, message = info_calls[0]
    assert title == "Workbook location"
    assert str(store.path) in message
    # Nothing about the actual workbook path changed.
    assert page.workbook_path_edit.text() == str(store.path)


# --------------------------------------------------------------------------
# Save now / Backup now
# --------------------------------------------------------------------------
def test_save_now_button_success_persists_pending_edit_and_emits_status(qtbot, store, workbook_path):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    qtbot.mouseClick(page.notifications_chk, Qt.LeftButton)  # make an unsaved edit
    assert store.is_dirty() is True

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Save now"), Qt.LeftButton)

    assert blocker.args == ["Saved."]
    assert store.is_dirty() is False

    fresh = ExcelStore(workbook_path)
    fresh.load()
    assert fresh.setting_bool("notifications", True) is False


def test_save_now_button_failure_shows_warning_not_saved_status(qtbot, store, monkeypatch):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    monkeypatch.setattr(store, "save", lambda force=False: False)
    warn_calls = []
    monkeypatch.setattr(
        settings_page_module.QMessageBox, "warning",
        lambda *a, **k: warn_calls.append(a)
    )
    status_seen = []
    page.statusMessage.connect(status_seen.append)

    qtbot.mouseClick(_button(page, "Save now"), Qt.LeftButton)

    assert len(warn_calls) == 1
    assert "Save failed" in warn_calls[0][2]
    assert "Saved." not in status_seen


def test_backup_now_button_success_creates_backup_file_and_emits_status(qtbot, store):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Backup now"), Qt.LeftButton)

    assert len(blocker.args) == 1
    assert blocker.args[0].startswith("Backup created: ")
    # store.backups_dir (store.path.parent / "backups") stays entirely
    # within pytest's own tmp_path - never the real project's data/backups/.
    backups = list(store.backups_dir.glob("study_tracker_*.xlsx"))
    assert len(backups) == 1


def test_backup_now_button_failure_shows_warning(qtbot, store, monkeypatch):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    monkeypatch.setattr(store, "backup_now", lambda: None)
    warn_calls = []
    monkeypatch.setattr(
        settings_page_module.QMessageBox, "warning",
        lambda *a, **k: warn_calls.append(a)
    )

    qtbot.mouseClick(_button(page, "Backup now"), Qt.LeftButton)

    assert len(warn_calls) == 1
    assert "Backup failed" in warn_calls[0][2]


# --------------------------------------------------------------------------
# Open data / coursepacks folders
# --------------------------------------------------------------------------
def test_open_data_folder_button_creates_the_directory(qtbot, store, tmp_path, monkeypatch):
    fake_dir = tmp_path / "data_dir_does_not_exist_yet"
    monkeypatch.setattr(config, "DATA_DIR", fake_dir)
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()
    assert not fake_dir.exists()

    with qtbot.waitSignal(page.statusMessage, timeout=2000):
        qtbot.mouseClick(_button(page, "Open data folder"), Qt.LeftButton)

    assert fake_dir.exists()


def test_open_coursepacks_folder_button_creates_the_directory(qtbot, store, tmp_path, monkeypatch):
    fake_dir = tmp_path / "coursepacks_does_not_exist_yet"
    monkeypatch.setattr(config, "COURSEPACKS_DIR", fake_dir)
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()
    assert not fake_dir.exists()

    with qtbot.waitSignal(page.statusMessage, timeout=2000):
        qtbot.mouseClick(_button(page, "Open coursepacks folder"), Qt.LeftButton)

    assert fake_dir.exists()


# --------------------------------------------------------------------------
# Extract coursepack zips now
# --------------------------------------------------------------------------
def test_extract_coursepacks_button_with_zero_zips_does_not_raise(qtbot, store, tmp_path, monkeypatch):
    fake_dir = tmp_path / "coursepacks"
    monkeypatch.setattr(config, "COURSEPACKS_DIR", fake_dir)
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    info_calls = []
    monkeypatch.setattr(
        settings_page_module.QMessageBox, "information",
        lambda *a, **k: info_calls.append(a)
    )

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Extract coursepack zips now"), Qt.LeftButton)

    assert fake_dir.exists()  # extract_coursepacks() still creates the dir
    assert blocker.args == ["Coursepack extraction finished (0 log line(s))."]
    assert len(info_calls) == 1
    assert info_calls[0][2] == "No zip files found in data/coursepacks/."


def test_extract_coursepacks_button_with_a_real_zip_extracts_it(qtbot, store, tmp_path, monkeypatch):
    fake_dir = tmp_path / "coursepacks"
    fake_dir.mkdir()
    _make_zip(fake_dir / "TEST101.zip", ["slides.pdf"])
    monkeypatch.setattr(config, "COURSEPACKS_DIR", fake_dir)
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    info_calls = []
    monkeypatch.setattr(
        settings_page_module.QMessageBox, "information",
        lambda *a, **k: info_calls.append(a)
    )

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Extract coursepack zips now"), Qt.LeftButton)

    assert (fake_dir / "TEST101" / "slides.pdf").exists()
    assert blocker.args == ["Coursepack extraction finished (1 log line(s))."]
    assert len(info_calls) == 1
    assert "TEST101.zip -> TEST101/" in info_calls[0][2]


# --------------------------------------------------------------------------
# Export .ics
# --------------------------------------------------------------------------
def test_export_ics_button_with_no_courses_shows_info_and_writes_nothing(qtbot, empty_store, monkeypatch, tmp_path):
    page = SettingsPage(empty_store)
    qtbot.addWidget(page)
    page.refresh()

    info_calls = []
    monkeypatch.setattr(
        settings_page_module.QMessageBox, "information",
        lambda *a, **k: info_calls.append(a)
    )
    save_calls = []
    monkeypatch.setattr(
        settings_page_module.QFileDialog, "getSaveFileName",
        lambda *a, **k: (save_calls.append(1) or ("", ""))
    )

    qtbot.mouseClick(_button(page, "Export .ics"), Qt.LeftButton)

    assert len(info_calls) == 1
    assert info_calls[0][2] == "No courses to export yet."
    assert save_calls == []  # never got as far as picking a save path


def test_export_ics_button_writes_all_active_courses_by_default(qtbot, store, tmp_path, monkeypatch):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    # All courses are active, so the dialog's checkboxes start all checked -
    # accept it as-is (all 6 courses), classes excluded (default unchecked).
    monkeypatch.setattr(
        settings_page_module._IcsExportDialog, "exec",
        lambda self: QDialog.Accepted,
    )
    dest = tmp_path / "out.ics"
    monkeypatch.setattr(
        settings_page_module.QFileDialog, "getSaveFileName",
        lambda *a, **k: (str(dest), "iCalendar (*.ics)"),
    )

    assessments = store.list_assessments()
    prelabs = store.list_prelabs()
    assert len(assessments) == 20
    assert len(prelabs) == 81

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Export .ics"), Qt.LeftButton)

    assert blocker.args == [f"Exported {len(assessments)}+{len(prelabs)} event(s) to out.ics."]
    assert dest.exists()
    text = dest.read_text(encoding="utf-8")
    assert text.startswith("BEGIN:VCALENDAR")
    assert text.rstrip("\r\n").endswith("END:VCALENDAR")
    # A known real course code is present in the export.
    assert "SUMMARY:CMTC2341:" in text


def test_export_ics_unchecking_one_course_via_real_click_excludes_its_events(qtbot, store, tmp_path, monkeypatch):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    target = next(c for c in store.list_courses() if c.code == "INST2361")  # not first/last

    def fake_exec(self):
        # Real click on the ACTUAL checkbox inside the real dialog - not
        # just calling setChecked() - to catch any closure/signal bug in
        # how each row's checkbox is wired to its course id. The dialog must
        # actually be shown for a synthesized click to reach its checkbox.
        qtbot.addWidget(self)
        self.show()
        cb = next(cb for cid, cb in self._course_checks if cid == target.course_id)
        assert cb.isChecked() is True  # active courses start pre-checked
        qtbot.mouseClick(cb, Qt.LeftButton)
        assert cb.isChecked() is False
        return QDialog.Accepted

    monkeypatch.setattr(settings_page_module._IcsExportDialog, "exec", fake_exec)
    dest = tmp_path / "out.ics"
    monkeypatch.setattr(
        settings_page_module.QFileDialog, "getSaveFileName",
        lambda *a, **k: (str(dest), ""),
    )

    all_assessments = store.list_assessments()
    excluded_count = sum(1 for a in all_assessments if a.course_id == target.course_id)
    included_count = len(all_assessments) - excluded_count

    with qtbot.waitSignal(page.statusMessage, timeout=2000):
        qtbot.mouseClick(_button(page, "Export .ics"), Qt.LeftButton)

    text = dest.read_text(encoding="utf-8")
    assert f"SUMMARY:{target.code}:" not in text
    assert "SUMMARY:CMTC2341:" in text  # some other, included course still present
    assert included_count >= 1  # sanity: the assertion above is meaningful


def test_export_ics_no_courses_selected_emits_status_and_writes_no_file(qtbot, store, tmp_path, monkeypatch):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    def fake_exec(self):
        qtbot.addWidget(self)
        self.show()
        # cb.click() rather than qtbot.mouseClick(): the last checkbox's row
        # sits right at the scroll area's clipped edge in this small dialog,
        # where a screen-position-based synthetic click is unreliable - the
        # single, spatially-isolated checkbox click test above already
        # covers a real positional mouseClick on this exact widget type.
        for _cid, cb in self._course_checks:
            if cb.isChecked():
                cb.click()
        return QDialog.Accepted

    monkeypatch.setattr(settings_page_module._IcsExportDialog, "exec", fake_exec)
    save_calls = []
    monkeypatch.setattr(
        settings_page_module.QFileDialog, "getSaveFileName",
        lambda *a, **k: (save_calls.append(1) or ("", "")),
    )

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Export .ics"), Qt.LeftButton)

    assert blocker.args == ["No courses selected - nothing exported."]
    assert save_calls == []


# --------------------------------------------------------------------------
# Import syllabus PDF
# --------------------------------------------------------------------------
def test_import_syllabus_cancelled_file_dialog_is_a_noop(qtbot, store, monkeypatch):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    monkeypatch.setattr(
        settings_page_module.QFileDialog, "getOpenFileName", lambda *a, **k: ("", "")
    )
    before = len(store.list_assessments())

    qtbot.mouseClick(_button(page, "Import syllabus PDF"), Qt.LeftButton)

    assert len(store.list_assessments()) == before


def test_import_syllabus_no_matches_shows_info(qtbot, store, monkeypatch, tmp_path):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    monkeypatch.setattr(
        settings_page_module.QFileDialog, "getOpenFileName",
        lambda *a, **k: (str(tmp_path / "syllabus.pdf"), ""),
    )
    monkeypatch.setattr(settings_page_module.importers, "scan_syllabus_pdf_dates", lambda p: [])
    info_calls = []
    monkeypatch.setattr(
        settings_page_module.QMessageBox, "information",
        lambda *a, **k: info_calls.append(a),
    )
    before = len(store.list_assessments())

    qtbot.mouseClick(_button(page, "Import syllabus PDF"), Qt.LeftButton)

    assert len(info_calls) == 1
    assert "No date-like lines" in info_calls[0][2]
    assert len(store.list_assessments()) == before


def test_import_syllabus_no_courses_shows_warning(qtbot, empty_store, monkeypatch, tmp_path):
    page = SettingsPage(empty_store)
    qtbot.addWidget(page)
    page.refresh()

    monkeypatch.setattr(
        settings_page_module.QFileDialog, "getOpenFileName",
        lambda *a, **k: (str(tmp_path / "syllabus.pdf"), ""),
    )
    monkeypatch.setattr(
        settings_page_module.importers, "scan_syllabus_pdf_dates",
        lambda p: [{"title": "Midterm", "date": "2026-10-01", "page": 3}],
    )
    warn_calls = []
    monkeypatch.setattr(
        settings_page_module.QMessageBox, "warning",
        lambda *a, **k: warn_calls.append(a),
    )

    qtbot.mouseClick(_button(page, "Import syllabus PDF"), Qt.LeftButton)

    assert len(warn_calls) == 1
    assert "Add a course first" in warn_calls[0][2]


def test_import_syllabus_happy_path_real_checkbox_and_combo_interaction_adds_assessments(
    qtbot, store, workbook_path, monkeypatch, tmp_path
):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    target = next(c for c in store.list_courses() if c.code == "INST2340")

    monkeypatch.setattr(
        settings_page_module.QFileDialog, "getOpenFileName",
        lambda *a, **k: (str(tmp_path / "syllabus.pdf"), ""),
    )
    scan_results = [
        {"title": "Midterm Exam", "date": "2026-10-15", "page": 4},
        {"title": "Reading Response", "date": None, "page": 7},
    ]
    monkeypatch.setattr(
        settings_page_module.importers, "scan_syllabus_pdf_dates", lambda p: scan_results
    )

    def fake_exec(self):
        qtbot.addWidget(self)
        self.show()
        # Pre-check state is a concrete fact: rows with a recognized date
        # start checked, rows without one start unchecked.
        assert self._rows[0]["checkbox"].isChecked() is True
        assert self._rows[1]["checkbox"].isChecked() is False
        # Real click to opt the second (undated) row in too.
        qtbot.mouseClick(self._rows[1]["checkbox"], Qt.LeftButton)
        assert self._rows[1]["checkbox"].isChecked() is True

        # Real, keyboard-driven combo selection - not a direct setCurrentText()
        # call - to pick the target course out of several.
        combo = self.course_combo
        target_index = next(
            i for i in range(combo.count()) if combo.itemData(i) == target.course_id
        )
        combo.setFocus()
        steps = target_index - combo.currentIndex()
        key = Qt.Key_Down if steps > 0 else Qt.Key_Up
        for _ in range(abs(steps)):
            qtbot.keyClick(combo, key)
        assert combo.currentData() == target.course_id

        return QDialog.Accepted

    monkeypatch.setattr(settings_page_module._SyllabusImportDialog, "exec", fake_exec)

    before = store.list_assessments()

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Import syllabus PDF"), Qt.LeftButton)

    assert blocker.args == ["Imported 2 assessment(s) from the syllabus PDF."]

    after = store.list_assessments()
    assert len(after) == len(before) + 2
    added = [a for a in after if a not in before]
    assert {a.title for a in added} == {"Midterm Exam", "Reading Response"}
    for a in added:
        assert a.course_id == target.course_id
        assert a.type == "Assignment"
    midterm = next(a for a in added if a.title == "Midterm Exam")
    assert midterm.due_date == date(2026, 10, 15)
    assert midterm.notes == "Imported from syllabus PDF page 4"
    reading = next(a for a in added if a.title == "Reading Response")
    assert reading.due_date is None  # no date text -> unparsed -> None
    assert reading.notes == "Imported from syllabus PDF page 7"

    # Persisted to disk too, not just the in-memory workbook.
    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    fresh_titles = {a.title for a in fresh.list_assessments()}
    assert {"Midterm Exam", "Reading Response"} <= fresh_titles


def test_import_syllabus_no_rows_checked_emits_status_and_adds_nothing(qtbot, store, monkeypatch, tmp_path):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    monkeypatch.setattr(
        settings_page_module.QFileDialog, "getOpenFileName",
        lambda *a, **k: (str(tmp_path / "syllabus.pdf"), ""),
    )
    monkeypatch.setattr(
        settings_page_module.importers, "scan_syllabus_pdf_dates",
        lambda p: [{"title": "Midterm", "date": "2026-10-01", "page": 3}],
    )

    def fake_exec(self):
        qtbot.addWidget(self)
        self.show()
        qtbot.mouseClick(self._rows[0]["checkbox"], Qt.LeftButton)  # uncheck the only row
        return QDialog.Accepted

    monkeypatch.setattr(settings_page_module._SyllabusImportDialog, "exec", fake_exec)
    before = len(store.list_assessments())

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Import syllabus PDF"), Qt.LeftButton)

    assert blocker.args == ["No rows selected - nothing imported."]
    assert len(store.list_assessments()) == before


# --------------------------------------------------------------------------
# Weekly summary: the page only requests it, the shell owns showing it
# --------------------------------------------------------------------------
def test_show_weekly_summary_button_emits_showWeeklySummaryRequested(qtbot, store):
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    with qtbot.waitSignal(page.showWeeklySummaryRequested, timeout=2000):
        qtbot.mouseClick(_button(page, "Show weekly summary now"), Qt.LeftButton)


def test_navigateTo_is_never_emitted_by_any_of_this_pages_own_actions(qtbot, store):
    """SettingsPage declares navigateTo (a shared page-signal convention)
    but its own docstring says it only ever emits showWeeklySummaryRequested
    (plus statusMessage) - confirm nothing here fires it."""
    page = SettingsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    seen: list[str] = []
    page.navigateTo.connect(seen.append)

    qtbot.mouseClick(page.notifications_chk, Qt.LeftButton)
    qtbot.mouseClick(_button(page, "Show weekly summary now"), Qt.LeftButton)

    assert seen == []
