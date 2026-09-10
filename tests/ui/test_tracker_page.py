"""Tests for app.ui.tracker_page.TrackerPage - the cross-course Exams /
Deadlines / Pre-labs / Week-view tabs (build spec §5.4).

Facts asserted about "what got rendered" are computed from the same
``store``/``empty_store`` fixture the page itself reads from, since the
supplied workbook is real Fall 2026 semester data whose "today"-relative
facts (overdue deadlines, the current term week, ...) depend on when this
suite runs.
"""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QLabel, QPushButton

from app import models
from app.excel_store import ExcelStore
from app.services import scheduler
from app.ui.tracker_page import TrackerPage, _EXAM_TYPES

EXPECTED_TAB_TITLES = ["Exams", "Deadlines", "Pre-labs", "Week view"]
_TAB_EXAMS, _TAB_DEADLINES, _TAB_PRELABS, _TAB_WEEK = 0, 1, 2, 3


def _button_in_tab(page: TrackerPage, tab_index: int, text: str) -> QPushButton:
    """The one QPushButton with this exact text inside one tab's widget -
    several buttons share a label across tabs (e.g. "Edit" appears on both
    the Exams and Pre-labs tabs), so callers must scope to a tab.

    Also switches to that tab: a QTabWidget never lays out a non-current
    page's children, so a widget on a still-hidden tab keeps a stale (0, 0,
    640, 480) geometry - harmless for a QPushButton (its whole rect is one
    hit target, so a click "works" there by accident) but it silently
    swallows a QCheckBox click (only its small indicator sub-rect is the
    real hit target). Switching tabs first - which is what a real user
    does before clicking anything on it - makes every click in this file a
    genuine, correctly-hit-tested interaction rather than a lucky one.
    """
    page.tabs.setCurrentIndex(tab_index)
    tab_widget = page.tabs.widget(tab_index)
    matches = [b for b in tab_widget.findChildren(QPushButton) if b.text() == text]
    assert len(matches) == 1, (
        f"expected exactly one {text!r} button in tab {tab_index} "
        f"({page.tabs.tabText(tab_index)!r}), found {len(matches)}"
    )
    return matches[0]


def _row_index_for(model, id_attr: str, id_value) -> int:
    for i in range(model.rowCount()):
        if getattr(model.row_object(i), id_attr) == id_value:
            return i
    raise AssertionError(f"no row with {id_attr}={id_value!r} in model")


# --------------------------------------------------------------------------
# Baseline: construct with the real store, refresh(), check concrete facts.
# --------------------------------------------------------------------------
def test_tracker_page_renders_real_workbook_data_across_all_tabs(qtbot, store):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    page.refresh()  # idempotent - must not raise

    tab_titles = [page.tabs.tabText(i) for i in range(page.tabs.count())]
    assert tab_titles == EXPECTED_TAB_TITLES

    all_assessments = store.list_assessments()
    expected_exams = [a for a in all_assessments if a.type in _EXAM_TYPES]
    # Pinned to the real supplied Fall 2026 workbook (6 courses, 20
    # assessments, 19 of exam-type) as well as re-derived dynamically, so a
    # real regression in the filter logic fails even if someone edits the
    # workbook later and forgets to update this literal.
    assert len(expected_exams) == 19
    assert page.exams_model.rowCount() == len(expected_exams)

    expected_deadlines = [a for a in all_assessments if a.status not in ("Submitted", "Graded")]
    assert page.deadlines_model.rowCount() == len(expected_deadlines)

    expected_prelabs = [p for p in store.list_prelabs() if p.lab_type != "None"]
    assert page.prelab_model.rowCount() == len(expected_prelabs)
    assert page.prelab_course_combo.count() == len(store.list_courses()) + 1
    assert page.prelab_course_combo.itemText(0) == "All courses"

    # A specific known course code from the real workbook shows up as the
    # resolved "Course" column value on at least one exam row.
    course_map = {c.course_id: c.code for c in store.list_courses()}
    assert "CMTC2341" in course_map.values()
    rendered_codes = {
        course_map.get(page.exams_model.row_object(i).course_id, "?")
        for i in range(page.exams_model.rowCount())
    }
    assert "CMTC2341" in rendered_codes

    # Dynamic per-course buttons/toolbars from the build spec are present.
    for tab_idx, text in (
        (_TAB_EXAMS, "+ Add"), (_TAB_EXAMS, "Edit"), (_TAB_EXAMS, "Delete"),
        (_TAB_EXAMS, "Generate roadmap for this exam"),
        (_TAB_DEADLINES, "Mark submitted"), (_TAB_DEADLINES, "Enter grade"),
        (_TAB_PRELABS, "Edit"), (_TAB_PRELABS, "Mark done"),
        (_TAB_WEEK, "◀ Previous week"), (_TAB_WEEK, "Next week ▶"),
    ):
        _button_in_tab(page, tab_idx, text)  # raises if not exactly one


def test_tracker_page_with_empty_store_shows_zero_rows_and_still_builds_week_view(qtbot, empty_store):
    page = TrackerPage(empty_store)
    qtbot.addWidget(page)

    page.refresh()

    assert page.exams_model.rowCount() == 0
    assert page.deadlines_model.rowCount() == 0
    assert page.prelab_model.rowCount() == 0
    assert page.prelab_pinned_model.rowCount() == 0
    assert page.prelab_course_combo.count() == 1
    assert page.prelab_course_combo.itemText(0) == "All courses"
    assert page.week_label.text().startswith("Week ")
    # Day-header labels for a 5-day week are still built with zero data rows.
    assert len(page.week_grid_host.findChildren(QLabel)) >= 5


# --------------------------------------------------------------------------
# Tab 1 - Exams
# --------------------------------------------------------------------------
def test_toggle_show_all_types_checkbox_includes_and_excludes_project_type(qtbot, store):
    page = TrackerPage(store)
    qtbot.addWidget(page)
    page.tabs.setCurrentIndex(_TAB_EXAMS)

    all_assessments = store.list_assessments()
    expected_exam_only = [a for a in all_assessments if a.type in _EXAM_TYPES]
    assert any(a.type == "Project" for a in all_assessments), (
        "expected the real workbook to contain a non-exam-type assessment "
        "(e.g. Project) to exercise the 'show all types' filter"
    )

    assert page._exams_show_all is False
    assert page.exams_model.rowCount() == len(expected_exam_only)

    qtbot.mouseClick(page.show_all_types_cb, Qt.LeftButton)

    assert page._exams_show_all is True
    assert page.exams_model.rowCount() == len(all_assessments)
    assert any(
        page.exams_model.row_object(i).type == "Project"
        for i in range(page.exams_model.rowCount())
    )

    qtbot.mouseClick(page.show_all_types_cb, Qt.LeftButton)

    assert page._exams_show_all is False
    assert page.exams_model.rowCount() == len(expected_exam_only)


def test_add_exam_with_no_courses_emits_status_message(qtbot, empty_store):
    page = TrackerPage(empty_store)
    qtbot.addWidget(page)

    add_btn = _button_in_tab(page, _TAB_EXAMS, "+ Add")
    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(add_btn, Qt.LeftButton)
    assert blocker.args == ["Add a course first (Courses page)."]
    assert empty_store.list_assessments() == []


def test_add_exam_persists_new_assessment_and_grows_exam_count(qtbot, store, workbook_path, monkeypatch):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    before_ids = {a.assessment_id for a in store.list_assessments()}
    exams_before = page.exams_model.rowCount()
    target_course = store.list_courses()[0]

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (target_course.code, True)))

    def fake_edit_row(parent, title, columns, obj, exclude_attrs=None, multiline_attrs=None):
        obj.title = "Pop Quiz — added by test"
        obj.due_date = date(2026, 10, 5)
        obj.max_score = 20.0
        return obj

    monkeypatch.setattr("app.ui.dialogs.edit_row", fake_edit_row)

    add_btn = _button_in_tab(page, _TAB_EXAMS, "+ Add")
    qtbot.mouseClick(add_btn, Qt.LeftButton)

    new_ids = {a.assessment_id for a in store.list_assessments()} - before_ids
    assert len(new_ids) == 1
    new_assessment = store.get_assessment(new_ids.pop())
    assert new_assessment.course_id == target_course.course_id
    assert new_assessment.title == "Pop Quiz — added by test"
    assert new_assessment.due_date == date(2026, 10, 5)
    assert new_assessment.type == "Quiz"  # models.Assessment(...) default, untouched by the fake dialog

    # "Quiz" is one of _EXAM_TYPES, so the default (non-"show all") exams
    # tab must have grown by exactly one row.
    assert page.exams_model.rowCount() == exams_before + 1

    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    assert fresh.get_assessment(new_assessment.assessment_id).title == "Pop Quiz — added by test"


def test_edit_exam_updates_selected_assessment_and_persists(qtbot, store, workbook_path, monkeypatch):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    row_idx = _row_index_for(page.exams_model, "assessment_id", 1)
    page.exams_view.selectRow(row_idx)

    def fake_edit_row(parent, title, columns, obj, exclude_attrs=None, multiline_attrs=None):
        obj.title = "Theory Assessment #1 (RESCHEDULED)"
        obj.score = 87.5
        obj.status = "Graded"
        return obj

    monkeypatch.setattr("app.ui.dialogs.edit_row", fake_edit_row)

    edit_btn = _button_in_tab(page, _TAB_EXAMS, "Edit")
    qtbot.mouseClick(edit_btn, Qt.LeftButton)

    updated = store.get_assessment(1)
    assert updated.title == "Theory Assessment #1 (RESCHEDULED)"
    assert updated.score == 87.5
    assert updated.status == "Graded"

    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    assert fresh.get_assessment(1).title == "Theory Assessment #1 (RESCHEDULED)"


def test_delete_exam_confirmed_removes_assessment_from_store(qtbot, store, monkeypatch):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    row_idx = _row_index_for(page.exams_model, "assessment_id", 2)
    page.exams_view.selectRow(row_idx)
    exams_before = page.exams_model.rowCount()

    monkeypatch.setattr("app.ui.widgets.confirm", lambda *a, **k: True)

    delete_btn = _button_in_tab(page, _TAB_EXAMS, "Delete")
    qtbot.mouseClick(delete_btn, Qt.LeftButton)

    assert store.get_assessment(2) is None
    assert page.exams_model.rowCount() == exams_before - 1


def test_delete_exam_declined_keeps_assessment(qtbot, store, monkeypatch):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    row_idx = _row_index_for(page.exams_model, "assessment_id", 2)
    page.exams_view.selectRow(row_idx)
    exams_before = page.exams_model.rowCount()

    monkeypatch.setattr("app.ui.widgets.confirm", lambda *a, **k: False)

    delete_btn = _button_in_tab(page, _TAB_EXAMS, "Delete")
    qtbot.mouseClick(delete_btn, Qt.LeftButton)

    assert store.get_assessment(2) is not None
    assert page.exams_model.rowCount() == exams_before


def test_exam_actions_without_selection_emit_status_message(qtbot, store):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    for text, expected in (
        ("Edit", "Select an assessment first."),
        ("Delete", "Select an assessment first."),
        ("Generate roadmap for this exam", "Select an exam first."),
    ):
        btn = _button_in_tab(page, _TAB_EXAMS, text)
        with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
            qtbot.mouseClick(btn, Qt.LeftButton)
        assert blocker.args == [expected]


def test_generate_roadmap_for_exam_creates_expected_auto_roadmap_items(qtbot, store):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    assessment = store.get_assessment(1)
    assert assessment.due_date is not None
    assert store.list_roadmap(assessment.course_id) == []

    topics = store.list_topics(assessment.course_id)
    week1_monday = store.setting_date("week1_monday")
    expected_items = scheduler.generate_roadmap(
        assessment.course_id, topics, week1_monday, date.today(), assessment.due_date,
        target_topic_ids=set(assessment.topic_ids),
    )
    assert expected_items, "expected at least one generated milestone for this real exam"

    row_idx = _row_index_for(page.exams_model, "assessment_id", 1)
    page.exams_view.selectRow(row_idx)

    roadmap_btn = _button_in_tab(page, _TAB_EXAMS, "Generate roadmap for this exam")
    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(roadmap_btn, Qt.LeftButton)

    assert blocker.args == [
        f"Roadmap generated for {assessment.title} ({len(expected_items)} milestone(s))."
    ]

    stored = store.list_roadmap(assessment.course_id)
    assert len(stored) == len(expected_items)
    assert all(r.auto_generated for r in stored)
    assert [r.milestone for r in stored] == [i.milestone for i in expected_items]


def test_generate_roadmap_without_due_date_emits_status_message_and_adds_nothing(qtbot, empty_store):
    course = empty_store.add_course(models.Course(code="TEST101", name="Test Course"))
    assessment = empty_store.add_assessment(models.Assessment(
        course_id=course.course_id, type="Midterm", title="Midterm with no date yet",
    ))
    assert assessment.due_date is None

    page = TrackerPage(empty_store)
    qtbot.addWidget(page)

    row_idx = _row_index_for(page.exams_model, "assessment_id", assessment.assessment_id)
    page.exams_view.selectRow(row_idx)

    roadmap_btn = _button_in_tab(page, _TAB_EXAMS, "Generate roadmap for this exam")
    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(roadmap_btn, Qt.LeftButton)

    assert blocker.args == [
        "This exam has no due date yet — set one before generating a roadmap."
    ]
    assert empty_store.list_roadmap(course.course_id) == []


# --------------------------------------------------------------------------
# Tab 2 - Deadlines
# --------------------------------------------------------------------------
def test_mark_submitted_button_updates_status_persists_and_filters_row(qtbot, store, workbook_path):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    deadlines_before = page.deadlines_model.rowCount()
    target = page.deadlines_model.row_object(0)
    target_id = target.assessment_id
    page.deadlines_view.selectRow(0)

    mark_btn = _button_in_tab(page, _TAB_DEADLINES, "Mark submitted")
    qtbot.mouseClick(mark_btn, Qt.LeftButton)

    updated = store.get_assessment(target_id)
    assert updated.status == "Submitted"
    assert page.deadlines_model.rowCount() == deadlines_before - 1
    assert all(
        page.deadlines_model.row_object(i).assessment_id != target_id
        for i in range(page.deadlines_model.rowCount())
    )

    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    assert fresh.get_assessment(target_id).status == "Submitted"


def test_enter_grade_button_updates_score_status_persists_and_filters_row(qtbot, store, workbook_path, monkeypatch):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    deadlines_before = page.deadlines_model.rowCount()
    target = page.deadlines_model.row_object(1)
    target_id = target.assessment_id
    page.deadlines_view.selectRow(1)

    monkeypatch.setattr(QInputDialog, "getDouble", staticmethod(lambda *a, **k: (91.5, True)))

    grade_btn = _button_in_tab(page, _TAB_DEADLINES, "Enter grade")
    qtbot.mouseClick(grade_btn, Qt.LeftButton)

    updated = store.get_assessment(target_id)
    assert updated.score == 91.5
    assert updated.status == "Graded"
    assert page.deadlines_model.rowCount() == deadlines_before - 1

    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    assert fresh.get_assessment(target_id).score == 91.5
    assert fresh.get_assessment(target_id).status == "Graded"


def test_enter_grade_cancelled_leaves_assessment_unchanged(qtbot, store, monkeypatch):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    target = page.deadlines_model.row_object(0)
    target_id = target.assessment_id
    original_status, original_score = target.status, target.score
    page.deadlines_view.selectRow(0)

    monkeypatch.setattr(QInputDialog, "getDouble", staticmethod(lambda *a, **k: (99.0, False)))

    grade_btn = _button_in_tab(page, _TAB_DEADLINES, "Enter grade")
    qtbot.mouseClick(grade_btn, Qt.LeftButton)

    updated = store.get_assessment(target_id)
    assert updated.status == original_status
    assert updated.score == original_score


def test_deadline_actions_without_selection_emit_status_message(qtbot, store):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    for text in ("Mark submitted", "Enter grade"):
        btn = _button_in_tab(page, _TAB_DEADLINES, text)
        with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
            qtbot.mouseClick(btn, Qt.LeftButton)
        assert blocker.args == ["Select a deadline first."]


def test_deadline_hours_warning_column_flags_underfunded_assessments(qtbot, store):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    warn_col = next(i for i, c in enumerate(page.deadlines_model.columns) if c.header == "Hrs ⚠")

    saw_warning = False
    for row in range(page.deadlines_model.rowCount()):
        obj = page.deadlines_model.row_object(row)
        free = page._free_hours(obj.due_date)
        expected = "⚠" if (obj.estimated_hours and obj.estimated_hours > free) else ""
        actual = page.deadlines_model.data(page.deadlines_model.index(row, warn_col))
        assert actual == expected
        saw_warning = saw_warning or actual == "⚠"

    assert saw_warning, (
        "expected at least one deadline whose estimated hours exceed the "
        "free hours remaining before its due date in the real workbook"
    )


# --------------------------------------------------------------------------
# Tab 3 - Pre-labs
# --------------------------------------------------------------------------
def test_toggle_show_none_checkbox_includes_and_excludes_none_type_prelabs(qtbot, store):
    page = TrackerPage(store)
    qtbot.addWidget(page)
    page.tabs.setCurrentIndex(_TAB_PRELABS)

    all_prelabs = store.list_prelabs()
    non_none = [p for p in all_prelabs if p.lab_type != "None"]
    assert any(p.lab_type == "None" for p in all_prelabs), (
        "expected the real workbook to contain a holiday/no-lab prelab row"
    )

    assert page._prelab_show_none is False
    assert page.prelab_model.rowCount() == len(non_none)

    qtbot.mouseClick(page.prelab_show_none_cb, Qt.LeftButton)

    assert page._prelab_show_none is True
    assert page.prelab_model.rowCount() == len(all_prelabs)

    qtbot.mouseClick(page.prelab_show_none_cb, Qt.LeftButton)

    assert page._prelab_show_none is False
    assert page.prelab_model.rowCount() == len(non_none)


def test_prelab_course_filter_combo_key_selection_filters_rows_to_that_course(qtbot, store):
    page = TrackerPage(store)
    qtbot.addWidget(page)
    page.tabs.setCurrentIndex(_TAB_PRELABS)

    assert page.prelab_course_combo.count() == len(store.list_courses()) + 1
    assert page.prelab_course_combo.currentText() == "All courses"
    assert page._prelab_course_filter == 0

    page.prelab_course_combo.setFocus()
    qtbot.keyClick(page.prelab_course_combo, Qt.Key_Down)

    target_course_id = page.prelab_course_combo.currentData()
    assert target_course_id, "expected the down-arrow to move off the 'All courses' item"
    assert page._prelab_course_filter == target_course_id

    expected = [p for p in store.list_prelabs(target_course_id) if p.lab_type != "None"]
    assert expected, "expected the first real course to have at least one pre-lab"
    assert page.prelab_model.rowCount() == len(expected)
    assert all(
        page.prelab_model.row_object(i).course_id == target_course_id
        for i in range(page.prelab_model.rowCount())
    )


def test_mark_prelab_done_button_persists_completion(qtbot, store, workbook_path):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    target = next(p for p in store.list_prelabs() if not p.completed and p.lab_type != "None")
    row_idx = _row_index_for(page.prelab_model, "prelab_id", target.prelab_id)
    page.prelab_view.selectRow(row_idx)

    done_btn = _button_in_tab(page, _TAB_PRELABS, "Mark done")
    qtbot.mouseClick(done_btn, Qt.LeftButton)

    updated = store.get_prelab(target.prelab_id)
    assert updated.completed is True
    assert updated.completed_on == date.today()

    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    fresh_p = fresh.get_prelab(target.prelab_id)
    assert fresh_p.completed is True
    assert fresh_p.completed_on == date.today()


def test_edit_prelab_button_persists_change(qtbot, store, monkeypatch):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    target = store.list_prelabs()[0]
    row_idx = _row_index_for(page.prelab_model, "prelab_id", target.prelab_id)
    page.prelab_view.selectRow(row_idx)

    def fake_edit_row(parent, title, columns, obj, exclude_attrs=None, multiline_attrs=None):
        obj.room = "TEST-ROOM-42"
        return obj

    monkeypatch.setattr("app.ui.dialogs.edit_row", fake_edit_row)

    edit_btn = _button_in_tab(page, _TAB_PRELABS, "Edit")
    qtbot.mouseClick(edit_btn, Qt.LeftButton)

    updated = store.get_prelab(target.prelab_id)
    assert updated.room == "TEST-ROOM-42"


def test_prelab_actions_without_selection_emit_status_message(qtbot, store):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    for text in ("Edit", "Mark done"):
        btn = _button_in_tab(page, _TAB_PRELABS, text)
        with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
            qtbot.mouseClick(btn, Qt.LeftButton)
        assert blocker.args == ["Select a pre-lab first."]


# --------------------------------------------------------------------------
# Tab 4 - Week view
# --------------------------------------------------------------------------
def test_week_view_defaults_to_current_term_week_with_a_known_agenda_item(qtbot, store):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    week1_monday = store.setting_date("week1_monday")
    expected_monday = scheduler.week_start(
        scheduler.term_week_number(date.today(), week1_monday), week1_monday
    )
    expected_friday = expected_monday + timedelta(days=4)
    expected_wk_num = scheduler.term_week_number(expected_monday, week1_monday)

    assert page._week_monday == expected_monday
    assert page.week_label.text() == (
        f"Week {expected_wk_num}: {expected_monday.strftime('%b %d')} "
        f"– {expected_friday.strftime('%b %d')}"
    )

    view = scheduler.week_view(
        expected_monday, store.list_schedule(), store.list_holidays(),
        store.list_prelabs(), store.list_assessments(),
    )
    course_map = {c.course_id: c for c in store.list_courses()}
    some_day_with_items = next((d for d, items in view.items() if items), None)
    assert some_day_with_items is not None, "expected at least one agenda item in the current term week"
    an_item = view[some_day_with_items][0]
    course = course_map.get(an_item.course_id)
    code = course.code if course else "?"
    t_s = an_item.start_time.strftime("%H:%M") if an_item.start_time else ""
    expected_text = f"{t_s} {code} {an_item.title}".strip()
    if an_item.room:
        expected_text += f" · {an_item.room}"

    all_grid_labels = {l.text() for l in page.week_grid_host.findChildren(QLabel)}
    assert expected_text in all_grid_labels


def test_next_and_previous_week_buttons_navigate_by_seven_days(qtbot, store):
    page = TrackerPage(store)
    qtbot.addWidget(page)

    starting_monday = page._week_monday
    starting_label = page.week_label.text()

    next_btn = _button_in_tab(page, _TAB_WEEK, "Next week ▶")
    qtbot.mouseClick(next_btn, Qt.LeftButton)

    assert page._week_monday == starting_monday + timedelta(days=7)
    assert page.week_label.text() != starting_label

    prev_btn = _button_in_tab(page, _TAB_WEEK, "◀ Previous week")
    qtbot.mouseClick(prev_btn, Qt.LeftButton)
    qtbot.mouseClick(prev_btn, Qt.LeftButton)

    assert page._week_monday == starting_monday - timedelta(days=7)
