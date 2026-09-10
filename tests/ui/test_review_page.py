"""Tests for app.ui.review_page.ReviewPage - the calendar + brain-dump
notebook (build spec §5.8).

Facts about "today" and about the note list are derived from the same
``store`` the page reads from (rather than hardcoded) since the real
workbook's one existing BrainDump note is dated 2026-09-02 and "today"
depends on when this suite runs.
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFileDialog, QListWidget, QPushButton

from app.excel_store import ExcelStore
from app.ui.review_page import ReviewPage

KNOWN_NOTE_DATE = date(2026, 9, 2)
KNOWN_NOTE_TITLE = "Semester kickoff"


def _buttons_with_text(page: ReviewPage, text: str) -> list[QPushButton]:
    return [b for b in page.findChildren(QPushButton) if b.text() == text]


def _click_list_item(qtbot, list_widget: QListWidget, item) -> None:
    """A real mouse click landing on a specific QListWidget row (not a call
    into the item's own click handler) - needs the widget to actually be
    laid out on screen for visualItemRect() to be meaningful."""
    rect = list_widget.visualItemRect(item)
    qtbot.mouseClick(list_widget.viewport(), Qt.LeftButton, pos=rect.center())


def _find_note_item(list_widget: QListWidget, note_id: int):
    for i in range(list_widget.count()):
        item = list_widget.item(i)
        if item.data(Qt.UserRole) == note_id:
            return item
    return None


# --------------------------------------------------------------------------
# Baseline: construct with the real store, refresh(), check concrete facts.
# --------------------------------------------------------------------------
def test_review_page_renders_real_workbook_data_on_refresh(qtbot, store):
    page = ReviewPage(store)
    qtbot.addWidget(page)

    page.refresh()  # must not raise

    # Course combo: "(no course)" placeholder + every real course code.
    assert page.course_combo.itemText(0) == "(no course)"
    assert page.course_combo.itemData(0) == 0
    combo_codes = {page.course_combo.itemText(i) for i in range(page.course_combo.count())}
    real_courses = store.list_courses()
    assert real_courses, "expected real courses in the supplied workbook"
    for course in real_courses:
        assert course.code in combo_codes

    # The one real BrainDump note (2026-09-02, "Semester kickoff") is
    # rendered as a bold/dotted-underline day on the calendar, with its
    # title in the day's tooltip.
    qd = QDate(KNOWN_NOTE_DATE.year, KNOWN_NOTE_DATE.month, KNOWN_NOTE_DATE.day)
    assert qd in page._formatted_dates
    fmt = page.calendar.dateTextFormat(qd)
    assert fmt.fontWeight() == QFont.Bold
    assert f"Notes: {KNOWN_NOTE_TITLE}" in fmt.toolTip()

    # "+ New note" and "Export to .md" buttons exist exactly once.
    assert len(_buttons_with_text(page, "+ New note")) == 1
    assert len(_buttons_with_text(page, "Export to .md")) == 1

    # Default selected date is today, and (barring a coincidence) the real
    # workbook has no note for today, so the notes panel shows the
    # empty-state placeholder for today's date.
    today = date.today()
    assert page._selected_date == today
    assert page.notes_header.text() == f"Notes for {today.strftime('%a %b %d, %Y')}"
    if not store.list_brain_dump(on_date=today):
        assert page.note_list.count() == 1
        assert page.note_list.item(0).text() == "(no notes yet)"


# --------------------------------------------------------------------------
# Edge case: a brand-new, completely empty workbook.
# --------------------------------------------------------------------------
def test_review_page_with_empty_store_shows_empty_placeholders(qtbot, empty_store):
    page = ReviewPage(empty_store)
    qtbot.addWidget(page)

    page.refresh()

    assert page.course_combo.count() == 1  # only "(no course)"
    assert page._formatted_dates == set()
    assert page.note_list.count() == 1
    assert page.note_list.item(0).text() == "(no notes yet)"
    assert page.agenda_list.count() == 1
    assert page.agenda_list.item(0).text() == "Nothing scheduled"
    # Editor starts disabled until a note is created or opened.
    assert not page.editor.isEnabled()
    assert not page.export_btn.isEnabled()


# --------------------------------------------------------------------------
# Search box: real typing filters the note list to title/body matches only.
# --------------------------------------------------------------------------
def test_search_box_filters_notes_to_title_substring_via_real_typing(qtbot, store):
    page = ReviewPage(store)
    qtbot.addWidget(page)
    page.refresh()

    qtbot.keyClicks(page.search_edit, "kickoff")

    assert page.notes_header.text() == 'Search results for "kickoff"'
    assert page.note_list.count() == 1
    item = page.note_list.item(0)
    assert KNOWN_NOTE_TITLE in item.text()
    assert KNOWN_NOTE_DATE.strftime("%Y-%m-%d") in item.text()

    # A query matching nothing shows the "(no matches)" placeholder.
    page.search_edit.clear()
    qtbot.keyClicks(page.search_edit, "zzz-no-such-note-zzz")
    assert page.note_list.count() == 1
    assert page.note_list.item(0).text() == "(no matches)"

    # Clearing the search box restores the date-scoped note list.
    page.search_edit.clear()
    today = date.today()
    assert page.notes_header.text() == f"Notes for {today.strftime('%a %b %d, %Y')}"


# --------------------------------------------------------------------------
# "+ New note" button: a real click creates a row that persists to disk.
# --------------------------------------------------------------------------
def test_new_note_button_click_creates_and_persists_note_to_workbook(qtbot, store, workbook_path):
    page = ReviewPage(store)
    qtbot.addWidget(page)
    page.refresh()

    today = date.today()
    assert store.list_brain_dump(on_date=today) == [], "expected no note for today yet"

    new_note_buttons = _buttons_with_text(page, "+ New note")
    assert len(new_note_buttons) == 1

    qtbot.mouseClick(new_note_buttons[0], Qt.LeftButton)

    todays_notes = store.list_brain_dump(on_date=today)
    assert len(todays_notes) == 1
    created = todays_notes[0]
    assert created.title == ""
    assert created.course_id is None
    assert created.tags == []
    assert page._current_note is not None
    assert page._current_note.note_id == created.note_id
    assert page.editor.isEnabled()
    assert page.export_btn.isEnabled()

    # Persists to the actual on-disk workbook, not just the in-memory cache.
    store.save(force=True)
    fresh = ExcelStore(workbook_path)
    fresh.load()
    fresh_notes = fresh.list_brain_dump(on_date=today)
    assert len(fresh_notes) == 1
    assert fresh_notes[0].note_id == created.note_id


# --------------------------------------------------------------------------
# new_note_today() - the cross-page contract used by Dashboard's "Quick
# brain dump" button.
# --------------------------------------------------------------------------
def test_new_note_today_creates_note_for_today_with_given_course(qtbot, store):
    page = ReviewPage(store)
    qtbot.addWidget(page)
    page.refresh()

    course_id = store.list_courses()[0].course_id
    today = date.today()

    page.new_note_today(course_id=course_id)

    todays_notes = store.list_brain_dump(on_date=today)
    assert len(todays_notes) == 1
    assert todays_notes[0].course_id == course_id

    assert page._selected_date == today
    assert page.calendar.selectedDate() == QDate(today.year, today.month, today.day)
    assert page._current_note is not None
    assert page._current_note.note_id == todays_notes[0].note_id
    assert page.course_combo.currentData() == course_id
    assert page.editor.isEnabled()


# --------------------------------------------------------------------------
# Deep flow: create a note, select it via a real list click, type real
# keystrokes into the rich-text editor, run the debounce handler directly
# (no sleeping), and confirm the store picked up both the title and body.
# Then apply two real toolbar-button clicks (Bold, H1) and confirm the
# resulting Qt rich-text markup shows up in the persisted body_html.
# --------------------------------------------------------------------------
def test_editing_a_note_via_click_typing_and_toolbar_formatting_autosaves_to_store(qtbot, store):
    page = ReviewPage(store)
    qtbot.addWidget(page)
    page.refresh()
    page.show()
    qtbot.waitExposed(page)

    page.new_note_today()
    note_id = page._current_note.note_id

    # Select the note via a real click on its note-list row (not by calling
    # the click handler directly).
    item = _find_note_item(page.note_list, note_id)
    assert item is not None
    _click_list_item(qtbot, page.note_list, item)
    assert page._current_note is not None
    assert page._current_note.note_id == note_id

    # Real keystrokes into the QTextEdit.
    page.editor.setFocus()
    qtbot.keyClicks(page.editor, "hello brain dump")
    assert page.editor.toPlainText() == "hello brain dump"
    assert page._autosave_timer.isActive(), "typing should arm the debounce timer"

    # Run the debounce handler directly instead of sleeping 1.5s.
    page._autosave_timer.stop()
    page._autosave_timer.timeout.emit()

    saved = store.get_brain_dump_note(note_id)
    assert saved is not None
    assert "hello brain dump" in saved.body_html
    # Title was left blank, so autosave derives it from the body text.
    assert saved.title == "hello brain dump"
    assert page.title_edit.text() == "hello brain dump"

    # Two real toolbar-button clicks: Bold, then Heading 1.
    bold_buttons = _buttons_with_text(page, "B")
    h1_buttons = _buttons_with_text(page, "H1")
    assert len(bold_buttons) == 1
    assert len(h1_buttons) == 1
    assert bold_buttons[0].toolTip() == "Bold"
    assert h1_buttons[0].toolTip() == "Heading 1"

    qtbot.mouseClick(bold_buttons[0], Qt.LeftButton)
    qtbot.mouseClick(h1_buttons[0], Qt.LeftButton)

    page._autosave_timer.stop()
    page._autosave_timer.timeout.emit()

    saved_after_formatting = store.get_brain_dump_note(note_id)
    assert saved_after_formatting is not None
    # Bold -> font-weight:700 in Qt's own rich-text HTML; Heading 1 ->
    # a 20pt font-size applied to the whole block under the cursor.
    assert "font-weight:700" in saved_after_formatting.body_html
    assert "font-size:20pt" in saved_after_formatting.body_html


# --------------------------------------------------------------------------
# Export to .md: a real click, with QFileDialog patched out (never call
# .exec()/open a native dialog in a test), writes the file and emits
# statusMessage with the chosen path.
# --------------------------------------------------------------------------
def test_export_button_click_writes_markdown_file_and_emits_status_message(qtbot, store, monkeypatch, tmp_path):
    page = ReviewPage(store)
    qtbot.addWidget(page)
    page.refresh()

    page.new_note_today()
    page.title_edit.setText("My export test note")

    out_path = tmp_path / "exported_note.md"
    monkeypatch.setattr(
        "app.ui.review_page.QFileDialog.getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(out_path), "Markdown files (*.md)")),
    )

    export_buttons = _buttons_with_text(page, "Export to .md")
    assert len(export_buttons) == 1

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        qtbot.mouseClick(export_buttons[0], Qt.LeftButton)
    assert blocker.args == [f"Exported to {out_path}"]

    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert content.startswith("# My export test note")


# --------------------------------------------------------------------------
# statusMessage also fires (without any dialog involved) when a page-level
# refresh step fails - exercised here via the "note vanished" guard path,
# reached by asking the page to open a note_id that no longer exists.
# --------------------------------------------------------------------------
def test_opening_a_deleted_note_id_emits_status_message_and_clears_editor(qtbot, store):
    page = ReviewPage(store)
    qtbot.addWidget(page)
    page.refresh()

    page.new_note_today()
    note_id = page._current_note.note_id
    assert store.delete_brain_dump_note(note_id)

    with qtbot.waitSignal(page.statusMessage, timeout=2000) as blocker:
        page._open_note(note_id)
    assert blocker.args == ["That note no longer exists."]
    assert page._current_note is None
    assert not page.editor.isEnabled()
