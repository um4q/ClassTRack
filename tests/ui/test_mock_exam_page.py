"""
Tests for app.ui.mock_exam_page.MockExamPage (build spec section 5.7).

Covers: baseline construction/refresh against the real six-course workbook,
the Setup -> Exam -> Results state machine driven end to end through real
qtbot interactions (course/source QComboBox selections, a shuffle QCheckBox
toggle, a real "Start" QPushButton click, a QRadioButton pick for an MCQ
question, keyboard-typed Short Answer text, a real "Submit exam" click,
self-grading a Short Answer question via its "Correct" QCheckBox, saving the
attempt and confirming it lands in ``store.list_mock_exams`` with a parsable
``results_json``, the "Set topics below 60% to Needs Focus" persistence
path, and "New exam" resetting back to Setup), the countdown's auto-submit
path at 0 seconds remaining, and two Setup-time guard rails (no course in
the combo at all; a real course with zero practice questions).

CMTC2341 (course_id resolved by code, not hard-coded) has exactly three
practice questions in the real workbook - question_id 1 (Short Answer,
topic 1.2), 2 (MCQ "Half-duplex", topic 1.4), 3 (Short Answer, topic 1.3).
Picking "All" with question count 3 and shuffle off always samples and
orders all three the same way (``random.sample`` of a whole population,
then a plain sort by question_id), which is what makes the deep flow test
below deterministic despite the page using real ``random`` sampling.

widgets.confirm (a blocking QMessageBox.question().exec()) is monkeypatched
before any click that would otherwise open it - see the safety rules in the
shared conftest docstring.
"""
from __future__ import annotations

import json
from datetime import date

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QRadioButton

from app.ui.mock_exam_page import MockExamPage


def _find_button(widget, text: str) -> QPushButton:
    matches = [b for b in widget.findChildren(QPushButton) if b.text() == text]
    assert len(matches) == 1, f"expected exactly one {text!r} button, found {len(matches)}"
    return matches[0]


def _click_checkable(qtbot, widget) -> None:
    """qtbot.mouseClick a QCheckBox/QRadioButton at its own indicator+label,
    not the default rect-center: these widgets restrict their real click
    area to that sub-rect (``hitButton()``), and a row widget stretched
    wide by its layout (as every checkbox/radio in this page is - they sit
    in full-width form rows) leaves empty space to the right of it where a
    center-of-rect click - what a real user's click never lands on, since
    there is nothing to click there - would silently do nothing."""
    qtbot.mouseClick(widget, Qt.LeftButton, pos=QPoint(8, widget.height() // 2))


def _course_id_by_code(store, code: str) -> int:
    return next(c.course_id for c in store.list_courses() if c.code == code)


# --------------------------------------------------------------------------
# Baseline: construct + refresh against the real workbook.
# --------------------------------------------------------------------------
def test_construct_and_refresh_with_real_store_renders_courses_and_history(qtbot, store):
    page = MockExamPage(store)
    qtbot.addWidget(page)
    page.refresh()

    active_courses = store.list_courses(active_only=True)
    assert page._course_combo.count() == len(active_courses) == 6
    labels = [page._course_combo.itemText(i) for i in range(page._course_combo.count())]
    assert any("CMTC2341" in label for label in labels)
    assert any("INST2380" in label for label in labels)

    assert page._start_btn.text() == "Start"

    # No mock exams exist yet in the real workbook for any course, so the
    # History table for whichever course ends up selected starts empty.
    current_course_id = page._course_combo.currentData()
    assert store.list_mock_exams(current_course_id) == []
    assert page._history_model.rowCount() == 0


# --------------------------------------------------------------------------
# Setup guard rails.
# --------------------------------------------------------------------------
def test_start_with_no_courses_emits_status_message_and_stays_in_setup(qtbot, empty_store):
    page = MockExamPage(empty_store)
    qtbot.addWidget(page)
    assert page._course_combo.count() == 0

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(page._start_btn, Qt.LeftButton)

    assert blocker.args == ["Add a course first."]
    assert page._stack.currentIndex() == 0


def test_start_with_course_having_no_practice_questions_emits_status_message(qtbot, store):
    page = MockExamPage(store)
    qtbot.addWidget(page)
    inst_id = _course_id_by_code(store, "INST2361")
    assert store.list_practice_questions(inst_id) == []
    page._course_combo.setCurrentIndex(page._course_combo.findData(inst_id))

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(page._start_btn, Qt.LeftButton)

    assert blocker.args == ["This course has no practice questions yet."]
    assert page._stack.currentIndex() == 0


def test_source_combo_chosen_topics_reveals_topic_checklist_and_requires_a_selection(qtbot, store):
    page = MockExamPage(store)
    qtbot.addWidget(page)
    cmtc_id = _course_id_by_code(store, "CMTC2341")
    page._course_combo.setCurrentIndex(page._course_combo.findData(cmtc_id))
    assert page._topics_list.isHidden()
    assert page._topics_list.count() == len(store.list_topics(cmtc_id)) == 50

    # Real QComboBox selection via setCurrentIndex - what Qt itself calls
    # when a user picks a combo entry.
    page._source_combo.setCurrentIndex(page._source_combo.findText("Chosen topics"))
    assert not page._topics_list.isHidden()

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(page._start_btn, Qt.LeftButton)

    assert blocker.args == ["Select at least one topic first."]
    assert page._stack.currentIndex() == 0


# --------------------------------------------------------------------------
# Deep flow: Setup -> Exam -> Results -> Save -> Set Needs Focus -> New exam.
# --------------------------------------------------------------------------
def test_full_mock_exam_flow_setup_through_save_and_needs_focus(qtbot, store, monkeypatch):
    page = MockExamPage(store)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitExposed(page)

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    combo_idx = page._course_combo.findData(cmtc_id)
    assert combo_idx >= 0
    page._course_combo.setCurrentIndex(combo_idx)

    page._num_spin.setValue(3)  # CMTC2341 has exactly 3 practice questions
    page._source_combo.setCurrentIndex(page._source_combo.findText("All"))

    # Real checkbox toggle: shuffle defaults on; turn it off so question and
    # option order is deterministic (sorted by question_id) for this test.
    assert page._shuffle_check.isChecked()
    _click_checkable(qtbot, page._shuffle_check)
    assert not page._shuffle_check.isChecked()

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(page._start_btn, Qt.LeftButton)
    assert blocker.args == ["Started mock exam: 3 question(s), 30 min."]

    # -- now in Exam mode ------------------------------------------------
    assert page._stack.currentIndex() == 1
    assert len(page._exam_questions) == 3
    assert [q.question_id for q in page._exam_questions] == [1, 2, 3]
    assert page._exam_progress_label.text() == "Question 1 / 3"

    # Q1 (question_id=1): Short Answer -> type an answer.
    assert page._exam_questions[0].question_type == "Short Answer"
    assert page._answer_line is not None
    qtbot.keyClicks(page._answer_line, "DTE originates/receives; DCE converts the signal.")

    next_btn = _find_button(page, "Next ▶")
    qtbot.mouseClick(next_btn, Qt.LeftButton)
    assert page._exam_progress_label.text() == "Question 2 / 3"

    # Q2 (question_id=2): MCQ -> select the correct radio button for real.
    q2 = page._exam_questions[1]
    assert q2.question_type == "MCQ"
    radios = {rb.text(): rb for rb in page._question_card.findChildren(QRadioButton)}
    assert set(radios) == set(page._exam_options[q2.question_id]) == {
        "Simplex", "Half-duplex", "Full-duplex", "Multiplex",
    }
    _click_checkable(qtbot, radios["Half-duplex"])

    qtbot.mouseClick(next_btn, Qt.LeftButton)
    assert page._exam_progress_label.text() == "Question 3 / 3"

    # Q3 (question_id=3): Short Answer -> type an answer.
    assert page._exam_questions[2].question_type == "Short Answer"
    qtbot.keyClicks(page._answer_line, "BPS counts bits; baud counts symbol changes.")

    # Submit: bypass the blocking confirm() dialog, then click for real.
    monkeypatch.setattr("app.ui.widgets.confirm", lambda *a, **k: True)
    submit_btn = _find_button(page, "Submit exam")
    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(submit_btn, Qt.LeftButton)
    assert blocker.args == ["Exam submitted."]

    # -- now in Results mode ----------------------------------------------
    assert page._stack.currentIndex() == 2
    assert page._submitted is True
    # MCQ auto-graded correct; both Short Answers default to ungraded/false
    # until the student self-grades them below.
    assert page._final_correct == {1: False, 2: True, 3: False}

    score_labels = [l.text() for l in page.findChildren(QLabel) if l.text().startswith("Score:")]
    assert score_labels == ["Score: 1/3 (33%)"]

    # Self-grade Q1 (the first Short Answer row) as correct via its real
    # "Correct" QCheckBox click.
    correct_checkboxes = [cb for cb in page.findChildren(QCheckBox) if cb.text() == "Correct"]
    assert len(correct_checkboxes) == 2  # one per Short Answer question (Q1, Q3)
    _click_checkable(qtbot, correct_checkboxes[0])
    assert page._final_correct[1] is True

    # Results view re-rendered on toggle - re-query the (new) score label.
    score_labels = [l.text() for l in page.findChildren(QLabel) if l.text().startswith("Score:")]
    assert score_labels == ["Score: 2/3 (67%)"]

    # -- Save --------------------------------------------------------------
    save_btn = _find_button(page, "Save")
    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(save_btn, Qt.LeftButton)
    assert blocker.args == ["Saved mock exam (2/3)."]
    assert page._exam_saved is True

    saved_rows = store.list_mock_exams(cmtc_id)
    assert len(saved_rows) == 1
    saved = saved_rows[0]
    assert saved.question_ids == [1, 2, 3]
    assert len(saved.question_ids) == 3
    assert saved.score == 2.0
    assert saved.max_score == 3.0
    assert saved.course_id == cmtc_id
    assert saved.date_taken == date.today()

    parsed = json.loads(saved.results_json)
    assert {q["question_id"] for q in parsed["questions"]} == {1, 2, 3}
    correct_map = {q["question_id"]: q["correct"] for q in parsed["questions"]}
    assert correct_map == {1: True, 2: True, 3: False}

    # Saving again is a no-op - the button disables itself, so there is
    # nothing left to click; confirm no second MockExams row exists.
    save_btn_after = _find_button(page, "Saved ✓")
    assert not save_btn_after.isEnabled()
    assert len(store.list_mock_exams(cmtc_id)) == 1

    # -- Set topics below 60% to Needs Focus --------------------------------
    # topic_id=3 (Q3's topic) is the only one still below 60% (topics 1.2
    # and 1.4, Q1 and Q2's topics, are both at 100% after self-grading).
    topic2_before = store.get_topic(2)
    topic4_before = store.get_topic(4)
    focus_btn = _find_button(page, "Set topics below 60% to Needs Focus")
    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(focus_btn, Qt.LeftButton)
    assert blocker.args == ["Set 1 topic(s) to Needs Focus."]

    topic3_after = store.get_topic(3)
    assert topic3_after.status == "Needs Focus"
    assert topic3_after.last_reviewed == date.today()
    # Topics tied to fully-correct questions are untouched.
    assert store.get_topic(2).status == topic2_before.status
    assert store.get_topic(4).status == topic4_before.status

    # -- New exam: resets back to Setup and clears in-progress exam state.
    new_btn = _find_button(page, "New exam")
    qtbot.mouseClick(new_btn, Qt.LeftButton)
    assert page._stack.currentIndex() == 0
    assert page._exam_questions == []
    assert page._submitted is False
    # (_exam_saved itself is only reset by the *next* _begin_exam call, not
    # by New exam - it just no longer matters once _exam_questions is empty)
    # History table now reflects the just-saved exam for whichever course
    # ended up selected after refresh() (still CMTC2341 - its combo data
    # is preserved across refresh()).
    assert page._course_combo.currentData() == cmtc_id
    assert page._history_model.rowCount() == 1


# --------------------------------------------------------------------------
# Countdown auto-submit at 0 seconds remaining.
# --------------------------------------------------------------------------
def test_countdown_reaching_zero_auto_submits_into_results(qtbot, store):
    page = MockExamPage(store)
    qtbot.addWidget(page)

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    page._course_combo.setCurrentIndex(page._course_combo.findData(cmtc_id))
    page._num_spin.setValue(2)
    page._time_spin.setValue(1)  # minimum allowed = 1 minute

    qtbot.mouseClick(page._start_btn, Qt.LeftButton)
    assert page._stack.currentIndex() == 1
    assert page._exam_timer.isActive()
    assert page._remaining_seconds == 60
    assert page._submitted is False

    # Fast-forward the countdown by driving its own tick handler directly
    # (real 60 one-second waits would make this test unbearably slow) -
    # force it to 1 second left, then let one real tick bring it to 0.
    page._remaining_seconds = 1
    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        page._on_exam_tick()

    assert blocker.args == ["Time's up — exam auto-submitted."]
    assert page._remaining_seconds == 0
    assert page._countdown_label.text() == "00:00"
    assert page._submitted is True
    assert not page._exam_timer.isActive()
    # It must land in Results mode, not hang in Exam mode.
    assert page._stack.currentIndex() == 2
