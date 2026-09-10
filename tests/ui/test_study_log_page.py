"""Tests for app.ui.study_log_page.StudyLogPage - the cross-course study
timer + log table + charts + heatmap + streak (build spec section 5.9).

The real supplied workbook has 6 courses (CMTC2341, CNTR2371, INST2310,
INST2340, INST2361, INST2380) and an empty StudyLog sheet, so every baseline
fact below is computed from the same ``store`` fixture the page itself reads
from rather than hardcoded, except the sheet being empty (verified once,
directly, as the known starting point every test builds on).
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QPushButton

from app import config, models
from app.excel_store import ExcelStore
from app.ui.study_log_page import StudyLogPage


def _button(page: StudyLogPage, text: str) -> QPushButton:
    """The one QPushButton with this exact text on the page - StudyLogPage
    has no tabs, so every button is always laid out and clickable."""
    matches = [b for b in page.findChildren(QPushButton) if b.text() == text]
    assert len(matches) == 1, f"expected exactly one {text!r} button, found {len(matches)}"
    return matches[0]


# --------------------------------------------------------------------------
# Baseline: construct with the real store, refresh(), check concrete facts.
# --------------------------------------------------------------------------
def test_construction_and_refresh_renders_real_workbook_data(qtbot, store):
    page = StudyLogPage(store)
    qtbot.addWidget(page)
    page.refresh()

    courses = store.list_courses()
    assert len(courses) == 6
    combo_codes = {page.course_combo.itemText(i) for i in range(page.course_combo.count())}
    assert combo_codes == {c.code for c in courses}
    assert "CMTC2341" in combo_codes and "INST2380" in combo_codes

    # The real StudyLog sheet is empty - the table model and the store agree.
    assert store.list_study_log() == []
    assert page.model.rowCount() == 0

    assert page.streak_label.text() == "0 day streak, best 0"
    assert page.elapsed_label.text() == "00:00"
    assert page.pomodoro_cb.text() == f"Pomodoro {config.DEFAULT_POMODORO_STUDY_MIN}/{config.DEFAULT_POMODORO_BREAK_MIN}"

    button_texts = {b.text() for b in page.findChildren(QPushButton)}
    assert button_texts == {"+ Add entry", "Edit", "Delete", "Start", "Pause", "Stop"}

    assert [page.activity_combo.itemText(i) for i in range(page.activity_combo.count())] == config.STUDYLOG_ACTIVITIES
    assert page.activity_combo.currentText() == "Reading"


def test_course_combo_selection_reloads_topic_combo_with_real_topics(qtbot, store):
    page = StudyLogPage(store)
    qtbot.addWidget(page)

    courses = store.list_courses()
    first_course, second_course = courses[0], courses[1]

    assert page.course_combo.currentData() == first_course.course_id
    first_topics = store.list_topics(first_course.course_id)
    assert len(first_topics) == 50
    # "(no topic)" plus one entry per real topic.
    assert page.topic_combo.count() == len(first_topics) + 1
    assert page.topic_combo.itemText(0) == "(no topic)"
    assert page.topic_combo.itemData(0) == 0

    # Real QComboBox selection - setCurrentIndex is exactly what Qt calls
    # internally when a user picks an entry from the popup.
    idx = page.course_combo.findData(second_course.course_id)
    assert idx >= 0
    page.course_combo.setCurrentIndex(idx)

    assert page.course_combo.currentData() == second_course.course_id
    second_topics = store.list_topics(second_course.course_id)
    assert len(second_topics) == 59
    assert page.topic_combo.count() == len(second_topics) + 1
    expected_label = f"{second_topics[0].section} {second_topics[0].title}".strip()
    assert page.topic_combo.itemText(1) == expected_label
    assert page.topic_combo.itemData(1) == second_topics[0].topic_id


# --------------------------------------------------------------------------
# Start guard: no courses at all (empty workbook).
# --------------------------------------------------------------------------
def test_start_with_no_courses_emits_status_and_never_starts(qtbot, empty_store):
    page = StudyLogPage(empty_store)
    qtbot.addWidget(page)
    assert page.course_combo.count() == 0

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Start"), Qt.LeftButton)

    assert blocker.args == ["Add a course first (Courses page)."]
    assert page._timer_running is False
    assert not page._qtimer.isActive()


# --------------------------------------------------------------------------
# DEEP interaction flow: select course + topic, real Start click, advance
# elapsed time via the tick handler (no real sleeping), real Stop click with
# the confidence QInputDialog monkeypatched, and verify both the new
# StudyLogEntry and the topic's updated confidence - in the store, and on
# disk after an explicit save.
# --------------------------------------------------------------------------
def test_timer_start_tick_stop_persists_entry_and_updates_topic_confidence(
    qtbot, store, workbook_path, monkeypatch
):
    page = StudyLogPage(store)
    qtbot.addWidget(page)

    courses = store.list_courses()
    target_course = courses[2]  # INST2310 - deliberately not the default combo index 0
    target_topic = store.list_topics(target_course.course_id)[0]

    course_idx = page.course_combo.findData(target_course.course_id)
    assert course_idx >= 0
    page.course_combo.setCurrentIndex(course_idx)
    topic_idx = page.topic_combo.findData(target_topic.topic_id)
    assert topic_idx >= 0
    page.topic_combo.setCurrentIndex(topic_idx)

    assert page.course_combo.currentData() == target_course.course_id
    assert page.topic_combo.currentData() == target_topic.topic_id

    qtbot.mouseClick(_button(page, "Start"), Qt.LeftButton)
    assert page._timer_running is True
    assert page._qtimer.isActive() is True

    # Advance elapsed time without sleeping for real seconds: drive the same
    # internal tick handler the QTimer would call once per second.
    for _ in range(125):
        page._on_tick()
    assert page.elapsed_label.text() == "02:05"

    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (4, True))

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Stop"), Qt.LeftButton)

    assert blocker.args == ["Logged 2 minute(s) of study."]
    assert page._timer_running is False
    assert not page._qtimer.isActive()
    assert page.elapsed_label.text() == "00:00"

    entries = store.list_study_log()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.course_id == target_course.course_id
    assert entry.topic_id == target_topic.topic_id
    assert entry.minutes == 2
    assert entry.activity == "Reading"

    updated_topic = store.get_topic(target_topic.topic_id)
    assert updated_topic.confidence == 4

    # Persisted to disk, not just held in the in-memory workbook.
    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    fresh_entries = fresh.list_study_log()
    assert len(fresh_entries) == 1
    assert fresh_entries[0].minutes == 2
    assert fresh_entries[0].course_id == target_course.course_id
    assert fresh.get_topic(target_topic.topic_id).confidence == 4


def test_stop_before_one_minute_elapsed_does_not_log_an_entry(qtbot, store):
    page = StudyLogPage(store)
    qtbot.addWidget(page)

    qtbot.mouseClick(_button(page, "Start"), Qt.LeftButton)
    for _ in range(10):
        page._on_tick()
    assert page.elapsed_label.text() == "00:10"

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Stop"), Qt.LeftButton)

    assert blocker.args == ["Timer stopped — less than a minute, not logged."]
    assert store.list_study_log() == []
    assert page._timer_running is False
    assert page.elapsed_label.text() == "00:00"


# --------------------------------------------------------------------------
# Cross-page contract: Dashboard's "Start 25-min timer" button.
# --------------------------------------------------------------------------
def test_start_timer_for_preselects_combos_and_actually_starts(qtbot, store):
    page = StudyLogPage(store)
    qtbot.addWidget(page)

    courses = store.list_courses()
    target_course = courses[-1]  # last course - not the default combo index 0
    target_topic = store.list_topics(target_course.course_id)[0]

    page.start_timer_for(target_course.course_id, target_topic.topic_id)

    assert page.course_combo.currentData() == target_course.course_id
    assert page.topic_combo.currentData() == target_topic.topic_id
    assert page.pomodoro_cb.isChecked() is True
    assert page._timer_running is True
    assert page._qtimer.isActive() is True


def test_start_timer_for_unknown_course_id_falls_back_to_first_course(qtbot, store):
    page = StudyLogPage(store)
    qtbot.addWidget(page)

    page.start_timer_for(course_id=999999)

    assert page.course_combo.currentIndex() == 0
    assert page.topic_combo.currentData() == 0  # "(no topic)" - none was requested
    assert page._timer_running is True


# --------------------------------------------------------------------------
# Pomodoro checkbox (real toggle) drives the banner through a full phase
# switch once the study block's minutes have elapsed.
# --------------------------------------------------------------------------
def test_pomodoro_checkbox_toggle_and_elapsed_ticks_switch_to_break_phase(qtbot, store):
    page = StudyLogPage(store)
    qtbot.addWidget(page)
    page.show()  # a checkbox needs real on-screen geometry for a synthetic click to land
    assert page.pomodoro_banner.isHidden()

    qtbot.mouseClick(page.pomodoro_cb, Qt.LeftButton)
    assert page.pomodoro_cb.isChecked() is True

    qtbot.mouseClick(_button(page, "Start"), Qt.LeftButton)
    assert page._pomodoro_phase == "Study"

    messages: list[str] = []
    page.statusMessage.connect(messages.append)
    study_seconds = config.DEFAULT_POMODORO_STUDY_MIN * 60
    for _ in range(study_seconds):
        page._on_tick()

    assert page._pomodoro_phase == "Break"
    assert not page.pomodoro_banner.isHidden()
    expected_msg = f"Study block done — take a {config.DEFAULT_POMODORO_BREAK_MIN} min break."
    assert messages == [expected_msg]
    assert page.pomodoro_banner.text() == f"🍅 {expected_msg}"

    qtbot.mouseClick(_button(page, "Stop"), Qt.LeftButton)
    assert page._timer_running is False


# --------------------------------------------------------------------------
# Manual "+ Add entry" flow (QInputDialog.getItem + dialogs.edit_row
# monkeypatched, per the safety rules - never let a real dialog exec()).
# --------------------------------------------------------------------------
def test_add_entry_button_persists_new_row_to_store_and_disk(qtbot, store, workbook_path, monkeypatch):
    page = StudyLogPage(store)
    qtbot.addWidget(page)

    target_course = store.list_courses()[3]  # INST2340
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (target_course.code, True)))

    def fake_edit_row(parent, title, columns, obj, exclude_attrs=None, multiline_attrs=None):
        obj.date = date(2026, 9, 15)
        obj.minutes = 45
        obj.activity = "Lab"
        obj.notes = "added by test"
        return obj

    monkeypatch.setattr("app.ui.dialogs.edit_row", fake_edit_row)

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "+ Add entry"), Qt.LeftButton)

    assert blocker.args == ["Study log entry added."]

    entries = store.list_study_log()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.course_id == target_course.course_id
    assert entry.minutes == 45
    assert entry.activity == "Lab"
    assert entry.notes == "added by test"
    assert entry.date == date(2026, 9, 15)
    assert page.model.rowCount() == 1

    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    fresh_entries = fresh.list_study_log()
    assert len(fresh_entries) == 1
    assert fresh_entries[0].minutes == 45
    assert fresh_entries[0].course_id == target_course.course_id


def test_add_entry_button_with_no_courses_emits_status_and_adds_nothing(qtbot, empty_store):
    page = StudyLogPage(empty_store)
    qtbot.addWidget(page)

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "+ Add entry"), Qt.LeftButton)

    assert blocker.args == ["Add a course first (Courses page)."]
    assert empty_store.list_study_log() == []


# --------------------------------------------------------------------------
# Edit / Delete on an existing row.
# --------------------------------------------------------------------------
def test_edit_entry_button_updates_selected_row_and_persists(qtbot, store, workbook_path, monkeypatch):
    course_id = store.list_courses()[0].course_id
    seeded = store.add_study_log_entry(models.StudyLogEntry(
        course_id=course_id, date=date(2026, 9, 2), minutes=30, activity="Reading", notes="orig",
    ))

    page = StudyLogPage(store)
    qtbot.addWidget(page)
    assert page.model.rowCount() == 1
    page.table.selectRow(0)

    def fake_edit_row(parent, title, columns, obj, exclude_attrs=None, multiline_attrs=None):
        obj.minutes = 99
        obj.notes = "edited by test"
        return obj

    monkeypatch.setattr("app.ui.dialogs.edit_row", fake_edit_row)

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Edit"), Qt.LeftButton)

    assert blocker.args == ["Study log entry updated."]

    updated = store.get_study_log_entry(seeded.log_id)
    assert updated.minutes == 99
    assert updated.notes == "edited by test"

    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    assert fresh.get_study_log_entry(seeded.log_id).minutes == 99


def test_delete_entry_button_confirmed_removes_row_from_store(qtbot, store, monkeypatch):
    course_id = store.list_courses()[0].course_id
    seeded = store.add_study_log_entry(models.StudyLogEntry(
        course_id=course_id, date=date(2026, 9, 3), minutes=20, activity="Practice",
    ))

    page = StudyLogPage(store)
    qtbot.addWidget(page)
    assert page.model.rowCount() == 1
    page.table.selectRow(0)

    monkeypatch.setattr("app.ui.widgets.confirm", lambda *a, **k: True)

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Delete"), Qt.LeftButton)

    assert blocker.args == ["Study log entry deleted."]
    assert store.list_study_log() == []
    assert store.get_study_log_entry(seeded.log_id) is None
    assert page.model.rowCount() == 0


def test_delete_entry_button_without_selection_emits_status_message(qtbot, store):
    page = StudyLogPage(store)
    qtbot.addWidget(page)
    assert page.model.rowCount() == 0

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Delete"), Qt.LeftButton)

    assert blocker.args == ["Select a study log entry first."]
