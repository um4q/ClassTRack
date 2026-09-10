"""Tests for app.ui.main_window.MainWindow - the application shell that
wires the sidebar, the lazily-built page stack, and the small cross-page
signal contract described in each page module's own docstring
(openCourse, quickBrainDump, startTimerRequested, new_note_today,
start_timer_for, ...).

This is the single most bug-prone integration point in the app: two real,
user-visible bugs (a button-closure bug in the course-grid buttons, and a
resource path-resolution bug) were only ever caught by testing through this
exact end-to-end path - clicking a real button and following it all the way
to Course Detail - not by testing pages in isolation. The click test below
deliberately uses qtbot.mouseClick on a real QPushButton rather than calling
a handler method directly, for that reason; weakening it to a direct call
would defeat its purpose.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton

from app.ui.main_window import PAGE_ORDER, MainWindow


# --------------------------------------------------------------------------
# Safety net: MainWindow schedules a background "weekly summary" check
# 800ms after construction (QTimer.singleShot) that can pop a modal QDialog
# via dlg.exec() if the Qt event loop happens to get pumped for that long
# while a test is running (e.g. several slow page constructions in a row).
# Neutralize QDialog.exec() globally for this file so a stray timer firing
# mid-test can never hang the suite, per the "never call .exec() on a
# QDialog in a test" rule - none of the tests below rely on that dialog.
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _never_block_on_a_stray_dialog(monkeypatch):
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Rejected)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _course_grid_buttons(win: MainWindow) -> list[QPushButton]:
    """Buttons inside the Courses page's dynamic grid only - excludes its
    own "+ Add course" toolbar button, which lives outside ``_grid_host``."""
    return win.courses_page._grid_host.findChildren(QPushButton)


def _button_for_code(win: MainWindow, code: str) -> QPushButton:
    matches = [b for b in _course_grid_buttons(win) if b.text().startswith(code)]
    assert len(matches) == 1, (
        f"expected exactly one course button starting with {code!r}, found {len(matches)}"
    )
    return matches[0]


# --------------------------------------------------------------------------
# Lazy construction - a measured 88% startup-time fix; a regression here
# would silently undo it.
# --------------------------------------------------------------------------
def test_construction_builds_only_the_dashboard_page(qtbot, store):
    win = MainWindow(store)
    qtbot.addWidget(win)

    assert set(win._pages.keys()) == {"Dashboard"}
    assert win._course_detail_page is None


# --------------------------------------------------------------------------
# go_to_page() - every sidebar entry, lazy construction on first visit only
# --------------------------------------------------------------------------
def test_go_to_page_switches_stack_and_constructs_each_page_lazily_on_first_visit(qtbot, store):
    win = MainWindow(store)
    qtbot.addWidget(win)

    for name in PAGE_ORDER:
        pre_existing = name in win._pages
        win.go_to_page(name)
        assert win.stack.currentWidget() is win._pages[name]
        if name == "Dashboard":
            assert pre_existing, "Dashboard should already be built by __init__"
        else:
            assert not pre_existing, f"{name} should be constructed on this first visit, not before"

    assert set(win._pages.keys()) == set(PAGE_ORDER)

    # Revisiting an already-built page reuses the same instance.
    dashboard_first = win._pages["Dashboard"]
    win.go_to_page("Dashboard")
    assert win._pages["Dashboard"] is dashboard_first


def test_go_to_page_with_unknown_name_does_not_change_the_current_page(qtbot, store):
    win = MainWindow(store)
    qtbot.addWidget(win)
    win.go_to_page("Courses")
    before = win.stack.currentWidget()

    win.go_to_page("Not A Real Page")

    assert win.stack.currentWidget() is before
    assert "Not A Real Page" not in win._pages


# --------------------------------------------------------------------------
# The exact end-to-end path that had the closure bug: a REAL simulated click
# on a course button must open Course Detail for THAT course, not whichever
# course happened to be first/last.
# --------------------------------------------------------------------------
def test_real_click_on_course_button_opens_correct_course_detail(qtbot, store):
    win = MainWindow(store)
    qtbot.addWidget(win)

    courses = store.list_courses(active_only=True)
    assert len(courses) == 6  # the real Fall 2026 workbook has 6 active courses
    target = courses[2]  # neither the first nor the last of the six

    win.go_to_page("Courses")
    button = _button_for_code(win, target.code)

    qtbot.mouseClick(button, Qt.LeftButton)

    detail = win.course_detail_page
    assert win.stack.currentWidget() is detail
    assert detail.course_id == target.course_id
    assert detail._header_title.text() == f"{target.code} — {target.name}"


# --------------------------------------------------------------------------
# show_course_detail() - switching between two different courses in
# sequence reuses the same page and updates its header both times.
# --------------------------------------------------------------------------
def test_show_course_detail_switches_header_between_two_courses_in_sequence(qtbot, store):
    win = MainWindow(store)
    qtbot.addWidget(win)

    courses = store.list_courses(active_only=True)
    first, second = courses[1], courses[4]

    win.show_course_detail(first.course_id)
    detail = win.course_detail_page
    assert win.stack.currentWidget() is detail
    assert detail.course_id == first.course_id
    assert detail._header_title.text() == f"{first.code} — {first.name}"

    win.show_course_detail(second.course_id)
    assert win.course_detail_page is detail  # same instance, not rebuilt
    assert detail.course_id == second.course_id
    assert detail._header_title.text() == f"{second.code} — {second.name}"
    assert win.stack.currentWidget() is detail


# --------------------------------------------------------------------------
# open_quick_brain_dump() - routes to Review AND actually invokes
# ReviewPage.new_note_today().
# --------------------------------------------------------------------------
def test_open_quick_brain_dump_navigates_to_review_page_and_invokes_new_note_today(qtbot, store, monkeypatch):
    win = MainWindow(store)
    qtbot.addWidget(win)

    page = win.review_page  # trigger lazy construction before monkeypatching it
    calls: list[tuple] = []
    monkeypatch.setattr(page, "new_note_today", lambda *a, **kw: calls.append((a, kw)))

    win.open_quick_brain_dump()

    assert win.stack.currentWidget() is page
    assert calls == [((), {})]


# --------------------------------------------------------------------------
# start_timer_for() - routes to Study Log AND actually invokes
# StudyLogPage.start_timer_for() with the right arguments.
# --------------------------------------------------------------------------
def test_start_timer_for_navigates_to_study_log_page_and_invokes_start_timer_for(qtbot, store, monkeypatch):
    win = MainWindow(store)
    qtbot.addWidget(win)

    # A real course/topic pair from the supplied workbook.
    course_id, topic_id = 3, 110

    page = win.study_log_page  # trigger lazy construction before monkeypatching it
    calls: list[tuple] = []
    monkeypatch.setattr(page, "start_timer_for", lambda cid, tid: calls.append((cid, tid)))

    win.start_timer_for(course_id, topic_id)

    assert win.stack.currentWidget() is page
    assert calls == [(course_id, topic_id)]


def test_start_timer_for_with_falsy_topic_id_passes_none(qtbot, store, monkeypatch):
    win = MainWindow(store)
    qtbot.addWidget(win)

    page = win.study_log_page
    calls: list[tuple] = []
    monkeypatch.setattr(page, "start_timer_for", lambda cid, tid: calls.append((cid, tid)))

    win.start_timer_for(3, 0)  # topic_id=0 ("no topic") must become None

    assert calls == [(3, None)]


# --------------------------------------------------------------------------
# Property accessors - each returns the SAME instance on repeated access.
# --------------------------------------------------------------------------
_PAGE_PROPERTY_NAMES = [
    "dashboard_page", "courses_page", "tracker_page", "roadmap_page",
    "practice_page", "mock_exam_page", "review_page", "study_log_page",
    "goals_page", "settings_page", "course_detail_page",
]


@pytest.mark.parametrize("prop_name", _PAGE_PROPERTY_NAMES)
def test_page_property_accessor_returns_same_instance_on_repeated_access(qtbot, store, prop_name):
    win = MainWindow(store)
    qtbot.addWidget(win)

    first = getattr(win, prop_name)
    second = getattr(win, prop_name)

    assert first is second


# --------------------------------------------------------------------------
# closeEvent()
# --------------------------------------------------------------------------
def test_close_event_accepts_when_store_closes_cleanly(qtbot, store, monkeypatch):
    win = MainWindow(store)
    qtbot.addWidget(win)
    monkeypatch.setattr(win.store, "close", lambda: True)

    event = QCloseEvent()
    win.closeEvent(event)

    assert event.isAccepted()


def test_close_event_discard_proceeds_even_though_store_close_failed(qtbot, store, monkeypatch):
    win = MainWindow(store)
    qtbot.addWidget(win)
    monkeypatch.setattr(win.store, "close", lambda: False)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **kw: QMessageBox.Discard))

    event = QCloseEvent()
    win.closeEvent(event)

    assert event.isAccepted()


def test_close_event_cancel_keeps_the_window_open(qtbot, store, monkeypatch):
    win = MainWindow(store)
    qtbot.addWidget(win)
    monkeypatch.setattr(win.store, "close", lambda: False)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **kw: QMessageBox.Cancel))

    event = QCloseEvent()
    win.closeEvent(event)

    assert not event.isAccepted()
