"""
Tests for app.ui.roadmap_page.RoadmapPage (build spec §5.5).

Covers: baseline construction/refresh against the real six-course workbook,
a real QComboBox course selection, the "Generate from syllabus" flow
(creates auto_generated roadmap rows, and a second run replaces the old
auto rows without touching a manually-added one), the Add/Edit/Delete/Mark
slipped/Shift remaining toolbar actions via real QPushButton clicks, the
statusMessage signal, and the empty-workbook edge case.

Modal dialogs (RowEditDialog via dialogs.edit_row, QMessageBox via
widgets.confirm, QInputDialog.getInt) are monkeypatched before any click
that would otherwise open them - see the safety rules in the shared
conftest docstring.
"""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsTextItem, QInputDialog, QPushButton

from app import models
from app.services import scheduler
from app.ui import dialogs, table_models, widgets
from app.ui.roadmap_page import RoadmapPage

# ROADMAP_COLUMNS = [sort_order, milestone, start_date, end_date, status, auto_generated]
COL_MILESTONE = 1


def _find_button(widget, text: str) -> QPushButton:
    for btn in widget.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    raise AssertionError(f"No QPushButton with text {text!r} found")


def _course_id_by_code(store, code: str) -> int:
    return next(c.course_id for c in store.list_courses() if c.code == code)


# --------------------------------------------------------------------------
# Baseline: construct + refresh against the real workbook
# --------------------------------------------------------------------------
def test_construct_and_refresh_with_real_store_renders_six_courses_and_toolbar(qtbot, store):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()

    # Fact 1: all six real Fall 2026 courses are in the combo.
    assert page.course_combo.count() == 6
    combo_texts = [page.course_combo.itemText(i) for i in range(6)]
    assert any("CMTC2341" in t for t in combo_texts)
    assert any("INST2380" in t for t in combo_texts)

    # Fact 2: the toolbar has exactly the four advertised actions.
    for label in ("Add milestone", "Generate from syllabus", "Mark slipped", "Shift remaining by N days"):
        _find_button(page, label)  # raises if missing

    # Fact 3: two tabs, Timeline then List.
    assert page.tabs.count() == 2
    assert page.tabs.tabText(0) == "Timeline"
    assert page.tabs.tabText(1) == "List"


def test_refresh_default_course_summary_shows_known_topic_count(qtbot, store):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()

    # refresh() defaults to the first course in the sheet, CMTC2341 (course_id=1).
    first_course = store.list_courses()[0]
    assert first_course.code == "CMTC2341"
    assert page._course_id == first_course.course_id

    # Known fact about the real supplied data: all 50 CMTC2341 topics are
    # "Not Started" (none Mastered), so topics_left == 50.
    topics = store.list_topics(first_course.course_id)
    assert len(topics) == 50
    assert sum(1 for t in topics if t.status != "Mastered") == 50
    assert "50 topic(s) left" in page.summary_label.text()

    # Week numbering matches the scheduler's own computation (not hardcoded,
    # since "today" advances with the calendar).
    week1 = store.setting_date("week1_monday")
    semester_end = store.setting_date("semester_end")
    today = date.today()
    total_wk = scheduler.term_week_number(semester_end, week1)
    cur_wk = scheduler.term_week_number(today, week1)
    assert f"Week {cur_wk} of {total_wk}" in page.summary_label.text()


def test_selecting_course_via_combo_updates_course_id_and_summary(qtbot, store):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()

    cntr_id = _course_id_by_code(store, "CNTR2371")
    idx = page.course_combo.findData(cntr_id)
    assert idx != -1

    # Real combo-box selection: setCurrentIndex is exactly what Qt calls
    # internally when the user picks an item from the dropdown, and it
    # fires currentIndexChanged through the real signal/slot path (the
    # slot reads combo.currentData() itself, not a captured argument).
    page.course_combo.setCurrentIndex(idx)

    assert page._course_id == cntr_id
    # Known fact: CNTR2371 has 59 non-Mastered topics in the real workbook.
    assert "59 topic(s) left" in page.summary_label.text()


# --------------------------------------------------------------------------
# Generate from syllabus: creates auto rows, regenerate replaces them,
# a manually-added row survives untouched.
# --------------------------------------------------------------------------
def test_generate_from_syllabus_creates_and_replaces_auto_rows_but_preserves_manual_row(qtbot, store):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    idx = page.course_combo.findData(cmtc_id)
    page.course_combo.setCurrentIndex(idx)
    assert page._course_id == cmtc_id

    # A manually-added milestone that "Generate from syllabus" must never
    # touch (auto_generated=False).
    manual = store.add_roadmap_item(models.RoadmapItem(
        course_id=cmtc_id, sort_order=999, milestone="My manual note",
        start_date=date(2026, 10, 1), end_date=date(2026, 10, 3),
        status="In Progress", auto_generated=False,
    ))
    manual_id = manual.roadmap_id
    page.refresh()

    messages = []
    page.statusMessage.connect(messages.append)

    gen_btn = _find_button(page, "Generate from syllabus")
    qtbot.mouseClick(gen_btn, Qt.LeftButton)

    assert messages, "expected a statusMessage after generating"
    assert "Generated" in messages[-1] and "CMTC2341" in messages[-1]

    rows1 = store.list_roadmap(cmtc_id)
    auto1 = [r for r in rows1 if r.auto_generated]
    manual1 = [r for r in rows1 if not r.auto_generated]
    assert len(auto1) > 0
    assert all(r.auto_generated for r in auto1)
    assert len(manual1) == 1
    assert manual1[0].roadmap_id == manual_id
    assert manual1[0].milestone == "My manual note"
    assert manual1[0].start_date == date(2026, 10, 1)

    # The timeline actually drew the auto-generated milestones (distinct
    # tooltip wording added only for auto_generated=True bars).
    assert any(
        isinstance(it, QGraphicsRectItem) and "auto-generated" in it.toolTip()
        for it in page.scene.items()
    )

    snapshot1 = sorted((r.milestone, r.start_date, r.end_date) for r in auto1)

    # Regenerate: must replace (not accumulate) the auto rows - delete_auto_
    # roadmap() removes every old auto_generated row before the new batch is
    # written, so re-running does NOT just keep appending - and must leave
    # the manual row completely untouched.
    qtbot.mouseClick(gen_btn, Qt.LeftButton)

    rows2 = store.list_roadmap(cmtc_id)
    auto2 = [r for r in rows2 if r.auto_generated]
    manual2 = [r for r in rows2 if not r.auto_generated]

    assert len(auto2) == len(auto1), "auto rows should be replaced, not accumulated"
    assert len(rows2) == len(auto1) + 1, "total row count must not grow across a regenerate"
    snapshot2 = sorted((r.milestone, r.start_date, r.end_date) for r in auto2)
    assert snapshot1 == snapshot2  # same content, generated the same "today"

    assert len(manual2) == 1
    assert manual2[0].roadmap_id == manual_id
    assert manual2[0].milestone == "My manual note"
    assert manual2[0].start_date == date(2026, 10, 1)
    assert manual2[0].end_date == date(2026, 10, 3)
    assert manual2[0].status == "In Progress"


def test_generate_from_syllabus_without_course_selected_emits_status_and_touches_nothing(qtbot, empty_store):
    page = RoadmapPage(empty_store)
    qtbot.addWidget(page)
    page.refresh()
    assert page._course_id is None

    messages = []
    page.statusMessage.connect(messages.append)
    gen_btn = _find_button(page, "Generate from syllabus")
    qtbot.mouseClick(gen_btn, Qt.LeftButton)

    assert messages == ["Select a course first."]
    assert empty_store.list_roadmap() == []


# --------------------------------------------------------------------------
# Add milestone (dialog monkeypatched to avoid a real modal .exec())
# --------------------------------------------------------------------------
def test_add_milestone_dialog_accepted_adds_row_to_store(qtbot, store, monkeypatch):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()
    cmtc_id = page._course_id

    def fake_edit_row(parent, title, columns, obj, exclude_attrs=None, multiline_attrs=None):
        assert title == "Add milestone"
        obj.milestone = "Test Milestone"
        obj.start_date = date(2026, 9, 15)
        obj.end_date = date(2026, 9, 20)
        return obj

    monkeypatch.setattr(dialogs, "edit_row", fake_edit_row)

    add_btn = _find_button(page, "Add milestone")
    qtbot.mouseClick(add_btn, Qt.LeftButton)

    rows = store.list_roadmap(cmtc_id)
    assert len(rows) == 1
    assert rows[0].milestone == "Test Milestone"
    assert rows[0].course_id == cmtc_id
    assert rows[0].roadmap_id != 0
    assert rows[0].start_date == date(2026, 9, 15)


def test_add_milestone_dialog_cancelled_adds_nothing(qtbot, store, monkeypatch):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()
    cmtc_id = page._course_id

    monkeypatch.setattr(dialogs, "edit_row", lambda *a, **k: None)

    add_btn = _find_button(page, "Add milestone")
    qtbot.mouseClick(add_btn, Qt.LeftButton)

    assert store.list_roadmap(cmtc_id) == []


# --------------------------------------------------------------------------
# Edit selected (dialog monkeypatched)
# --------------------------------------------------------------------------
def test_edit_selected_via_dialog_updates_row_and_preserves_identity(qtbot, store, monkeypatch):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()
    cmtc_id = page._course_id

    original = store.add_roadmap_item(models.RoadmapItem(
        course_id=cmtc_id, sort_order=1, milestone="Original", status="Planned",
        auto_generated=False,
    ))
    page.refresh()
    assert page.list_model.rowCount() == 1

    def fake_edit_row(parent, title, columns, obj, exclude_attrs=None, multiline_attrs=None):
        assert title == "Edit milestone"
        obj.milestone = "Edited Milestone"
        obj.status = "In Progress"
        return obj

    monkeypatch.setattr(dialogs, "edit_row", fake_edit_row)

    page.list_view.selectRow(0)
    edit_btn = _find_button(page, "Edit")
    qtbot.mouseClick(edit_btn, Qt.LeftButton)

    rows = store.list_roadmap(cmtc_id)
    assert len(rows) == 1
    assert rows[0].roadmap_id == original.roadmap_id
    assert rows[0].course_id == cmtc_id
    assert rows[0].auto_generated is False
    assert rows[0].milestone == "Edited Milestone"
    assert rows[0].status == "In Progress"


def test_edit_selected_with_no_selection_emits_status_message(qtbot, store):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()
    assert page.list_model.rowCount() == 0

    messages = []
    page.statusMessage.connect(messages.append)
    edit_btn = _find_button(page, "Edit")
    qtbot.mouseClick(edit_btn, Qt.LeftButton)

    assert messages == ["Select a milestone in the List tab first."]


# --------------------------------------------------------------------------
# Mark slipped
# --------------------------------------------------------------------------
def test_mark_slipped_button_updates_status_for_selected_rows_and_persists(qtbot, store):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()
    cmtc_id = page._course_id

    store.add_roadmap_item(models.RoadmapItem(course_id=cmtc_id, sort_order=1, milestone="M1", status="Planned"))
    store.add_roadmap_item(models.RoadmapItem(course_id=cmtc_id, sort_order=2, milestone="M2", status="Planned"))
    page.refresh()
    assert page.list_model.rowCount() == 2

    messages = []
    page.statusMessage.connect(messages.append)

    page.list_view.selectAll()
    slip_btn = _find_button(page, "Mark slipped")
    qtbot.mouseClick(slip_btn, Qt.LeftButton)

    assert messages == ["Marked 2 milestone(s) slipped."]
    by_milestone = {r.milestone: r.status for r in store.list_roadmap(cmtc_id)}
    assert by_milestone["M1"] == "Slipped"
    assert by_milestone["M2"] == "Slipped"


# --------------------------------------------------------------------------
# Delete selected (confirm() monkeypatched to avoid a real QMessageBox)
# --------------------------------------------------------------------------
def test_delete_selected_confirmed_removes_row(qtbot, store, monkeypatch):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()
    cmtc_id = page._course_id

    item = store.add_roadmap_item(models.RoadmapItem(course_id=cmtc_id, sort_order=1, milestone="ToDelete"))
    page.refresh()
    assert page.list_model.rowCount() == 1

    monkeypatch.setattr(widgets, "confirm", lambda *a, **k: True)

    page.list_view.selectRow(0)
    del_btn = _find_button(page, "Delete")
    qtbot.mouseClick(del_btn, Qt.LeftButton)

    remaining = store.list_roadmap(cmtc_id)
    assert remaining == []
    assert store.get_roadmap_item(item.roadmap_id) is None


def test_delete_selected_declined_keeps_row(qtbot, store, monkeypatch):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()
    cmtc_id = page._course_id

    item = store.add_roadmap_item(models.RoadmapItem(course_id=cmtc_id, sort_order=1, milestone="KeepMe"))
    page.refresh()

    monkeypatch.setattr(widgets, "confirm", lambda *a, **k: False)

    page.list_view.selectRow(0)
    del_btn = _find_button(page, "Delete")
    qtbot.mouseClick(del_btn, Qt.LeftButton)

    remaining = store.list_roadmap(cmtc_id)
    assert len(remaining) == 1
    assert remaining[0].roadmap_id == item.roadmap_id
    assert remaining[0].milestone == "KeepMe"


# --------------------------------------------------------------------------
# Shift remaining by N days (QInputDialog.getInt monkeypatched)
# --------------------------------------------------------------------------
def test_shift_remaining_shifts_only_future_non_done_items(qtbot, store, monkeypatch):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()
    cmtc_id = page._course_id

    today = date.today()
    future_planned = store.add_roadmap_item(models.RoadmapItem(
        course_id=cmtc_id, sort_order=1, milestone="Future", status="Planned",
        start_date=today + timedelta(days=5), end_date=today + timedelta(days=7),
    ))
    future_done = store.add_roadmap_item(models.RoadmapItem(
        course_id=cmtc_id, sort_order=2, milestone="DoneFuture", status="Done",
        start_date=today + timedelta(days=5), end_date=today + timedelta(days=7),
    ))
    past_planned = store.add_roadmap_item(models.RoadmapItem(
        course_id=cmtc_id, sort_order=3, milestone="Past", status="Planned",
        start_date=today - timedelta(days=5), end_date=today - timedelta(days=3),
    ))
    page.refresh()

    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (10, True))

    messages = []
    page.statusMessage.connect(messages.append)
    shift_btn = _find_button(page, "Shift remaining by N days")
    qtbot.mouseClick(shift_btn, Qt.LeftButton)

    assert messages == ["Shifted 1 milestone(s) by 10 day(s)."]

    by_id = {r.roadmap_id: r for r in store.list_roadmap(cmtc_id)}
    assert by_id[future_planned.roadmap_id].start_date == today + timedelta(days=15)
    assert by_id[future_planned.roadmap_id].end_date == today + timedelta(days=17)
    # Done items are never shifted, regardless of date.
    assert by_id[future_done.roadmap_id].start_date == today + timedelta(days=5)
    assert by_id[future_done.roadmap_id].end_date == today + timedelta(days=7)
    # Items that already started (start_date < today) are left alone too.
    assert by_id[past_planned.roadmap_id].start_date == today - timedelta(days=5)
    assert by_id[past_planned.roadmap_id].end_date == today - timedelta(days=3)


def test_shift_remaining_cancelled_dialog_makes_no_changes(qtbot, store, monkeypatch):
    page = RoadmapPage(store)
    qtbot.addWidget(page)
    page.refresh()
    cmtc_id = page._course_id

    today = date.today()
    item = store.add_roadmap_item(models.RoadmapItem(
        course_id=cmtc_id, sort_order=1, milestone="Future", status="Planned",
        start_date=today + timedelta(days=5), end_date=today + timedelta(days=7),
    ))
    page.refresh()

    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (10, False))  # user hit Cancel

    shift_btn = _find_button(page, "Shift remaining by N days")
    qtbot.mouseClick(shift_btn, Qt.LeftButton)

    unchanged = store.get_roadmap_item(item.roadmap_id)
    assert unchanged.start_date == today + timedelta(days=5)
    assert unchanged.end_date == today + timedelta(days=7)


# --------------------------------------------------------------------------
# Edge case: brand-new empty workbook, zero courses
# --------------------------------------------------------------------------
def test_empty_store_no_courses_shows_placeholder_and_guards_every_action(qtbot, empty_store):
    page = RoadmapPage(empty_store)
    qtbot.addWidget(page)
    page.refresh()

    assert page.course_combo.count() == 0
    assert page._course_id is None
    assert page.summary_label.text() == "No course selected."
    assert page.list_model.rowCount() == 0
    assert any(
        isinstance(it, QGraphicsTextItem) and "Add a course first" in it.toPlainText()
        for it in page.scene.items()
    )

    messages = []
    page.statusMessage.connect(messages.append)

    qtbot.mouseClick(_find_button(page, "Add milestone"), Qt.LeftButton)
    assert messages[-1] == "Select a course first."

    qtbot.mouseClick(_find_button(page, "Generate from syllabus"), Qt.LeftButton)
    assert messages[-1] == "Select a course first."

    qtbot.mouseClick(_find_button(page, "Shift remaining by N days"), Qt.LeftButton)
    assert messages[-1] == "Select a course first."

    qtbot.mouseClick(_find_button(page, "Edit"), Qt.LeftButton)
    assert messages[-1] == "Select a milestone in the List tab first."

    qtbot.mouseClick(_find_button(page, "Mark slipped"), Qt.LeftButton)
    assert messages[-1] == "Select one or more milestones in the List tab first."

    qtbot.mouseClick(_find_button(page, "Delete"), Qt.LeftButton)
    assert messages[-1] == "Select one or more milestones in the List tab first."

    assert empty_store.list_roadmap() == []
