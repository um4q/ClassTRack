"""
Review page - calendar + brain-dump notebook (build spec §5.8).

Left: a QCalendarWidget where days with a BrainDump note are bold with a
small dotted underline, days with a lab/exam/deadline are tinted in that
course's color (tooltip lists what's on), and holidays are greyed out.

Right: a "notebook" for the calendar's selected date - a list of that day's
notes, a "New note" button, a rich-text editor with a small formatting
toolbar (autosaving to BrainDump.body_html on a 1.5s debounce), a course tag
combo + free-text tags field, a "Brain dump template" button, a read-only
agenda panel for the selected date, a search box that filters notes by
title/body across the whole term, and an "Export to .md" button.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from PySide6.QtCore import QDate, QTimer, Qt, Signal
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextListFormat, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCalendarWidget, QComboBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from app import config
from app.excel_store import ExcelStore
from app.models import BrainDumpNote
from app.services import scheduler
from app.ui.widgets import clear_layout  # noqa: F401 - kept for parity with other pages

logger = logging.getLogger("study_tracker")

_AUTOSAVE_DEBOUNCE_MS = 1500
_NOTE_DOT_COLOR = "#38bdf8"
_HIGHLIGHT_COLOR = "#fde68a"
_CODE_BG = "#1f2937"
_CODE_FG = "#93c5fd"

_TEMPLATE_HTML = (
    "<h2>What I remember</h2><p><br/></p>"
    "<h2>What I'm fuzzy on</h2><p><br/></p>"
    "<h2>Questions to ask</h2><p><br/></p>"
    "<h2>Next action</h2><p><br/></p>"
)

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_MAP = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&apos;": "'",
}


def _html_to_text(html: str) -> str:
    """Best-effort HTML -> plain text: turn block-ish tags into newlines,
    strip everything else, unescape the common entities. Used for search
    matching and for the .md export."""
    if not html:
        return ""
    text = re.sub(r"(?is)<head[^>]*>.*?</head>", "", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", "", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|li|h[1-6])>", "\n", text)
    text = re.sub(r"(?is)<li[^>]*>", "- ", text)
    text = _TAG_RE.sub("", text)
    for entity, ch in _ENTITY_MAP.items():
        text = text.replace(entity, ch)
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: list[str] = []
    blank = False
    for ln in lines:
        if ln.strip() == "":
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out).strip()


def _derive_title(plain_text: str) -> str:
    for line in plain_text.splitlines():
        line = line.strip()
        if line:
            return line[:60]
    return "Untitled note"


class ReviewPage(QWidget):
    statusMessage = Signal(str)
    navigateTo = Signal(str)

    def __init__(self, store: ExcelStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._selected_date: date = date.today()
        self._current_note: Optional[BrainDumpNote] = None
        self._loading_note = False
        self._formatted_dates: set[QDate] = set()
        self._courses: list = []
        self._course_map: dict[int, object] = {}
        self._toolbar_buttons: list[QPushButton] = []

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(_AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self._guard(self._autosave, "Couldn't autosave note - see log"))

        outer = QVBoxLayout(self)

        title = QLabel("Review")
        title.setStyleSheet("font-size: 16pt; font-weight: 700;")
        outer.addWidget(title)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 800])
        outer.addWidget(splitter, 1)

        self.calendar.selectionChanged.connect(self._guard(self._on_date_selected, "Couldn't load that date"))
        self.store.dataChanged.connect(lambda _sheet: self.refresh())

        qd = QDate(self._selected_date.year, self._selected_date.month, self._selected_date.day)
        self.calendar.setSelectedDate(qd)

        self.refresh()

    # ------------------------------------------------------------------
    # Error-safe wrapper (never let a slot raise - build spec convention).
    # ------------------------------------------------------------------
    def _guard(self, fn, message: str = "Action failed - see log"):
        def wrapped(*args, **kwargs):
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception(message)
                self.statusMessage.emit(message)
        return wrapped

    # ==================================================================
    # UI construction
    # ==================================================================
    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        v.addWidget(self.calendar)

        legend = QLabel(
            "<span style='font-weight:700; text-decoration: underline; "
            "text-decoration-style: dotted;'>Bold·dot</span> = has notes &nbsp;·&nbsp; "
            "tinted background = class / lab / exam / deadline &nbsp;·&nbsp; "
            "<span style='color:#6b7280;'>grey</span> = holiday"
        )
        legend.setWordWrap(True)
        legend.setTextFormat(Qt.RichText)
        legend.setStyleSheet("color: #94a3b8; font-size: 9pt; margin-top: 4px;")
        v.addWidget(legend)
        v.addStretch(1)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)

        # -- search ------------------------------------------------------
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search notes (title or body)…")
        self.search_edit.textChanged.connect(self._guard(
            lambda _t: self._rebuild_note_list(), "Search failed - see log"
        ))
        v.addWidget(self.search_edit)

        # -- note list -----------------------------------------------------
        list_header = QHBoxLayout()
        self.notes_header = QLabel("Notes")
        self.notes_header.setStyleSheet("font-weight: 600;")
        list_header.addWidget(self.notes_header, 1)
        new_note_btn = QPushButton("+ New note")
        new_note_btn.clicked.connect(self._guard(self._on_new_note, "Couldn't create a new note"))
        list_header.addWidget(new_note_btn)
        v.addLayout(list_header)

        self.note_list = QListWidget()
        self.note_list.setMaximumHeight(130)
        self.note_list.itemClicked.connect(self._guard(self._on_note_item_clicked, "Couldn't open note"))
        v.addWidget(self.note_list)

        # -- title ---------------------------------------------------------
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Untitled note")
        self.title_edit.textChanged.connect(self._on_editor_changed)
        v.addWidget(self.title_edit)

        # -- formatting toolbar ---------------------------------------------
        toolbar_row = QHBoxLayout()
        toolbar_row.addWidget(self._toolbar_button("B", "Bold", self._fmt_bold))
        toolbar_row.addWidget(self._toolbar_button("I", "Italic", self._fmt_italic))
        toolbar_row.addWidget(self._toolbar_button("U", "Underline", self._fmt_underline))
        toolbar_row.addWidget(self._toolbar_button("H1", "Heading 1", lambda: self._fmt_heading(1)))
        toolbar_row.addWidget(self._toolbar_button("H2", "Heading 2", lambda: self._fmt_heading(2)))
        toolbar_row.addWidget(self._toolbar_button("•", "Bullet list", self._fmt_bullet_list))
        toolbar_row.addWidget(self._toolbar_button("1.", "Numbered list", self._fmt_numbered_list))
        toolbar_row.addWidget(self._toolbar_button("☐", "Checkbox", self._fmt_checkbox))
        toolbar_row.addWidget(self._toolbar_button("HL", "Highlight", self._fmt_highlight))
        toolbar_row.addWidget(self._toolbar_button("<>", "Code", self._fmt_code))
        toolbar_row.addStretch(1)
        template_btn = QPushButton("Brain dump template")
        template_btn.setToolTip("Insert: What I remember / What I'm fuzzy on / Questions to ask / Next action")
        template_btn.clicked.connect(self._guard(self._on_template, "Couldn't insert template"))
        self._toolbar_buttons.append(template_btn)
        toolbar_row.addWidget(template_btn)
        v.addLayout(toolbar_row)

        # -- course / tags ---------------------------------------------------
        meta_row = QHBoxLayout()
        meta_row.addWidget(QLabel("Course:"))
        self.course_combo = QComboBox()
        self.course_combo.currentIndexChanged.connect(lambda _i: self._on_editor_changed())
        meta_row.addWidget(self.course_combo, 1)
        meta_row.addWidget(QLabel("Tags:"))
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("comma, separated, tags")
        self.tags_edit.textChanged.connect(self._on_editor_changed)
        meta_row.addWidget(self.tags_edit, 2)
        v.addLayout(meta_row)

        # -- editor ------------------------------------------------------
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(True)
        self.editor.textChanged.connect(self._on_editor_changed)
        v.addWidget(self.editor, 1)

        # -- agenda (read-only) -----------------------------------------
        self.agenda_header = QLabel("Agenda")
        self.agenda_header.setStyleSheet("font-weight: 600; margin-top: 4px;")
        v.addWidget(self.agenda_header)
        self.agenda_list = QListWidget()
        self.agenda_list.setMaximumHeight(140)
        self.agenda_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.agenda_list.setFocusPolicy(Qt.NoFocus)
        v.addWidget(self.agenda_list)

        # -- export --------------------------------------------------------
        export_row = QHBoxLayout()
        export_row.addStretch(1)
        self.export_btn = QPushButton("Export to .md")
        self.export_btn.clicked.connect(self._guard(self._on_export, "Couldn't export note"))
        export_row.addWidget(self.export_btn)
        v.addLayout(export_row)

        self._clear_editor()
        return panel

    def _toolbar_button(self, label: str, tooltip: str, handler) -> QPushButton:
        btn = QPushButton(label)
        btn.setToolTip(tooltip)
        btn.setMaximumWidth(36)
        btn.clicked.connect(self._guard(handler, f"Couldn't apply {tooltip.lower()}"))
        self._toolbar_buttons.append(btn)
        return btn

    # ==================================================================
    # refresh() - rebuild calendar / note list / agenda from the store.
    # Deliberately never touches the open editor's content (only explicit
    # note selection does that) so autosave round-trips don't clobber
    # in-progress typing or reset the cursor.
    # ==================================================================
    def refresh(self) -> None:
        try:
            self._courses = self.store.list_courses()
            self._course_map = {c.course_id: c for c in self._courses}
        except Exception:
            logger.exception("ReviewPage: failed to load courses")
            self._courses, self._course_map = [], {}

        try:
            self._rebuild_course_combo()
        except Exception:
            logger.exception("ReviewPage: failed to rebuild course combo")
            self.statusMessage.emit("Review page: couldn't load courses - see log")
        try:
            self._rebuild_calendar()
        except Exception:
            logger.exception("ReviewPage: failed to rebuild calendar")
            self.statusMessage.emit("Review page: couldn't update calendar - see log")
        try:
            self._rebuild_note_list()
        except Exception:
            logger.exception("ReviewPage: failed to rebuild note list")
            self.statusMessage.emit("Review page: couldn't load notes - see log")
        try:
            self._update_agenda_panel(self._selected_date)
        except Exception:
            logger.exception("ReviewPage: failed to update agenda panel")
            self.statusMessage.emit("Review page: couldn't build agenda - see log")

    # ------------------------------------------------------------------
    def _rebuild_course_combo(self) -> None:
        current_data = self.course_combo.currentData() if self.course_combo.count() else None
        self.course_combo.blockSignals(True)
        self.course_combo.clear()
        self.course_combo.addItem("(no course)", 0)
        for c in self._courses:
            self.course_combo.addItem(c.code or "?", c.course_id)
        if current_data is not None:
            idx = self.course_combo.findData(current_data)
            self.course_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.course_combo.blockSignals(False)

    # ------------------------------------------------------------------
    def _rebuild_calendar(self) -> None:
        cal = self.calendar
        for qd in list(self._formatted_dates):
            cal.setDateTextFormat(qd, QTextCharFormat())
        self._formatted_dates = set()

        try:
            notes = self.store.list_brain_dump()
        except Exception:
            logger.exception("ReviewPage: failed to load notes for calendar")
            notes = []
        notes_by_date: dict[date, list[BrainDumpNote]] = defaultdict(list)
        for n in notes:
            if n.date:
                notes_by_date[n.date].append(n)

        try:
            prelabs = self.store.list_prelabs()
            assessments = self.store.list_assessments()
            holidays = self.store.list_holidays()
        except Exception:
            logger.exception("ReviewPage: failed to load calendar event data")
            prelabs, assessments, holidays = [], [], []

        events_by_date: dict[date, list[str]] = defaultdict(list)
        color_by_date: dict[date, str] = {}
        for p in prelabs:
            if p.lab_date and p.lab_type != "None":
                c = self._course_map.get(p.course_id)
                code = c.code if c else "?"
                events_by_date[p.lab_date].append(f"{code} Lab {p.lab_number}: {p.title}")
                color_by_date.setdefault(p.lab_date, c.color_hex if c else config.DEFAULT_COURSE_COLOR)
        for a in assessments:
            if a.due_date:
                c = self._course_map.get(a.course_id)
                code = c.code if c else "?"
                text = f"{code} {a.type}: {a.title}"
                if a.is_flagged:
                    text += " ⚠"
                events_by_date[a.due_date].append(text)
                color_by_date.setdefault(a.due_date, c.color_hex if c else config.DEFAULT_COURSE_COLOR)

        holiday_by_date = {h.date: h.name for h in holidays if h.date and h.classes_cancelled}

        all_dates = set(notes_by_date) | set(events_by_date) | set(holiday_by_date)
        for d in all_dates:
            qd = QDate(d.year, d.month, d.day)
            fmt = QTextCharFormat()
            tooltip_lines: list[str] = []
            if d in holiday_by_date:
                fmt.setForeground(QColor("#6b7280"))
                tooltip_lines.append(f"Holiday: {holiday_by_date[d]}")
            elif d in color_by_date:
                bg = QColor(color_by_date[d])
                bg.setAlpha(95)
                fmt.setBackground(bg)
            if d in events_by_date:
                tooltip_lines.extend(events_by_date[d])
            if d in notes_by_date:
                fmt.setFontWeight(QFont.Bold)
                fmt.setUnderlineStyle(QTextCharFormat.DotLine)
                fmt.setUnderlineColor(QColor(_NOTE_DOT_COLOR))
                titles = ", ".join((n.title or "(untitled)") for n in notes_by_date[d][:5])
                tooltip_lines.append(f"Notes: {titles}")
            if tooltip_lines:
                fmt.setToolTip("\n".join(tooltip_lines))
            cal.setDateTextFormat(qd, fmt)
            self._formatted_dates.add(qd)

    # ------------------------------------------------------------------
    def _rebuild_note_list(self) -> None:
        self.note_list.blockSignals(True)
        self.note_list.clear()
        query = self.search_edit.text().strip().lower()
        if query:
            self.notes_header.setText(f'Search results for "{query}"')
            try:
                notes = self.store.list_brain_dump()
            except Exception:
                logger.exception("ReviewPage: search failed to load notes")
                notes = []
            matches = [
                n for n in notes
                if query in (n.title or "").lower() or query in _html_to_text(n.body_html).lower()
            ]
            for n in matches:
                when = n.date.strftime("%Y-%m-%d") if n.date else "?"
                item = QListWidgetItem(f"{when} — {n.title or '(untitled)'}")
                item.setData(Qt.UserRole, n.note_id)
                self.note_list.addItem(item)
            if not matches:
                self.note_list.addItem("(no matches)")
        else:
            self.notes_header.setText(f"Notes for {self._selected_date.strftime('%a %b %d, %Y')}")
            try:
                notes = self.store.list_brain_dump(on_date=self._selected_date)
            except Exception:
                logger.exception("ReviewPage: failed to load notes for %s", self._selected_date)
                notes = []
            for n in notes:
                item = QListWidgetItem(n.title or "(untitled)")
                item.setData(Qt.UserRole, n.note_id)
                self.note_list.addItem(item)
            if not notes:
                self.note_list.addItem("(no notes yet)")

        if self._current_note is not None:
            for i in range(self.note_list.count()):
                item = self.note_list.item(i)
                if item.data(Qt.UserRole) == self._current_note.note_id:
                    self.note_list.setCurrentItem(item)
                    break
        self.note_list.blockSignals(False)

    # ------------------------------------------------------------------
    def _update_agenda_panel(self, d: date) -> None:
        self.agenda_header.setText(f"Agenda — {d.strftime('%a %b %d, %Y')}")
        self.agenda_list.clear()
        try:
            schedule = self.store.list_schedule()
            holidays = self.store.list_holidays()
            prelabs = self.store.list_prelabs()
            assessments = self.store.list_assessments()
            items = scheduler.agenda(d, schedule, holidays, prelabs, assessments)
        except Exception:
            logger.exception("ReviewPage: failed to build agenda for %s", d)
            items = []
        for it in items:
            c = self._course_map.get(it.course_id)
            code = c.code if c else "?"
            t_s = it.start_time.strftime("%H:%M") if it.start_time else "--:--"
            text = f"{t_s} · {code} · {it.title}"
            if it.room:
                text += f" · {it.room}"
            if it.flagged:
                text += " ⚠"
            self.agenda_list.addItem(text)
        try:
            log_entries = [e for e in self.store.list_study_log() if e.date == d]
        except Exception:
            logger.exception("ReviewPage: failed to load study log for %s", d)
            log_entries = []
        for e in log_entries:
            c = self._course_map.get(e.course_id)
            code = c.code if c else "?"
            start_s = e.start_time.strftime("%H:%M") if e.start_time else ""
            text = f"{start_s} Studied · {code} · {e.minutes or 0} min · {e.activity}".strip()
            self.agenda_list.addItem(text)
        if self.agenda_list.count() == 0:
            self.agenda_list.addItem("Nothing scheduled")

    # ==================================================================
    # Calendar / note-list interaction
    # ==================================================================
    def _on_date_selected(self) -> None:
        qd = self.calendar.selectedDate()
        self._selected_date = date(qd.year(), qd.month(), qd.day())
        if self.search_edit.text():
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
        self._clear_editor()
        self._rebuild_note_list()
        self._update_agenda_panel(self._selected_date)

    def _on_note_item_clicked(self, item: QListWidgetItem) -> None:
        note_id = item.data(Qt.UserRole)
        if note_id is None:
            return
        self._open_note(note_id)

    def _open_note(self, note_id: int) -> None:
        note = self.store.get_brain_dump_note(note_id)
        if note is None:
            self.statusMessage.emit("That note no longer exists.")
            self._clear_editor()
            self._rebuild_note_list()
            return
        if note.date and note.date != self._selected_date:
            self._selected_date = note.date
            qd = QDate(note.date.year, note.date.month, note.date.day)
            self.calendar.blockSignals(True)
            self.calendar.setSelectedDate(qd)
            self.calendar.blockSignals(False)
        if self.search_edit.text():
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
        self._load_note_into_editor(note)
        self._rebuild_note_list()
        self._update_agenda_panel(self._selected_date)

    def _on_new_note(self) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        note = BrainDumpNote(
            date=self._selected_date, course_id=None, title="", body_html="",
            tags=[], created_at=now_iso, updated_at=now_iso,
        )
        saved = self.store.add_brain_dump_note(note)
        self._open_note(saved.note_id)
        self.title_edit.setFocus()

    def new_note_today(self, course_id: Optional[int] = None) -> None:
        """Cross-page contract: select today, create+open a new note (used
        by Dashboard's 'Quick brain dump' button)."""
        try:
            today = date.today()
            self._selected_date = today
            qd = QDate(today.year, today.month, today.day)
            self.calendar.blockSignals(True)
            self.calendar.setSelectedDate(qd)
            self.calendar.blockSignals(False)
            if self.search_edit.text():
                self.search_edit.blockSignals(True)
                self.search_edit.clear()
                self.search_edit.blockSignals(False)
            now_iso = datetime.now().isoformat(timespec="seconds")
            note = BrainDumpNote(
                date=today, course_id=course_id, title="", body_html="",
                tags=[], created_at=now_iso, updated_at=now_iso,
            )
            saved = self.store.add_brain_dump_note(note)
            self._load_note_into_editor(saved)
            self._rebuild_note_list()
            self._update_agenda_panel(today)
            self.title_edit.setFocus()
        except Exception:
            logger.exception("ReviewPage.new_note_today failed")
            self.statusMessage.emit("Couldn't create today's note - see log")

    # ==================================================================
    # Editor state (load / clear / autosave)
    # ==================================================================
    def _clear_editor(self) -> None:
        self._loading_note = True
        try:
            self._current_note = None
            self.title_edit.clear()
            self.editor.clear()
            self.tags_edit.clear()
            if self.course_combo.count():
                self.course_combo.setCurrentIndex(0)
            for w in (self.title_edit, self.editor, self.tags_edit, self.course_combo, self.export_btn):
                w.setEnabled(False)
            for btn in self._toolbar_buttons:
                btn.setEnabled(False)
        finally:
            self._loading_note = False

    def _load_note_into_editor(self, note: BrainDumpNote) -> None:
        self._loading_note = True
        try:
            self._current_note = note
            self.title_edit.setText(note.title or "")
            self.editor.setHtml(note.body_html or "")
            idx = self.course_combo.findData(note.course_id or 0)
            self.course_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.tags_edit.setText(", ".join(note.tags or []))
            for w in (self.title_edit, self.editor, self.tags_edit, self.course_combo, self.export_btn):
                w.setEnabled(True)
            for btn in self._toolbar_buttons:
                btn.setEnabled(True)
        finally:
            self._loading_note = False

    def _on_editor_changed(self, *args) -> None:
        if self._loading_note or self._current_note is None:
            return
        self._autosave_timer.start()

    def _autosave(self) -> None:
        if self._current_note is None:
            return
        note = self._current_note
        title = self.title_edit.text().strip()
        if not title:
            title = _derive_title(self.editor.toPlainText())
            self._loading_note = True
            try:
                self.title_edit.setText(title)
            finally:
                self._loading_note = False
        note.title = title
        note.body_html = self.editor.toHtml()
        course_id = self.course_combo.currentData()
        note.course_id = course_id if course_id else None
        note.tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        note.updated_at = datetime.now().isoformat(timespec="seconds")
        self.store.update_brain_dump_note(note)

    # ==================================================================
    # Formatting toolbar (QTextEdit's own char/list formatting API)
    # ==================================================================
    def _merge_format(self, fmt: QTextCharFormat) -> None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)

    def _fmt_bold(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Normal if self.editor.fontWeight() == QFont.Bold else QFont.Bold)
        self._merge_format(fmt)

    def _fmt_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.editor.fontItalic())
        self._merge_format(fmt)

    def _fmt_underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.editor.fontUnderline())
        self._merge_format(fmt)

    def _fmt_heading(self, level: int) -> None:
        fmt = QTextCharFormat()
        fmt.setFontPointSize(20.0 if level == 1 else 16.0)
        fmt.setFontWeight(QFont.Bold)
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.BlockUnderCursor)
        cursor.mergeCharFormat(fmt)
        self.editor.setTextCursor(cursor)
        self.editor.mergeCurrentCharFormat(fmt)

    def _fmt_bullet_list(self) -> None:
        cursor = self.editor.textCursor()
        cursor.insertList(QTextListFormat.ListDisc)
        self.editor.setTextCursor(cursor)

    def _fmt_numbered_list(self) -> None:
        cursor = self.editor.textCursor()
        cursor.insertList(QTextListFormat.ListDecimal)
        self.editor.setTextCursor(cursor)

    def _fmt_checkbox(self) -> None:
        cursor = self.editor.textCursor()
        cursor.insertText("☐ ")
        self.editor.setTextCursor(cursor)

    def _fmt_highlight(self) -> None:
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(_HIGHLIGHT_COLOR))
        fmt.setForeground(QColor("#1f2937"))
        self._merge_format(fmt)

    def _fmt_code(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontFamily("Consolas")
        fmt.setBackground(QColor(_CODE_BG))
        fmt.setForeground(QColor(_CODE_FG))
        self._merge_format(fmt)

    def _on_template(self) -> None:
        if self._current_note is None:
            self.statusMessage.emit("Open or create a note first.")
            return
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(_TEMPLATE_HTML)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    # ==================================================================
    # Export to Markdown
    # ==================================================================
    def _on_export(self) -> None:
        if self._current_note is None:
            self.statusMessage.emit("Open a note first.")
            return
        note = self._current_note
        title = self.title_edit.text().strip() or "Untitled note"
        default_name = f"{(note.date.isoformat() if note.date else 'note')}_{title}".replace(" ", "_")
        default_name = re.sub(r"[^A-Za-z0-9_\-]", "", default_name)[:80] + ".md"
        path, _ = QFileDialog.getSaveFileName(self, "Export note to Markdown", default_name, "Markdown files (*.md)")
        if not path:
            return
        lines = [f"# {title}", ""]
        if note.date:
            lines.append(f"*{note.date.strftime('%A, %B %d, %Y')}*")
        course_id = self.course_combo.currentData()
        if course_id:
            c = self._course_map.get(course_id)
            if c:
                lines.append(f"*Course: {c.code} — {c.name}*")
        tags_text = self.tags_edit.text().strip()
        if tags_text:
            lines.append(f"*Tags: {tags_text}*")
        lines.append("")
        lines.append(_html_to_text(self.editor.toHtml()))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self.statusMessage.emit(f"Exported to {path}")
