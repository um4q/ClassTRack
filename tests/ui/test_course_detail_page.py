"""Tests for app.ui.course_detail_page.CourseDetailPage - the full detail
view for one course (build spec section 5.3): header, dynamic materials
buttons, and a 9-tab QTabWidget.

Facts asserted about "what got rendered" are computed from the same
``store``/``empty_store`` fixture the page itself reads from, since the
supplied workbook is real semester data.
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QPushButton, QWidget

from app import config, models
from app.excel_store import ExcelStore
from app.ui.course_detail_page import CourseDetailPage

EXPECTED_TAB_TITLES = [
    "Syllabus / Topics", "Labs", "Assessments", "Grades", "Roadmap",
    "Study", "Attendance", "Practice", "Flags",
]


def _buttons_with_text(widget: QWidget, text: str) -> list[QPushButton]:
    return [b for b in widget.findChildren(QPushButton) if b.text() == text]


def _materials_group_box(page: CourseDetailPage, group_name: str) -> QGroupBox:
    """The QGroupBox _refresh_materials() builds for one
    config.MATERIAL_KIND_GROUPS entry - titled '<group_name> (<count>)'."""
    prefix = f"{group_name} ("
    for box in page.findChildren(QGroupBox):
        if box.title().startswith(prefix):
            return box
    raise AssertionError(f"no materials QGroupBox found for group {group_name!r}")


# --------------------------------------------------------------------------
# Baseline: construct with the real store, show a real course, refresh(),
# check concrete facts about the render.
# --------------------------------------------------------------------------
def test_show_course_renders_header_tabs_and_assessments_for_real_course(qtbot, store):
    page = CourseDetailPage(store)
    qtbot.addWidget(page)

    course = store.get_course(1)
    assert course is not None, "expected course_id=1 to exist in the real workbook"

    page.show_course(course.course_id)
    page.refresh()  # idempotent - must not raise

    assert page._header_title.text() == f"{course.code} — {course.name}"
    assert "No course selected" not in page._header_title.text()

    tab_titles = [page._tabs.tabText(i) for i in range(page._tabs.count())]
    assert tab_titles == EXPECTED_TAB_TITLES

    expected_assessments = store.list_assessments(course.course_id)
    assert expected_assessments, "expected course 1 to have real assessments"
    assert page._assessments_model.rowCount() == len(expected_assessments)

    assert len(_buttons_with_text(page, "+ Add topic")) == 1
    assert len(_buttons_with_text(page, "← Back")) == 1


# --------------------------------------------------------------------------
# show_course() switches courses without recreating the widget.
# --------------------------------------------------------------------------
def test_show_course_switches_header_between_two_different_courses(qtbot, store):
    page = CourseDetailPage(store)
    qtbot.addWidget(page)

    courses = store.list_courses()
    assert len(courses) >= 2, "expected at least 2 real courses in the workbook"
    first, second = courses[0], courses[1]
    assert first.code != second.code

    page.show_course(first.course_id)
    assert first.code in page._header_title.text()
    assert "No course selected" not in page._header_title.text()
    assert page.course_id == first.course_id

    page.show_course(second.course_id)
    assert second.code in page._header_title.text()
    assert first.code not in page._header_title.text()
    assert page.course_id == second.course_id


def test_show_course_with_unknown_id_falls_back_to_empty_state(qtbot, store):
    page = CourseDetailPage(store)
    qtbot.addWidget(page)

    course = store.list_courses()[0]
    page.show_course(course.course_id)
    assert course.code in page._header_title.text()

    page.show_course(999999)  # no such course_id in the workbook
    assert page._header_title.text() == "No course selected"
    assert page.course is None
    assert page._assessments_model.rowCount() == 0


# --------------------------------------------------------------------------
# Materials buttons - a real click on the SECOND button in a group must
# resolve to the SECOND material, not the first (closure-capture bug class
# that already broke real buttons elsewhere in this app - see widgets.py).
# --------------------------------------------------------------------------
def test_materials_button_click_resolves_material_for_that_specific_button(qtbot, store, monkeypatch):
    page = CourseDetailPage(store)
    qtbot.addWidget(page)

    course_id = 2  # CNTR2371 - has many "Lab procedures" materials in real data
    page.show_course(course_id)

    materials = store.list_materials(course_id)
    kinds = config.MATERIAL_KIND_GROUPS["Lab procedures"]
    group = [m for m in materials if m.kind in kinds]
    assert len(group) >= 2, "expected 2+ 'Lab procedures' materials for this course"
    first_material, second_material = group[0], group[1]

    box = _materials_group_box(page, "Lab procedures")
    buttons = box.findChildren(QPushButton)
    assert len(buttons) == len(group)
    assert buttons[0].text() == (first_material.label or first_material.kind)
    assert buttons[1].text() == (second_material.label or second_material.kind)

    calls: list[models.Material] = []
    monkeypatch.setattr(page, "_on_open_material", lambda m: calls.append(m))

    # Click the SECOND button, not the first.
    qtbot.mouseClick(buttons[1], Qt.LeftButton)

    assert len(calls) == 1
    assert calls[0].material_id == second_material.material_id
    assert calls[0].label == second_material.label
    assert calls[0].material_id != first_material.material_id


def test_materials_group_with_no_materials_shows_placeholder_label(qtbot, empty_store):
    course = empty_store.add_course(models.Course(code="TEST101", name="Test Course"))
    page = CourseDetailPage(empty_store)
    qtbot.addWidget(page)

    page.show_course(course.course_id)

    for group_name in config.MATERIAL_KIND_GROUPS:
        box = _materials_group_box(page, group_name)
        assert box.title() == f"{group_name} (0)"
        placeholders = [l for l in box.findChildren(QWidget) if hasattr(l, "text") and l.text() == "(none yet)"]
        assert len(placeholders) == 1


# --------------------------------------------------------------------------
# Signals: backRequested and statusMessage.
# --------------------------------------------------------------------------
def test_back_button_click_emits_back_requested_signal(qtbot, store):
    page = CourseDetailPage(store)
    qtbot.addWidget(page)

    back_buttons = _buttons_with_text(page, "← Back")
    assert len(back_buttons) == 1

    with qtbot.waitSignal(page.backRequested, timeout=2000):
        qtbot.mouseClick(back_buttons[0], Qt.LeftButton)


def test_add_topic_button_with_no_course_selected_emits_status_message(qtbot, store):
    page = CourseDetailPage(store)
    qtbot.addWidget(page)
    assert page.course_id is None

    add_topic_buttons = _buttons_with_text(page, "+ Add topic")
    assert len(add_topic_buttons) == 1

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(add_topic_buttons[0], Qt.LeftButton)
    assert blocker.args == ["Open a course first."]


# --------------------------------------------------------------------------
# Real interaction that persists: marking today's attendance "Present" adds
# a real AttendanceEntry row, verified via a fresh store read from disk.
# --------------------------------------------------------------------------
def test_mark_attendance_present_button_persists_new_entry_to_workbook(qtbot, store, workbook_path):
    page = CourseDetailPage(store)
    qtbot.addWidget(page)

    course_id = 1
    page.show_course(course_id)
    assert store.list_attendance(course_id) == [], "expected no attendance rows yet for this course"

    present_buttons = _buttons_with_text(page, "Present")
    assert len(present_buttons) == 1

    qtbot.mouseClick(present_buttons[0], Qt.LeftButton)

    today = date.today()
    in_memory = store.list_attendance(course_id)
    assert len(in_memory) == 1
    assert in_memory[0].date == today
    assert in_memory[0].status == "Present"

    # update/add only marks the store dirty and arms a debounced autosave;
    # force a synchronous save so the on-disk copy reflects the click.
    store.save(force=True)

    fresh = ExcelStore(workbook_path)
    fresh.load()
    fresh_entries = fresh.list_attendance(course_id)
    assert len(fresh_entries) == 1
    assert fresh_entries[0].status == "Present"
    assert fresh_entries[0].date == today
