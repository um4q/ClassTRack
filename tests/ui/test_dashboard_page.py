"""Tests for app.ui.dashboard_page.DashboardPage - the 10-card overview.

Every assertion about "what's on the page" is computed from the same
``store`` the page reads from, rather than hardcoded, since the underlying
workbook is real semester data and "today" (used by several cards) differs
depending on when this suite runs.
"""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QListWidget, QPushButton

from app.excel_store import ExcelStore
from app.services import progress
from app.ui.dashboard_page import DashboardPage, _ClickableCard

CARD_TITLES = (
    "Tomorrow's Pre-Lab",
    "Today / Tomorrow",
    "Upcoming Exams",
    "Deadlines (7 days)",
    "This Week",
    "Course Progress",
    "Focus Next",
    "Next Best Action",
    "Syllabus Flags",
    "Quick Brain Dump",
)


def _card_titles(page: DashboardPage) -> list[str]:
    """Text of the bold title QLabel that _card() puts first in every
    QFrame#Card's layout."""
    out = []
    for frame in page.findChildren(QFrame):
        if frame.objectName() == "Card" and frame.layout() and frame.layout().count() > 0:
            w = frame.layout().itemAt(0).widget()
            if isinstance(w, QLabel):
                out.append(w.text())
    return out


def _card_by_title(page: DashboardPage, title: str) -> QFrame:
    for frame in page.findChildren(QFrame):
        if frame.objectName() == "Card" and frame.layout() and frame.layout().count() > 0:
            w = frame.layout().itemAt(0).widget()
            if isinstance(w, QLabel) and w.text() == title:
                return frame
    raise AssertionError(f"no Card titled {title!r} found")


def _buttons_with_text(page: DashboardPage, text: str) -> list[QPushButton]:
    return [b for b in page.findChildren(QPushButton) if b.text() == text]


# --------------------------------------------------------------------------
# Baseline: construct with the real store, refresh(), check concrete facts
# --------------------------------------------------------------------------
def test_dashboard_renders_all_ten_cards_with_real_workbook_data(qtbot, store):
    page = DashboardPage(store)
    qtbot.addWidget(page)

    page.refresh()  # must not raise

    # All ten cards from the build spec are present, in title order.
    assert _card_titles(page) == list(CARD_TITLES)

    # Card 6 (Course Progress): one _ClickableCard per active course, and
    # every real course code shows up as a Badge somewhere in the tree.
    active_courses = [c for c in store.list_courses() if c.active]
    assert active_courses, "expected at least one active course in the real workbook"
    clickable_cards = page.findChildren(_ClickableCard)
    assert len(clickable_cards) == len(active_courses)

    all_label_text = " ".join(l.text() for l in page.findChildren(QLabel))
    for course in active_courses:
        assert course.code in all_label_text

    # Card 9 (Syllabus Flags): the toggle button's count matches the store.
    unresolved = store.list_syllabus_flags(unresolved_only=True)
    flag_buttons = _buttons_with_text(page, f"⚠ {len(unresolved)} unresolved")
    assert len(flag_buttons) == 1

    # Card 10 (Quick Brain Dump) and Card 8 (Next Best Action) buttons exist.
    assert len(_buttons_with_text(page, "+ Quick brain dump")) == 1
    assert len(_buttons_with_text(page, "Start 25-min timer")) == 1


def test_dashboard_with_empty_store_shows_empty_states_for_every_card(qtbot, empty_store):
    page = DashboardPage(empty_store)
    qtbot.addWidget(page)

    page.refresh()

    assert _card_titles(page) == list(CARD_TITLES)
    assert page.findChildren(_ClickableCard) == []

    texts = {l.text() for l in page.findChildren(QLabel)}
    assert "No lab tomorrow \U0001F389" in texts
    assert "No exams in the next 30 days" in texts
    assert "Nothing due in the next 7 days" in texts
    assert "No active courses" in texts
    assert "Nothing urgent — nice work" in texts
    assert "All caught up — nothing urgent right now." in texts
    assert len(_buttons_with_text(page, "⚠ 0 unresolved")) == 1


# --------------------------------------------------------------------------
# Card 1 (Tomorrow's Pre-Lab) content matches what the store says is due
# --------------------------------------------------------------------------
def test_prelab_card_matches_store_computed_due_prelabs(qtbot, store):
    page = DashboardPage(store)
    qtbot.addWidget(page)
    page.refresh()

    today = date.today()
    tomorrow = today + timedelta(days=1)
    active_ids = {c.course_id for c in store.list_courses() if c.active}
    prelabs = [
        p for p in store.list_prelabs()
        if p.course_id in active_ids and p.lab_type != "None"
    ]
    matches = [
        p for p in prelabs
        if p.lab_date == tomorrow or (p.lab_date == today and not p.completed)
    ]

    card = _card_by_title(page, "Tomorrow's Pre-Lab")
    card_text = " ".join(l.text() for l in card.findChildren(QLabel))

    if not matches:
        assert "No lab tomorrow \U0001F389" in card_text
    else:
        assert "No lab tomorrow" not in card_text
        course_map = {c.course_id: c for c in store.list_courses()}
        for p in matches:
            course = course_map.get(p.course_id)
            code = course.code if course else "?"
            assert code in card_text
            assert f"Lab {p.lab_number}: {p.title}" in card_text
        # Exactly one "Pre-lab complete" checkbox per matched pre-lab.
        checkboxes = [
            cb for cb in card.findChildren(QCheckBox) if cb.text() == "Pre-lab complete"
        ]
        assert len(checkboxes) == len(matches)


# --------------------------------------------------------------------------
# Card 6 click -> openCourse(course_id)
# --------------------------------------------------------------------------
def test_clicking_course_progress_card_emits_open_course_with_correct_id(qtbot, store):
    page = DashboardPage(store)
    qtbot.addWidget(page)
    page.refresh()

    active_courses = [c for c in store.list_courses() if c.active]
    target = active_courses[0]

    target_card = None
    for card in page.findChildren(_ClickableCard):
        if any(l.text() == target.code for l in card.findChildren(QLabel)):
            target_card = card
            break
    assert target_card is not None, f"no course-progress card found for {target.code}"

    with qtbot.waitSignal(page.openCourse, timeout=2000) as blocker:
        qtbot.mouseClick(target_card, Qt.LeftButton)
    assert blocker.args == [target.course_id]


# --------------------------------------------------------------------------
# Card 10 click -> quickBrainDump()
# --------------------------------------------------------------------------
def test_clicking_quick_brain_dump_button_emits_signal(qtbot, store):
    page = DashboardPage(store)
    qtbot.addWidget(page)
    page.refresh()

    buttons = _buttons_with_text(page, "+ Quick brain dump")
    assert len(buttons) == 1

    with qtbot.waitSignal(page.quickBrainDump, timeout=2000):
        qtbot.mouseClick(buttons[0], Qt.LeftButton)


# --------------------------------------------------------------------------
# Card 8 click -> startTimerRequested(course_id, topic_id) with a real id
# --------------------------------------------------------------------------
def test_clicking_start_timer_button_emits_signal_with_real_course_id(qtbot, store):
    page = DashboardPage(store)
    qtbot.addWidget(page)
    page.refresh()

    today = date.today()
    all_courses = store.list_courses()
    course_map = {c.course_id: c for c in all_courses}
    active_ids = {c.course_id for c in all_courses if c.active}
    expected_message, expected_course_id, expected_topic_id = page._compute_next_action(
        today, course_map, active_ids
    )
    assert expected_course_id != 0, (
        "expected the real workbook to produce a concrete next-best-action "
        "course id, not the 'nothing to do' fallback"
    )

    buttons = _buttons_with_text(page, "Start 25-min timer")
    assert len(buttons) == 1

    with qtbot.waitSignal(page.startTimerRequested, timeout=2000) as blocker:
        qtbot.mouseClick(buttons[0], Qt.LeftButton)
    assert blocker.args == [expected_course_id, expected_topic_id]


# --------------------------------------------------------------------------
# Card 9: resolving a flag's checkbox is a real interaction that must
# persist to the workbook (checked with a fresh store read afterwards).
# --------------------------------------------------------------------------
def test_resolving_a_syllabus_flag_checkbox_persists_to_the_workbook(qtbot, store, workbook_path):
    page = DashboardPage(store)
    qtbot.addWidget(page)
    page.refresh()
    page.show()
    qtbot.waitExposed(page)

    unresolved_before = store.list_syllabus_flags(unresolved_only=True)
    assert unresolved_before, "expected at least one unresolved syllabus flag in real data"
    target_flag = unresolved_before[0]
    course_map = {c.course_id: c for c in store.list_courses()}
    code = course_map[target_flag.course_id].code if target_flag.course_id in course_map else "?"
    expected_row_text = f"{code} — {target_flag.item}: {target_flag.issue}"

    toggle_buttons = _buttons_with_text(page, f"⚠ {len(unresolved_before)} unresolved")
    assert len(toggle_buttons) == 1
    toggle_btn = toggle_buttons[0]

    flags_card = _card_by_title(page, "Syllabus Flags")
    flag_list = flags_card.findChild(QListWidget)
    assert flag_list is not None
    assert not flag_list.isVisible()

    qtbot.mouseClick(toggle_btn, Qt.LeftButton)
    assert flag_list.isVisible()

    target_checkbox = None
    for i in range(flag_list.count()):
        row_widget = flag_list.itemWidget(flag_list.item(i))
        lbl = row_widget.findChild(QLabel)
        if lbl is not None and lbl.text() == expected_row_text:
            target_checkbox = row_widget.findChild(QCheckBox)
            break
    assert target_checkbox is not None, "couldn't find the row for the target flag"
    assert not target_checkbox.isChecked()

    qtbot.mouseClick(target_checkbox, Qt.LeftButton)
    assert target_checkbox.isChecked()

    # update_syllabus_flag only marks the store dirty and arms a debounced
    # autosave timer; force a synchronous save so the on-disk copy reflects
    # the click without waiting on that timer.
    store.save(force=True)

    fresh = ExcelStore(workbook_path)
    fresh.load()
    updated = next(f for f in fresh.list_syllabus_flags() if f.flag_id == target_flag.flag_id)
    assert updated.resolved is True
    assert all(f.flag_id != target_flag.flag_id for f in fresh.list_syllabus_flags(unresolved_only=True))


# --------------------------------------------------------------------------
# Card 7: statusMessage fires when a topic has no openable link (real click
# path, with open_resource patched so no native file/URL open is attempted).
# --------------------------------------------------------------------------
def test_focus_next_open_button_with_no_link_emits_status_message(qtbot, store, monkeypatch):
    page = DashboardPage(store)
    qtbot.addWidget(page)
    page.refresh()

    today = date.today()
    active_ids = {c.course_id for c in store.list_courses() if c.active}
    topics = [t for t in store.list_topics() if t.course_id in active_ids]
    assessments = [a for a in store.list_assessments() if a.course_id in active_ids]
    ranked = progress.rank_topics_by_focus(topics, assessments, today, limit=8)
    assert ranked, "expected at least one focus-ranked topic in real data"
    expected_topic = ranked[0]

    monkeypatch.setattr("app.ui.dashboard_page.open_resource", lambda *a, **k: False)

    open_buttons = _buttons_with_text(page, "Open")
    assert open_buttons, "expected 'Open' buttons in the Focus Next card"

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(open_buttons[0], Qt.LeftButton)
    assert blocker.args == [f"No link for topic '{expected_topic.title}'"]
