"""Tests for app.ui.courses_page.CoursesPage - the "Courses" grid of large,
per-course buttons (build spec §5.3).

Every assertion about "what's on the page" is anchored to the same ``store``
the page itself reads from (course codes, ids, counts) rather than a
hardcoded row count, except where the real Fall 2026 workbook's shape (six
active courses, specific codes) is asserted directly as a known fact about
the supplied data.
"""
from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton

from app import models
from app.excel_store import ExcelStore
from app.ui import courses_page as courses_page_module
from app.ui.courses_page import CoursesPage


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _grid_buttons(page: CoursesPage) -> list[QPushButton]:
    """Buttons inside the dynamic course grid only - excludes the toolbar's
    "+ Add course" button, which lives outside ``_grid_host``."""
    return page._grid_host.findChildren(QPushButton)


def _button_for_code(page: CoursesPage, code: str) -> QPushButton:
    matches = [b for b in _grid_buttons(page) if b.text().startswith(code)]
    assert len(matches) == 1, (
        f"expected exactly one course button starting with {code!r}, found {len(matches)}"
    )
    return matches[0]


def _add_course_button(page: CoursesPage) -> QPushButton:
    matches = [b for b in page.findChildren(QPushButton) if b.text() == "+ Add course"]
    assert len(matches) == 1
    return matches[0]


# --------------------------------------------------------------------------
# Baseline: construct with the real store, refresh(), check concrete facts
# --------------------------------------------------------------------------
def test_refresh_builds_one_button_per_active_course_from_real_workbook(qtbot, store):
    page = CoursesPage(store)
    qtbot.addWidget(page)

    page.refresh()  # must not raise

    courses = store.list_courses(active_only=True)
    assert len(courses) == 6  # the real Fall 2026 workbook has 6 active courses

    buttons = _grid_buttons(page)
    assert len(buttons) == 6

    button_texts = [b.text() for b in buttons]
    # Every active course from the store has exactly one matching button.
    for course in courses:
        assert sum(t.startswith(course.code) for t in button_texts) == 1

    # Spot-check two concrete, known courses render their code + name.
    assert any(t.startswith("CMTC2341 — Data Communications") for t in button_texts)
    assert any(t.startswith("INST2380 — Analyzers I") for t in button_texts)

    # The "+ Add course" toolbar button also exists, outside the grid.
    assert _add_course_button(page).isEnabled()


def test_refresh_when_store_raises_emits_status_message_and_shows_empty_grid(qtbot, store, monkeypatch):
    def _raise(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(store, "list_courses", _raise)

    page = CoursesPage(store)
    qtbot.addWidget(page)

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        page.refresh()
    assert blocker.args == ["Could not load courses."]

    assert _grid_buttons(page) == []
    empty_labels = [l.text() for l in page._grid_host.findChildren(QLabel)]
    assert "(none yet)" in empty_labels


# --------------------------------------------------------------------------
# openCourse regression coverage: a closure/signal-payload bug once made
# every course button fire with the same (wrong) course_id - these tests
# click buttons OTHER than the first/last, and out of definition order, to
# guard against that class of bug reappearing.
# --------------------------------------------------------------------------
def test_clicking_a_middle_course_button_emits_openCourse_with_matching_id(qtbot, store):
    page = CoursesPage(store)
    qtbot.addWidget(page)
    page.refresh()

    courses = store.list_courses(active_only=True)
    assert len(courses) == 6
    target = courses[3]  # 4th course in the sheet - not first, not last
    assert target.code == "INST2340"
    assert target.course_id not in (0, courses[0].course_id, courses[-1].course_id)

    btn = _button_for_code(page, target.code)

    with qtbot.waitSignal(page.openCourse, timeout=2000) as blocker:
        qtbot.mouseClick(btn, Qt.LeftButton)
    assert blocker.args == [target.course_id]


def test_clicking_course_buttons_out_of_order_each_emits_its_own_course_id(qtbot, store):
    page = CoursesPage(store)
    qtbot.addWidget(page)
    page.refresh()

    courses_by_code = {c.code: c for c in store.list_courses(active_only=True)}
    first_id = courses_by_code["CMTC2341"].course_id
    middle_id = courses_by_code["INST2340"].course_id
    last_id = courses_by_code["INST2380"].course_id
    assert 0 not in (first_id, middle_id, last_id)
    assert len({first_id, middle_id, last_id}) == 3

    fired: list[int] = []
    page.openCourse.connect(fired.append)

    # Click LAST, then MIDDLE, then FIRST - deliberately out of definition
    # order. The bug this guards against made every button's callback fire
    # with whichever course_id a shared loop variable last held, regardless
    # of which button was actually clicked.
    qtbot.mouseClick(_button_for_code(page, "INST2380"), Qt.LeftButton)
    qtbot.mouseClick(_button_for_code(page, "INST2340"), Qt.LeftButton)
    qtbot.mouseClick(_button_for_code(page, "CMTC2341"), Qt.LeftButton)

    assert fired == [last_id, middle_id, first_id]


# --------------------------------------------------------------------------
# "+ Add course": real click, dialog result persisted through the store,
# checked with a fresh read (and a reload from disk, once saved).
# --------------------------------------------------------------------------
def test_add_course_button_click_persists_a_new_active_course(qtbot, store, workbook_path, monkeypatch):
    page = CoursesPage(store)
    qtbot.addWidget(page)
    page.refresh()

    before = store.list_courses(active_only=True)
    assert len(before) == 6
    before_ids = {c.course_id for c in before}

    new_course = models.Course(
        code="TEST999",
        name="Testing Fundamentals",
        term="Fall 2026",
        target_grade=90.0,
        color_hex="#123456",
    )
    monkeypatch.setattr(courses_page_module.dialogs, "edit_row", lambda *_a, **_k: new_course)

    qtbot.mouseClick(_add_course_button(page), Qt.LeftButton)

    # Fresh read from the SAME store: the add persisted into its in-memory
    # workbook and the page rebuilt its buttons for it.
    after = store.list_courses(active_only=True)
    assert len(after) == 7
    added = next(c for c in after if c.code == "TEST999")
    assert added.course_id not in before_ids
    assert added.name == "Testing Fundamentals"

    assert len(_grid_buttons(page)) == 7
    assert _button_for_code(page, "TEST999") is not None

    # And it reaches the on-disk workbook once saved - not just an in-memory
    # list - confirming add_course wrote a real new row.
    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    assert any(c.code == "TEST999" for c in fresh.list_courses())


def test_add_course_cancelled_dialog_does_not_add_a_course(qtbot, store, monkeypatch):
    page = CoursesPage(store)
    qtbot.addWidget(page)
    page.refresh()

    monkeypatch.setattr(courses_page_module.dialogs, "edit_row", lambda *_a, **_k: None)

    qtbot.mouseClick(_add_course_button(page), Qt.LeftButton)

    assert len(store.list_courses(active_only=True)) == 6
    assert len(_grid_buttons(page)) == 6


# --------------------------------------------------------------------------
# Edit / Archive / Delete: these normally arrive via the button's
# right-click QMenu, whose .exec() blocks headlessly, so the handlers are
# invoked directly here (per the task's dialog-safety rules) with the
# dialog/confirm calls they'd otherwise make monkeypatched out.
# --------------------------------------------------------------------------
def test_edit_course_updates_store_and_rerenders_its_button(qtbot, store, monkeypatch):
    page = CoursesPage(store)
    qtbot.addWidget(page)
    page.refresh()

    course = next(c for c in store.list_courses(active_only=True) if c.code == "INST2310")
    edited = dataclasses.replace(course, name="Renamed Measurements Course")
    monkeypatch.setattr(courses_page_module.dialogs, "edit_row", lambda *_a, **_k: edited)

    page._on_edit_course(course)

    updated = store.get_course(course.course_id)
    assert updated.name == "Renamed Measurements Course"

    btn = _button_for_code(page, "INST2310")
    assert "Renamed Measurements Course" in btn.text()


def test_archive_course_confirmed_removes_it_from_the_active_grid(qtbot, store, monkeypatch):
    page = CoursesPage(store)
    qtbot.addWidget(page)
    page.refresh()

    course = next(c for c in store.list_courses(active_only=True) if c.code == "CNTR2371")
    monkeypatch.setattr(courses_page_module.widgets, "confirm", lambda *_a, **_k: True)

    page._on_archive_course(course)

    assert store.get_course(course.course_id).active is False
    remaining = store.list_courses(active_only=True)
    assert len(remaining) == 5
    assert "CNTR2371" not in {c.code for c in remaining}
    assert len(_grid_buttons(page)) == 5


def test_archive_course_declined_confirmation_leaves_it_untouched(qtbot, store, monkeypatch):
    page = CoursesPage(store)
    qtbot.addWidget(page)
    page.refresh()

    course = next(c for c in store.list_courses(active_only=True) if c.code == "CNTR2371")
    monkeypatch.setattr(courses_page_module.widgets, "confirm", lambda *_a, **_k: False)

    page._on_archive_course(course)

    assert store.get_course(course.course_id).active is True
    assert len(store.list_courses(active_only=True)) == 6
    assert len(_grid_buttons(page)) == 6


def test_delete_course_confirmed_removes_it_entirely(qtbot, store, monkeypatch):
    page = CoursesPage(store)
    qtbot.addWidget(page)
    page.refresh()

    course = next(c for c in store.list_courses(active_only=True) if c.code == "INST2380")
    monkeypatch.setattr(courses_page_module.widgets, "confirm", lambda *_a, **_k: True)

    page._on_delete_course(course)

    assert store.get_course(course.course_id) is None
    assert len(store.list_courses()) == 5
    assert len(_grid_buttons(page)) == 5


# --------------------------------------------------------------------------
# Edge case: a brand-new, completely empty workbook (zero courses).
# --------------------------------------------------------------------------
def test_empty_store_shows_no_course_buttons_and_add_course_still_works(qtbot, empty_store, monkeypatch):
    page = CoursesPage(empty_store)
    qtbot.addWidget(page)
    page.refresh()

    assert empty_store.list_courses(active_only=True) == []
    assert _grid_buttons(page) == []
    assert "(none yet)" in [l.text() for l in page._grid_host.findChildren(QLabel)]
    assert _add_course_button(page).isEnabled()

    new_course = models.Course(code="NEW101", name="Brand New Course")
    monkeypatch.setattr(courses_page_module.dialogs, "edit_row", lambda *_a, **_k: new_course)

    qtbot.mouseClick(_add_course_button(page), Qt.LeftButton)

    courses = empty_store.list_courses(active_only=True)
    assert len(courses) == 1
    assert courses[0].course_id == 1
    assert len(_grid_buttons(page)) == 1
