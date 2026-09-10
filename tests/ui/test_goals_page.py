"""Tests for app.ui.goals_page.GoalsPage - a table of Goals rows (add/edit/
delete through the generic ``dialogs.edit_row`` form) plus one progress bar
per goal below it (build spec §5.10).

``refresh()`` is the page's whole reason for existing: it recomputes every
goal's ``current_value``/``status`` from the live workbook data (per
§6.9/§6.10) before redrawing either the table or the progress-bar cards, so
most of these tests check that recomputation against independently derived
expectations rather than merely asserting "no exception". The real supplied
Fall 2026 workbook happens to have an empty StudyLog sheet and no graded
assessments yet, so every metric this page can compute currently lands on
0.0 or None - a fact re-derived from the store in each test rather than
hardcoded, and asserted to be exactly the value ``progress.compute_goal_progress``
would produce for that data.
"""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QLabel, QPushButton

from app.excel_store import ExcelStore
from app.services import progress
from app.ui.goals_page import GoalsPage

_NONE_CHOICE = "(none - semester/weekly goal)"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _button(page: GoalsPage, text: str) -> QPushButton:
    matches = [b for b in page.findChildren(QPushButton) if b.text() == text]
    assert len(matches) == 1, f"expected exactly one {text!r} button, found {len(matches)}"
    return matches[0]


def _row_index_for(model, id_attr: str, id_value) -> int:
    for i in range(model.rowCount()):
        if getattr(model.row_object(i), id_attr) == id_value:
            return i
    raise AssertionError(f"no row with {id_attr}={id_value!r} in model")


def _col_index(model, attr: str) -> int:
    return next(i for i, c in enumerate(model.columns) if c.attr == attr)


def _expected_weekly_hours(study_log, today: date) -> float:
    """Independent re-implementation of progress._week_bounds/study_hours_per_week
    (Monday-start week) so the test doesn't just call the code under test."""
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    minutes = sum(e.minutes or 0 for e in study_log if e.date and start <= e.date <= end)
    return round(minutes / 60, 2)


# --------------------------------------------------------------------------
# Baseline: construct with the real store, refresh(), check concrete facts.
# --------------------------------------------------------------------------
def test_goals_page_renders_real_workbook_data_baseline(qtbot, store):
    page = GoalsPage(store)
    qtbot.addWidget(page)

    page.refresh()  # idempotent - must not raise

    goals = store.list_goals()
    assert len(goals) == 5  # the real supplied Fall 2026 workbook seeds 5 goals
    assert page.table_model.rowCount() == 5

    for text in ("+ Add goal", "Edit", "Delete"):
        _button(page, text)  # raises if not exactly one

    section_labels = {l.text() for l in page.findChildren(QLabel)}
    assert "Goals" in section_labels
    assert "Progress" in section_labels

    # A specific, known course-scoped goal resolves its Course column to a
    # real course code from the store, not "?" or a raw id.
    goal4 = store.get_goal(4)
    assert store.get_course(goal4.course_id).code == "CNTR2371"
    row_idx = _row_index_for(page.table_model, "goal_id", 4)
    assert page.table_model.data(page.table_model.index(row_idx, 0)) == "CNTR2371"

    # One progress-bar card per goal, each carrying its own description.
    assert page._cards_layout.count() == len(goals)
    card_labels = [l.text() for l in page._cards_host.findChildren(QLabel)]
    assert any("Study streak of 30 days" in t for t in card_labels)
    assert any("CNTR2371 >= 80%" in t for t in card_labels)


# --------------------------------------------------------------------------
# refresh()'s core job: recompute current_value/status from live data.
# --------------------------------------------------------------------------
def test_refresh_recomputes_current_value_and_status_for_weekly_and_streak_goals(qtbot, store):
    study_log = store.list_study_log()
    assert study_log == [], "expected the real supplied workbook to have zero StudyLog rows"
    expected_hours = _expected_weekly_hours(study_log, date.today())
    assert expected_hours == 0.0  # no logged study sessions at all -> zero hours any week

    page = GoalsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    goals_by_id = {g.goal_id: g for g in store.list_goals()}  # fresh read from the store

    weekly_goal = goals_by_id[1]
    assert weekly_goal.metric == "study_hours_per_week"
    assert weekly_goal.current_value is not None
    assert weekly_goal.current_value == pytest.approx(expected_hours)
    assert weekly_goal.status == progress.goal_status(weekly_goal)
    assert weekly_goal.status == "Active"

    streak_goal = goals_by_id[3]
    assert streak_goal.metric == "streak_days"
    current, _best = progress.study_streak(study_log, date.today())
    assert streak_goal.current_value == pytest.approx(float(current))
    assert streak_goal.current_value == 0.0
    assert streak_goal.status == progress.goal_status(streak_goal)


def test_refresh_leaves_ungraded_course_goals_at_none_but_still_active(qtbot, store):
    """grade_at_least goals for CNTR2371/INST2340 stay current_value=None
    because nothing is graded yet in the real data - goal_status must still
    resolve them to "Active" (not Achieved, not Missed) via their own rules."""
    page = GoalsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    for goal_id, course_code in ((4, "CNTR2371"), (5, "INST2340")):
        goal = store.get_goal(goal_id)
        assert goal.metric == "grade_at_least"
        assert store.get_course(goal.course_id).code == course_code
        assessments = store.list_assessments(goal.course_id)
        assert assessments, "expected the real workbook to have assessments for this course"
        assert all(a.score is None for a in assessments), "expected nothing graded yet"
        assert goal.current_value is None
        assert goal.status == progress.goal_status(goal)
        assert goal.status == "Active"


# --------------------------------------------------------------------------
# "+ Add goal": course picker (QInputDialog) then the generic edit_row form.
# --------------------------------------------------------------------------
def test_add_goal_weekly_scope_persists_new_goal_via_real_click(qtbot, store, workbook_path, monkeypatch):
    page = GoalsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    before_ids = {g.goal_id for g in store.list_goals()}
    rows_before = page.table_model.rowCount()

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (_NONE_CHOICE, True)))

    captured = {}

    def fake_edit_row(parent, title, columns, obj, exclude_attrs=None, multiline_attrs=None):
        captured["pre_fill"] = dataclasses.replace(obj)
        obj.description = "Log 15h this week"
        obj.metric = "study_hours_per_week"
        obj.target_value = 15.0
        return obj

    monkeypatch.setattr("app.ui.dialogs.edit_row", fake_edit_row)

    qtbot.mouseClick(_button(page, "+ Add goal"), Qt.LeftButton)

    # The page must have pre-filled a Weekly, course-less, Active goal
    # before ever opening the form.
    pre_fill = captured["pre_fill"]
    assert pre_fill.scope == "Weekly"
    assert pre_fill.course_id is None
    assert pre_fill.status == "Active"
    assert pre_fill.start_date == date.today()

    new_ids = {g.goal_id for g in store.list_goals()} - before_ids
    assert len(new_ids) == 1
    added = store.get_goal(new_ids.pop())
    assert added.description == "Log 15h this week"
    assert added.target_value == 15.0
    assert added.course_id is None
    # refresh() ran after the add: recomputed immediately, not left as None.
    assert added.current_value == 0.0
    assert added.status == progress.goal_status(added)

    assert page.table_model.rowCount() == rows_before + 1
    row_idx = _row_index_for(page.table_model, "goal_id", added.goal_id)
    assert page.table_model.data(page.table_model.index(row_idx, 0)) == "—"

    # And it reaches the on-disk workbook once saved.
    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    assert fresh.get_goal(added.goal_id).description == "Log 15h this week"


def test_add_goal_course_scoped_uses_the_picked_course(qtbot, store, monkeypatch):
    page = GoalsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    course = next(c for c in store.list_courses() if c.code == "INST2310")
    mastered_before = [t for t in store.list_topics(course.course_id) if t.status == "Mastered"]
    assert mastered_before == [], "expected zero Mastered topics for this course in the real data"

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (course.code, True)))

    def fake_edit_row(parent, title, columns, obj, exclude_attrs=None, multiline_attrs=None):
        assert obj.scope == "Course"
        assert obj.course_id == course.course_id
        obj.description = "Master every INST2310 topic"
        obj.metric = "topics_mastered"
        obj.target_value = 3.0
        return obj

    monkeypatch.setattr("app.ui.dialogs.edit_row", fake_edit_row)

    before_ids = {g.goal_id for g in store.list_goals()}
    qtbot.mouseClick(_button(page, "+ Add goal"), Qt.LeftButton)

    new_id = ({g.goal_id for g in store.list_goals()} - before_ids).pop()
    added = store.get_goal(new_id)
    assert added.course_id == course.course_id
    assert added.scope == "Course"
    assert added.current_value == 0.0  # zero Mastered topics for this course
    assert added.status == progress.goal_status(added)

    row_idx = _row_index_for(page.table_model, "goal_id", added.goal_id)
    assert page.table_model.data(page.table_model.index(row_idx, 0)) == "INST2310"


def test_add_goal_cancelled_course_picker_never_opens_edit_dialog_or_adds(qtbot, store, monkeypatch):
    page = GoalsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    before = store.list_goals()

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (_NONE_CHOICE, False)))

    def fail_if_called(*a, **k):
        raise AssertionError("edit_row must not be called when the course picker is cancelled")

    monkeypatch.setattr("app.ui.dialogs.edit_row", fail_if_called)

    qtbot.mouseClick(_button(page, "+ Add goal"), Qt.LeftButton)

    assert store.list_goals() == before
    assert page.table_model.rowCount() == len(before)


# --------------------------------------------------------------------------
# Edit
# --------------------------------------------------------------------------
def test_edit_goal_button_updates_selected_goal_and_persists(qtbot, store, workbook_path, monkeypatch):
    page = GoalsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    row_idx = _row_index_for(page.table_model, "goal_id", 5)
    page.table_view.selectRow(row_idx)

    captured = {}

    def fake_edit_row(parent, title, columns, obj, exclude_attrs=None, multiline_attrs=None):
        captured["title"] = title
        obj.target_value = 90.0
        obj.description = "INST2340 >= 90% (raised)"
        return obj

    monkeypatch.setattr("app.ui.dialogs.edit_row", fake_edit_row)

    qtbot.mouseClick(_button(page, "Edit"), Qt.LeftButton)

    assert captured["title"] == "Edit goal — INST2340"
    updated = store.get_goal(5)
    assert updated.target_value == 90.0
    assert updated.description == "INST2340 >= 90% (raised)"

    row_idx_after = _row_index_for(page.table_model, "goal_id", 5)
    target_col = _col_index(page.table_model, "target_value")
    assert page.table_model.data(page.table_model.index(row_idx_after, target_col)) == 90.0

    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    assert fresh.get_goal(5).target_value == 90.0


def test_edit_goal_without_selection_emits_status_message(qtbot, store):
    page = GoalsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    before = store.list_goals()
    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Edit"), Qt.LeftButton)
    assert blocker.args == ["Select a goal first."]
    assert store.list_goals() == before


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------
def test_delete_goal_confirmed_removes_it_from_store_and_table(qtbot, store, monkeypatch):
    page = GoalsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    row_idx = _row_index_for(page.table_model, "goal_id", 2)
    page.table_view.selectRow(row_idx)
    rows_before = page.table_model.rowCount()

    monkeypatch.setattr("app.ui.widgets.confirm", lambda *a, **k: True)

    qtbot.mouseClick(_button(page, "Delete"), Qt.LeftButton)

    assert store.get_goal(2) is None
    assert all(g.goal_id != 2 for g in store.list_goals())
    assert page.table_model.rowCount() == rows_before - 1


def test_delete_goal_declined_confirmation_leaves_it_untouched(qtbot, store, monkeypatch):
    page = GoalsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    row_idx = _row_index_for(page.table_model, "goal_id", 2)
    page.table_view.selectRow(row_idx)
    rows_before = page.table_model.rowCount()

    monkeypatch.setattr("app.ui.widgets.confirm", lambda *a, **k: False)

    qtbot.mouseClick(_button(page, "Delete"), Qt.LeftButton)

    assert store.get_goal(2) is not None
    assert page.table_model.rowCount() == rows_before


def test_delete_goal_without_selection_emits_status_message(qtbot, store):
    page = GoalsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    before = store.list_goals()
    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(_button(page, "Delete"), Qt.LeftButton)
    assert blocker.args == ["Select a goal first."]
    assert store.list_goals() == before


# --------------------------------------------------------------------------
# Inline cell edit (the table's own editable "Status"/etc. columns).
# --------------------------------------------------------------------------
def test_inline_status_cell_edit_persists_through_on_cell_edited(qtbot, store):
    page = GoalsPage(store)
    qtbot.addWidget(page)
    page.refresh()

    row_idx = _row_index_for(page.table_model, "goal_id", 3)
    status_col = _col_index(page.table_model, "status")
    index = page.table_model.index(row_idx, status_col)

    ok = page.table_model.setData(index, "Missed", Qt.EditRole)

    assert ok is True
    assert store.get_goal(3).status == "Missed"


# --------------------------------------------------------------------------
# Failure path
# --------------------------------------------------------------------------
def test_refresh_when_goals_load_fails_emits_status_message_and_shows_empty_table(qtbot, store, monkeypatch):
    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "list_goals", _raise)

    page = GoalsPage(store)
    qtbot.addWidget(page)

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        page.refresh()
    assert blocker.args == ["Could not load goals."]

    assert page.table_model.rowCount() == 0
    assert any("No goals yet" in l.text() for l in page._cards_host.findChildren(QLabel))


# --------------------------------------------------------------------------
# Edge case: a brand-new, completely empty workbook (zero goals, zero
# courses) - both the empty-state placeholder and adding the first goal.
# --------------------------------------------------------------------------
def test_empty_store_shows_placeholder_and_supports_adding_the_first_goal(qtbot, empty_store, monkeypatch):
    page = GoalsPage(empty_store)
    qtbot.addWidget(page)
    page.refresh()

    assert empty_store.list_goals() == []
    assert page.table_model.rowCount() == 0
    assert any("No goals yet" in l.text() for l in page._cards_host.findChildren(QLabel))

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (_NONE_CHOICE, True)))

    def fake_edit_row(parent, title, columns, obj, exclude_attrs=None, multiline_attrs=None):
        obj.description = "First ever goal"
        obj.metric = "study_hours_per_week"
        obj.target_value = 5.0
        return obj

    monkeypatch.setattr("app.ui.dialogs.edit_row", fake_edit_row)

    qtbot.mouseClick(_button(page, "+ Add goal"), Qt.LeftButton)

    goals = empty_store.list_goals()
    assert len(goals) == 1
    assert goals[0].goal_id == 1
    assert goals[0].description == "First ever goal"
    # An empty workbook also has an empty StudyLog -> recomputes to 0.0.
    assert goals[0].current_value == 0.0
    assert page.table_model.rowCount() == 1
    assert not any("No goals yet" in l.text() for l in page._cards_host.findChildren(QLabel))
