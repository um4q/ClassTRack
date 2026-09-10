"""
Tests for app.ui.practice_page.PracticePage (build spec §5.6): the
spaced-repetition flash-card practice queue over PracticeQuestions, its
course/topic/difficulty/tag/due-today/needs-focus filters, the
Reveal -> "Got it"/"Missed it" Leitner-box update flow (both real button
clicks and the Space/1/2 keyboard shortcuts), the "Open coursepack" link,
and the "Manage questions" CRUD + CSV import sub-dialog.

Modal dialogs (_ManageQuestionsDialog.exec, QMessageBox.information,
QFileDialog.getOpenFileName) are monkeypatched before any click that would
otherwise open them - see the safety rules in the shared conftest docstring.
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QPushButton, QRadioButton

from app.services import progress
from app.ui import practice_page as practice_page_module
from app.ui.practice_page import PracticePage, _ManageQuestionsDialog


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _find_button(widget, text: str) -> QPushButton:
    for btn in widget.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    raise AssertionError(f"No QPushButton with text {text!r} found")


def _course_id_by_code(store, code: str) -> int:
    return next(c.course_id for c in store.list_courses() if c.code == code)


def _select_course(page: PracticePage, course_id) -> None:
    """Real QComboBox selection - setCurrentIndex is exactly what Qt calls
    internally when a user picks an item from the dropdown, firing
    currentIndexChanged through the real signal/slot path."""
    idx = page.course_combo.findData(course_id)
    assert idx != -1, f"course_id {course_id!r} not found in course_combo"
    page.course_combo.setCurrentIndex(idx)


def _select_topic(page: PracticePage, topic_id) -> None:
    idx = page.topic_combo.findData(topic_id)
    assert idx != -1, f"topic_id {topic_id!r} not found in topic_combo"
    page.topic_combo.setCurrentIndex(idx)


# --------------------------------------------------------------------------
# Baseline: construct + refresh against the real six-course workbook.
# --------------------------------------------------------------------------
def test_construct_and_refresh_with_real_store_shows_known_counts_and_controls(qtbot, store):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.refresh()  # must not raise

    # Fact 1: "All courses" plus the six real Fall 2026 courses.
    assert page.course_combo.count() == 7
    assert page.course_combo.itemText(0) == "All courses"
    combo_texts = [page.course_combo.itemText(i) for i in range(7)]
    assert any(t.startswith("CMTC2341") for t in combo_texts)
    assert any(t.startswith("INST2380") for t in combo_texts)

    # Fact 2: the real workbook has exactly 9 practice questions, none ever
    # attempted (box=1, next_due=None -> every one counts as "due").
    all_questions = store.list_practice_questions()
    assert len(all_questions) == 9
    assert page.counter_label.text() == "9 due · 9 total"

    # Fact 3: the advertised controls exist with their real labels.
    assert page.reveal_btn.text() == "Reveal (Space)"
    assert page.gotit_btn.text() == "Got it (1)"
    assert page.missedit_btn.text() == "Missed it (2)"
    _find_button(page, "Manage questions")  # raises if missing


def test_refresh_when_store_raises_emits_status_message(qtbot, store, monkeypatch):
    page = PracticePage(store)
    qtbot.addWidget(page)

    def _raise(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(store, "list_courses", _raise)

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        page.refresh()
    assert blocker.args == ["Could not load practice questions."]


# --------------------------------------------------------------------------
# Filters: course, topic, difficulty, tag, needs-focus.
# --------------------------------------------------------------------------
def test_selecting_course_via_combo_narrows_queue_and_topic_choices(qtbot, store):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.refresh()

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    _select_course(page, cmtc_id)

    # Known fact: CMTC2341 has 50 topics in the real workbook (+"All topics").
    topics = store.list_topics(cmtc_id)
    assert len(topics) == 50
    assert page.topic_combo.count() == 51

    # Known fact: exactly 3 practice questions belong to CMTC2341.
    course_qs = store.list_practice_questions(cmtc_id)
    assert len(course_qs) == 3
    assert len(page._queue) == 3
    assert page.counter_label.text() == "3 due · 3 total"
    assert page._current is not None
    assert page._current.course_id == cmtc_id


def test_topic_filter_narrows_queue_to_a_single_question(qtbot, store):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.refresh()

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    _select_course(page, cmtc_id)

    target = next(q for q in store.list_practice_questions(cmtc_id) if q.topic_id == 3)
    _select_topic(page, 3)

    assert len(page._queue) == 1
    assert page._queue[0].question_id == target.question_id
    assert page.counter_label.text() == "1 due · 1 total"

    # Back to "All topics" restores the full course queue.
    _select_topic(page, None)
    assert len(page._queue) == 3


def test_difficulty_filter_narrows_queue_by_difficulty(qtbot, store):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.refresh()

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    _select_course(page, cmtc_id)

    idx = page.difficulty_combo.findData(2)
    assert idx != -1
    page.difficulty_combo.setCurrentIndex(idx)

    assert len(page._queue) == 1
    assert page._queue[0].difficulty == 2
    assert page.counter_label.text() == "1 due · 1 total"


def test_tag_filter_matches_case_insensitively_via_real_typing(qtbot, store):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.refresh()

    # "All courses" stays selected (the default) - across the whole real
    # workbook, exactly one practice question is tagged "glossary".
    qtbot.keyClicks(page.tag_edit, "GLOSSARY")

    assert len(page._queue) == 1
    assert "glossary" in [t.lower() for t in page._queue[0].tags]
    assert page.counter_label.text() == "1 due · 1 total"

    # Clearing the tag box restores everything.
    page.tag_edit.clear()
    assert len(page._queue) == 9


def test_needs_focus_checkbox_real_toggle_filters_to_needs_focus_topics(qtbot, store):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.show()  # a checkbox needs real on-screen geometry for a synthetic click to land
    page.refresh()

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    _select_course(page, cmtc_id)
    assert len(page._queue) == 3

    topic = store.get_topic(2)  # question_id=1 belongs to this topic
    topic.status = "Needs Focus"
    store.update_topic(topic)

    # Real checkbox toggle: a click anywhere on a QCheckBox's clickable area
    # toggles it, exactly like a user clicking it.
    qtbot.mouseClick(page.needs_focus_check, Qt.LeftButton)
    assert page.needs_focus_check.isChecked()

    assert len(page._queue) == 1
    assert page._queue[0].topic_id == 2
    assert page.counter_label.text() == "1 due · 1 total"

    # Un-toggling restores the full course queue.
    qtbot.mouseClick(page.needs_focus_check, Qt.LeftButton)
    assert not page.needs_focus_check.isChecked()
    assert len(page._queue) == 3


def test_mcq_question_renders_a_radio_button_per_option(qtbot, store):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.refresh()

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    _select_course(page, cmtc_id)
    _select_topic(page, 4)  # question_id=2 is the only MCQ under topic 4

    assert page._current.question_id == 2
    assert page._current.question_type == "MCQ"

    radios = page.options_widget.findChildren(QRadioButton)
    assert [r.text() for r in radios] == page._current.options
    assert page._radio_group is not None
    assert page._radio_group.exclusive()


# --------------------------------------------------------------------------
# Deep flow: Reveal -> Got it / Missed it, real Leitner-box math persisted.
# --------------------------------------------------------------------------
def test_reveal_and_answer_flow_updates_leitner_box_and_stats(qtbot, store):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.show()
    # The Space/1/2 shortcuts are WidgetWithChildrenShortcut-scoped to
    # card_frame, which only receives them once its top-level window is
    # the real *active* window - plain show()/waitExposed() is not enough
    # under the offscreen platform.
    assert QTest.qWaitForWindowActive(page, 2000)
    page.refresh()

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    _select_course(page, cmtc_id)
    assert len(page._queue) == 3

    today = date.today()

    # -- First question: Reveal via a real button click, then "Got it" via
    #    a real button click. --
    first = page._current
    assert first.times_attempted == 0
    assert first.box == 1

    qtbot.mouseClick(page.reveal_btn, Qt.LeftButton)
    assert page._revealed is True
    assert page.answer_frame.isVisible()
    assert page.answer_label.text() == f"Answer: {first.answer_text}"
    assert page.explanation_label.isVisible() == bool(first.explanation)
    if first.explanation:
        assert page.explanation_label.text() == first.explanation
    assert not page.reveal_btn.isEnabled()

    qtbot.mouseClick(page.gotit_btn, Qt.LeftButton)

    expected_box1 = progress.leitner_next_box(1, True)
    updated_first = store.get_practice_question(first.question_id)
    assert updated_first.box == expected_box1
    assert updated_first.times_attempted == 1
    assert updated_first.times_correct == 1
    assert updated_first.last_attempted == today
    assert updated_first.next_due == progress.leitner_next_due(expected_box1, today)

    # The queue re-sorted after the save - a DIFFERENT question is now shown.
    second = page._current
    assert second is not None
    assert second.question_id != first.question_id
    assert second.times_attempted == 0

    # -- Second (different) question: Reveal via the Space shortcut, then
    #    "Missed it" via the "2" shortcut - both scoped to card_frame. --
    qtbot.keyClick(page.card_frame, Qt.Key_Space)
    assert page._revealed is True
    assert page.answer_frame.isVisible()

    qtbot.keyClick(page.card_frame, Qt.Key_2)

    expected_box2 = progress.leitner_next_box(second.box, False)
    updated_second = store.get_practice_question(second.question_id)
    assert expected_box2 == 1  # "Missed it" always resets to box 1
    assert updated_second.box == expected_box2
    assert updated_second.times_attempted == 1
    assert updated_second.times_correct == 0  # NOT incremented on a miss
    assert updated_second.last_attempted == today
    assert updated_second.next_due == progress.leitner_next_due(expected_box2, today)

    # The untouched third question in the course is unaffected.
    third_id = next(
        q.question_id for q in store.list_practice_questions(cmtc_id)
        if q.question_id not in (first.question_id, second.question_id)
    )
    untouched = store.get_practice_question(third_id)
    assert untouched.times_attempted == 0
    assert untouched.box == 1


def test_answer_buttons_before_reveal_are_ignored(qtbot, store):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.show()
    page.refresh()

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    _select_course(page, cmtc_id)
    current = page._current
    assert not page._revealed

    qtbot.mouseClick(page.gotit_btn, Qt.LeftButton)

    unchanged = store.get_practice_question(current.question_id)
    assert unchanged.times_attempted == 0
    assert unchanged.box == current.box
    assert not page.answer_frame.isVisible()
    # Still the same current question - nothing advanced the queue.
    assert page._current.question_id == current.question_id


def test_no_questions_match_filters_shows_empty_state(qtbot, store):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.show()
    page.refresh()

    qtbot.keyClicks(page.tag_edit, "zzz-no-such-tag")

    assert page._queue == []
    assert page._current is None
    assert page.empty_label.isVisible()
    assert not page.reveal_btn.isVisible()
    assert not page.answer_frame.isVisible()
    assert page.counter_label.text() == "0 due · 0 total"


# --------------------------------------------------------------------------
# "Open coursepack questions for this topic".
# --------------------------------------------------------------------------
def test_open_coursepack_button_click_calls_open_resource_and_reports_failure(qtbot, store, monkeypatch):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.show()
    page.refresh()

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    _select_course(page, cmtc_id)
    _select_topic(page, 3)  # topic 3 has a real coursepack link in the workbook
    assert page._current.topic_id == 3
    topic = store.get_topic(3)
    assert topic.link

    assert page.open_coursepack_btn.isVisible()

    calls = []
    def fake_open_resource(store_arg, link, page_arg, parent=None):
        calls.append((store_arg, link, page_arg, parent))
        return False
    monkeypatch.setattr(practice_page_module.widgets, "open_resource", fake_open_resource)

    messages = []
    page.statusMessage.connect(messages.append)
    qtbot.mouseClick(page.open_coursepack_btn, Qt.LeftButton)

    assert calls == [(store, topic.link, topic.page, page)]
    assert messages == ["Could not open that resource."]


def test_open_coursepack_with_no_link_hides_button_and_reports_status(qtbot, store):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.show()
    page.refresh()

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    _select_course(page, cmtc_id)
    topic = store.get_topic(3)
    topic.link = ""
    store.update_topic(topic)
    _select_topic(page, 3)  # re-render now that the topic has no link

    assert page._current.topic_id == 3
    assert not page.open_coursepack_btn.isVisible()

    messages = []
    page.statusMessage.connect(messages.append)
    # The button is hidden (nothing to click), so the pre-dialog handler is
    # invoked directly, per the task's dialog-safety allowance.
    page._on_open_coursepack()
    assert messages == ["No coursepack link for this topic."]


# --------------------------------------------------------------------------
# "Manage questions" dialog: no course available, and CSV import.
# --------------------------------------------------------------------------
def test_manage_questions_with_no_courses_shows_info_and_opens_no_dialog(qtbot, empty_store, monkeypatch):
    page = PracticePage(empty_store)
    qtbot.addWidget(page)
    page.refresh()
    assert empty_store.list_courses() == []

    info_calls = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda parent, title, text: info_calls.append((title, text)),
    )

    manage_btn = _find_button(page, "Manage questions")
    qtbot.mouseClick(manage_btn, Qt.LeftButton)

    assert info_calls == [(
        "Manage questions",
        "Add a course first (Courses page) before adding practice questions.",
    )]


def test_manage_questions_import_csv_adds_new_questions_via_real_dialog_flow(qtbot, store, monkeypatch, tmp_path):
    page = PracticePage(store)
    qtbot.addWidget(page)
    page.refresh()

    cmtc_id = _course_id_by_code(store, "CMTC2341")
    before_ids = {q.question_id for q in store.list_practice_questions(cmtc_id)}
    assert len(before_ids) == 3

    csv_path = tmp_path / "import_questions.csv"
    csv_text = (
        "question,options,answer,explanation,topic,difficulty,tags\n"
        "What is TCP?,,Transmission Control Protocol,A reliable transport protocol.,2,3,networking\n"
        'Pick one,A|B|C,B,,,2,"mcq,quiz"\n'
    )
    csv_path.write_text(csv_text, encoding="utf-8")

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                         lambda *a, **k: (str(csv_path), "CSV files (*.csv)"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    def fake_exec(self):
        # _ManageQuestionsDialog's own widgets already exist by now (built
        # in __init__, before .exec() would normally block) - click its
        # real "Import CSV" toolbar button instead of calling the real,
        # blocking .exec().
        import_btn = _find_button(self, "Import CSV")
        assert self.course_combo.currentData() == cmtc_id  # defaults to the first course
        qtbot.mouseClick(import_btn, Qt.LeftButton)
        return QDialog.Accepted
    monkeypatch.setattr(_ManageQuestionsDialog, "exec", fake_exec)

    manage_btn = _find_button(page, "Manage questions")
    qtbot.mouseClick(manage_btn, Qt.LeftButton)

    after = store.list_practice_questions(cmtc_id)
    assert len(after) == 5

    new_qs = {q.question_text: q for q in after if q.question_id not in before_ids}
    assert set(new_qs) == {"What is TCP?", "Pick one"}

    tcp = new_qs["What is TCP?"]
    assert tcp.question_type == "Short Answer"
    assert tcp.topic_id == 2
    assert tcp.difficulty == 3
    assert tcp.tags == ["networking"]
    assert tcp.course_id == cmtc_id

    mcq = new_qs["Pick one"]
    assert mcq.question_type == "MCQ"
    assert mcq.options == ["A", "B", "C"]
    assert mcq.answer_text == "B"
    assert mcq.tags == ["mcq", "quiz"]
    assert mcq.course_id == cmtc_id

    # _on_manage_questions calls self.refresh() once the dialog closes, so
    # the page's own filtered queue already reflects the imported rows.
    _select_course(page, cmtc_id)
    queue_ids = {q.question_id for q in page._queue}
    assert {tcp.question_id, mcq.question_id} <= queue_ids


# --------------------------------------------------------------------------
# Edge case: a brand-new, completely empty workbook (zero courses/questions).
# --------------------------------------------------------------------------
def test_empty_store_shows_placeholder_and_zero_counts(qtbot, empty_store):
    page = PracticePage(empty_store)
    qtbot.addWidget(page)
    page.show()
    page.refresh()

    assert page.course_combo.count() == 1  # "All courses" only
    assert page.counter_label.text() == "0 due · 0 total"
    assert page._queue == []
    assert page._current is None
    assert page.empty_label.isVisible()
    assert not page.reveal_btn.isVisible()
