"""
Practice page (build spec §5.6): spaced-repetition flash-card practice over
PracticeQuestions, filtered by course/topic/difficulty/tag/due/needs-focus,
plus a "Manage questions" dialog for full CRUD + CSV/pasted-text import and
CSV export.

Nothing here is hardcoded per course/topic/question - every combo, filter,
and the question queue itself are rebuilt from ``store.list_*`` results at
refresh()/filter-change time (build spec §1 "Dynamic buttons").
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QTableView,
    QVBoxLayout, QWidget,
)

from app import config, models
from app.excel_store import ExcelStore
from app.models import Course, PracticeQuestion, Topic
from app.services import importers, progress
from app.ui import dialogs, table_models, widgets
from app.ui.table_models import ColumnSpec

log = logging.getLogger("study_tracker")

# Extra columns (beyond table_models.PRACTICE_QUESTION_COLUMNS) shown only in
# the Add/Edit dialogs of the "Manage questions" panel, so a question can
# actually be authored by hand (options/answer/explanation/topic/tags) and
# not just via CSV/pasted import. The table itself uses the plain predefined
# PRACTICE_QUESTION_COLUMNS list, per the shared table-model convention.
_EDIT_COLUMNS: list[ColumnSpec] = table_models.PRACTICE_QUESTION_COLUMNS + [
    ColumnSpec("topic_id", "Topic ID", kind="int", editable=True, width=70),
    ColumnSpec("options", "Options (comma sep., MCQ only)", editable=True),
    ColumnSpec("answer_text", "Answer", editable=True),
    ColumnSpec("explanation", "Explanation", editable=True),
    ColumnSpec("tags", "Tags (comma sep.)", editable=True),
]


def _is_due(q: PracticeQuestion, today: date) -> bool:
    """A question with no next_due has never been attempted - treat it as
    always due, same convention as new spaced-repetition cards."""
    return q.next_due is None or q.next_due <= today


def _prompt_pasted_text(parent: Optional[QWidget]) -> Optional[str]:
    """A small QPlainTextEdit input dialog for pasted Q:/A:/E: blocks."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Import pasted questions")
    dlg.resize(520, 380)
    layout = QVBoxLayout(dlg)
    hint = QLabel(
        "Paste one or more blocks, separated by a blank line:\n\n"
        "Q: question text\nA: answer text\nE: explanation (optional)"
    )
    hint.setWordWrap(True)
    layout.addWidget(hint)
    editor = QPlainTextEdit()
    layout.addWidget(editor, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    if dlg.exec() == QDialog.Accepted:
        return editor.toPlainText()
    return None


class PracticePage(QWidget):
    """Top-level "Practice" page: filtered flash-card queue + question bank
    management (build spec §5.6)."""

    statusMessage = Signal(str)
    navigateTo = Signal(str)

    def __init__(self, store: ExcelStore, parent=None):
        super().__init__(parent)
        self.store = store

        self._courses: list[Course] = []
        self._course_map: dict[int, str] = {}
        self._topics_by_id: dict[int, Topic] = {}
        self._queue: list[PracticeQuestion] = []
        self._current: Optional[PracticeQuestion] = None
        self._revealed: bool = False
        self._radio_group: Optional[QButtonGroup] = None
        self._suspend_filter_signals = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        title = QLabel("Practice")
        title.setStyleSheet("font-size: 16pt; font-weight: 700;")
        outer.addWidget(title)

        # ---- filter bar ----
        filt = QHBoxLayout()
        filt.addWidget(QLabel("Course:"))
        self.course_combo = QComboBox()
        self.course_combo.currentIndexChanged.connect(self._on_course_changed)
        filt.addWidget(self.course_combo)

        filt.addWidget(QLabel("Topic:"))
        self.topic_combo = QComboBox()
        self.topic_combo.currentIndexChanged.connect(self._on_filters_changed)
        filt.addWidget(self.topic_combo)

        filt.addWidget(QLabel("Difficulty:"))
        self.difficulty_combo = QComboBox()
        self.difficulty_combo.addItem("All", None)
        for d in range(1, 6):
            self.difficulty_combo.addItem(str(d), d)
        self.difficulty_combo.currentIndexChanged.connect(self._on_filters_changed)
        filt.addWidget(self.difficulty_combo)

        filt.addWidget(QLabel("Tag:"))
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("filter by tag…")
        self.tag_edit.setMaximumWidth(140)
        self.tag_edit.textChanged.connect(self._on_filters_changed)
        filt.addWidget(self.tag_edit)

        self.due_today_check = QCheckBox("Due today")
        self.due_today_check.toggled.connect(self._on_filters_changed)
        filt.addWidget(self.due_today_check)

        self.needs_focus_check = QCheckBox("Needs Focus topics only")
        self.needs_focus_check.toggled.connect(self._on_filters_changed)
        filt.addWidget(self.needs_focus_check)

        filt.addStretch(1)

        self.counter_label = QLabel("0 due · 0 total")
        self.counter_label.setStyleSheet("font-weight: 700;")
        filt.addWidget(self.counter_label)

        manage_btn = QPushButton("Manage questions")
        manage_btn.clicked.connect(self._on_manage_questions)
        filt.addWidget(manage_btn)

        outer.addLayout(filt)

        # ---- question card ----
        self.card_frame = QFrame()
        self.card_frame.setObjectName("Card")
        self.card_frame.setFocusPolicy(Qt.StrongFocus)
        card_layout = QVBoxLayout(self.card_frame)

        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet("color: #94a3b8; font-weight: 600;")
        card_layout.addWidget(self.meta_label)

        self.question_label = QLabel("")
        self.question_label.setWordWrap(True)
        self.question_label.setStyleSheet("font-size: 13pt; font-weight: 700;")
        card_layout.addWidget(self.question_label)

        self.options_widget = QWidget()
        self.options_layout = QVBoxLayout(self.options_widget)
        self.options_layout.setContentsMargins(12, 4, 0, 4)
        card_layout.addWidget(self.options_widget)

        top_btn_row = QHBoxLayout()
        self.reveal_btn = QPushButton("Reveal (Space)")
        self.reveal_btn.clicked.connect(self._on_reveal)
        top_btn_row.addWidget(self.reveal_btn)
        self.open_coursepack_btn = QPushButton("Open coursepack questions for this topic")
        self.open_coursepack_btn.clicked.connect(self._on_open_coursepack)
        self.open_coursepack_btn.setVisible(False)
        top_btn_row.addWidget(self.open_coursepack_btn)
        top_btn_row.addStretch(1)
        card_layout.addLayout(top_btn_row)

        self.answer_frame = QWidget()
        answer_layout = QVBoxLayout(self.answer_frame)
        answer_layout.setContentsMargins(0, 6, 0, 0)
        self.answer_label = QLabel("")
        self.answer_label.setWordWrap(True)
        self.answer_label.setStyleSheet("font-weight: 600;")
        answer_layout.addWidget(self.answer_label)
        self.explanation_label = QLabel("")
        self.explanation_label.setWordWrap(True)
        answer_layout.addWidget(self.explanation_label)

        grade_row = QHBoxLayout()
        self.gotit_btn = QPushButton("Got it (1)")
        self.gotit_btn.setStyleSheet(
            f"QPushButton {{ background-color: {config.TOPIC_STATUS_COLORS['Mastered']}; "
            f"color: white; font-weight: 700; padding: 6px 16px; border-radius: 4px; }}"
        )
        self.gotit_btn.clicked.connect(lambda: self._on_answer(True))
        self.missedit_btn = QPushButton("Missed it (2)")
        self.missedit_btn.setStyleSheet(
            f"QPushButton {{ background-color: {config.URGENCY_OVERDUE}; "
            f"color: white; font-weight: 700; padding: 6px 16px; border-radius: 4px; }}"
        )
        self.missedit_btn.clicked.connect(lambda: self._on_answer(False))
        grade_row.addWidget(self.gotit_btn)
        grade_row.addWidget(self.missedit_btn)
        grade_row.addStretch(1)
        answer_layout.addLayout(grade_row)

        card_layout.addWidget(self.answer_frame)
        self.answer_frame.setVisible(False)

        self.empty_label = QLabel("No questions match your filters.")
        self.empty_label.setStyleSheet("color: #94a3b8; padding: 24px; font-size: 12pt;")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setVisible(False)
        card_layout.addWidget(self.empty_label)

        card_layout.addStretch(1)
        outer.addWidget(self.card_frame, 1)

        # ---- keyboard shortcuts ----
        # Scoped (WidgetWithChildrenShortcut) to the question card itself, so
        # typing " ", "1" or "2" into the Tag filter QLineEdit (a sibling,
        # not a descendant of the card) is never hijacked as a shortcut.
        self._reveal_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self.card_frame)
        self._reveal_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._reveal_shortcut.activated.connect(self._on_reveal)

        self._gotit_shortcut = QShortcut(QKeySequence(Qt.Key_1), self.card_frame)
        self._gotit_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._gotit_shortcut.activated.connect(lambda: self._on_answer(True))

        self._missedit_shortcut = QShortcut(QKeySequence(Qt.Key_2), self.card_frame)
        self._missedit_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._missedit_shortcut.activated.connect(lambda: self._on_answer(False))

        self.refresh()

    # ----------------------------------------------------------------
    # refresh / filters
    # ----------------------------------------------------------------
    def refresh(self) -> None:
        try:
            prev_course_id = self.course_combo.currentData() if self.course_combo.count() else None
            prev_topic_id = self.topic_combo.currentData() if self.topic_combo.count() else None

            self._courses = self.store.list_courses()
            self._course_map = {c.course_id: (c.code or "?") for c in self._courses}

            self._suspend_filter_signals = True
            try:
                self.course_combo.blockSignals(True)
                self.course_combo.clear()
                self.course_combo.addItem("All courses", None)
                for c in self._courses:
                    label = f"{c.code} — {c.name}" if c.name else (c.code or f"Course {c.course_id}")
                    self.course_combo.addItem(label, c.course_id)
                idx = self.course_combo.findData(prev_course_id) if prev_course_id is not None else 0
                self.course_combo.setCurrentIndex(idx if idx >= 0 else 0)
                self.course_combo.blockSignals(False)

                self._rebuild_topic_combo(prev_topic_id)
            finally:
                self._suspend_filter_signals = False

            self._apply_filters()
        except Exception:
            log.exception("PracticePage.refresh: failed")
            self.statusMessage.emit("Could not load practice questions.")

    def _rebuild_topic_combo(self, prev_topic_id: Optional[int]) -> None:
        course_id = self.course_combo.currentData()
        topics = self.store.list_topics(course_id)
        topics = sorted(topics, key=lambda t: (t.unit or "", t.section or "", t.title or ""))
        self.topic_combo.blockSignals(True)
        self.topic_combo.clear()
        self.topic_combo.addItem("All topics", None)
        for t in topics:
            label = f"{t.section} {t.title}".strip() if t.section else (t.title or f"Topic {t.topic_id}")
            self.topic_combo.addItem(label, t.topic_id)
        idx = self.topic_combo.findData(prev_topic_id) if prev_topic_id is not None else 0
        self.topic_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.topic_combo.blockSignals(False)

    def _on_course_changed(self, _idx=None) -> None:
        if self._suspend_filter_signals:
            return
        try:
            self._suspend_filter_signals = True
            try:
                self._rebuild_topic_combo(None)  # new course -> reset to "All topics"
            finally:
                self._suspend_filter_signals = False
            self._apply_filters()
        except Exception:
            log.exception("PracticePage: course filter change failed")
            self.statusMessage.emit("Could not apply the course filter.")

    def _on_filters_changed(self, *_args) -> None:
        if self._suspend_filter_signals:
            return
        try:
            self._apply_filters()
        except Exception:
            log.exception("PracticePage: filter change failed")
            self.statusMessage.emit("Could not apply the filters.")

    def _apply_filters(self) -> None:
        today = date.today()
        course_id = self.course_combo.currentData()
        topic_id = self.topic_combo.currentData()
        difficulty = self.difficulty_combo.currentData()
        tag_query = (self.tag_edit.text() or "").strip().lower()
        needs_focus_only = self.needs_focus_check.isChecked()
        due_only = self.due_today_check.isChecked()

        self._topics_by_id = {t.topic_id: t for t in self.store.list_topics(course_id)}
        questions = self.store.list_practice_questions(course_id)

        base: list[PracticeQuestion] = []
        for q in questions:
            if topic_id is not None and q.topic_id != topic_id:
                continue
            if difficulty is not None and q.difficulty != difficulty:
                continue
            if tag_query and not any(tag_query in (t or "").lower() for t in (q.tags or [])):
                continue
            if needs_focus_only:
                topic = self._topics_by_id.get(q.topic_id) if q.topic_id else None
                if topic is None or topic.status != "Needs Focus":
                    continue
            base.append(q)

        due_count = sum(1 for q in base if _is_due(q, today))
        self.counter_label.setText(f"{due_count} due · {len(base)} total")

        queue = [q for q in base if _is_due(q, today)] if due_only else list(base)
        queue.sort(key=lambda q: (q.next_due or date.min, -(q.difficulty or 0)))
        self._queue = queue
        self._show_next()

    # ----------------------------------------------------------------
    # question card
    # ----------------------------------------------------------------
    def _show_next(self) -> None:
        self._revealed = False
        widgets.clear_layout(self.options_layout)
        self._radio_group = None

        if not self._queue:
            self._current = None
            self.meta_label.setText("")
            self.question_label.setText("")
            self.reveal_btn.setVisible(False)
            self.open_coursepack_btn.setVisible(False)
            self.answer_frame.setVisible(False)
            self.empty_label.setVisible(True)
            return

        self.empty_label.setVisible(False)
        candidate = self._queue[0]
        # Re-fetch the freshest copy in case another page changed it since
        # the filtered list was built.
        fresh = self.store.get_practice_question(candidate.question_id)
        self._current = fresh if fresh is not None else candidate
        self._render_question(self._current)
        self.card_frame.setFocus()

    def _render_question(self, q: PracticeQuestion) -> None:
        topic = self._topics_by_id.get(q.topic_id) if q.topic_id else None
        if topic is None and q.topic_id:
            # Filter scope may not include the topic (e.g. "All courses");
            # fall back to a direct lookup rather than showing nothing.
            topic = self.store.get_topic(q.topic_id)

        meta_bits = [
            self._course_map.get(q.course_id, "?"),
            q.question_type,
            f"Difficulty {q.difficulty}",
            f"Box {q.box}",
        ]
        if topic is not None and topic.title:
            meta_bits.append(topic.title)
        self.meta_label.setText(" · ".join(b for b in meta_bits if b))

        self.question_label.setText(q.question_text or "(no question text)")

        widgets.clear_layout(self.options_layout)
        self._radio_group = None
        if q.question_type == "MCQ" and q.options:
            self._radio_group = QButtonGroup(self)
            self._radio_group.setExclusive(True)
            for i, opt in enumerate(q.options):
                rb = QRadioButton(opt)
                self._radio_group.addButton(rb, i)
                self.options_layout.addWidget(rb)

        self.reveal_btn.setVisible(True)
        self.reveal_btn.setEnabled(True)
        self.answer_frame.setVisible(False)
        self.answer_label.setText("")
        self.explanation_label.setText("")

        has_link = topic is not None and bool(topic.link)
        self.open_coursepack_btn.setVisible(has_link)
        self.open_coursepack_btn.setEnabled(has_link)

    def _on_reveal(self) -> None:
        try:
            if self._current is None or self._revealed:
                return
            self._revealed = True
            answer = self._current.answer_text or "(no answer recorded)"
            self.answer_label.setText(f"Answer: {answer}")
            self.explanation_label.setText(self._current.explanation or "")
            self.explanation_label.setVisible(bool(self._current.explanation))
            self.answer_frame.setVisible(True)
            self.reveal_btn.setEnabled(False)
        except Exception:
            log.exception("PracticePage: reveal failed")
            self.statusMessage.emit("Could not reveal the answer.")

    def _on_answer(self, correct: bool) -> None:
        try:
            if self._current is None or not self._revealed:
                return
            updated = progress.practice_answer(self._current, correct, date.today())
            self.store.update_practice_question(updated)
            self._apply_filters()
        except Exception:
            log.exception("PracticePage: recording answer failed for question_id=%s",
                          getattr(self._current, "question_id", "?"))
            self.statusMessage.emit("Could not save that answer.")

    def _on_open_coursepack(self) -> None:
        try:
            if self._current is None or not self._current.topic_id:
                return
            topic = self.store.get_topic(self._current.topic_id)
            if topic is None or not topic.link:
                self.statusMessage.emit("No coursepack link for this topic.")
                return
            if not widgets.open_resource(self.store, topic.link, topic.page, parent=self):
                self.statusMessage.emit("Could not open that resource.")
        except Exception:
            log.exception("PracticePage: open coursepack questions failed")
            self.statusMessage.emit("Could not open the coursepack.")

    # ----------------------------------------------------------------
    # Manage questions
    # ----------------------------------------------------------------
    def _on_manage_questions(self) -> None:
        try:
            if not self._courses:
                QMessageBox.information(
                    self, "Manage questions",
                    "Add a course first (Courses page) before adding practice questions.",
                )
                return
            initial_course_id = self.course_combo.currentData()
            dlg = _ManageQuestionsDialog(
                self.store, self._courses, initial_course_id, self.statusMessage.emit, parent=self
            )
            dlg.exec()
            self.refresh()
        except Exception:
            log.exception("PracticePage: manage-questions dialog failed")
            self.statusMessage.emit("Could not open the manage-questions dialog.")


# --------------------------------------------------------------------------
# "Manage questions" sub-panel dialog
# --------------------------------------------------------------------------
class _ManageQuestionsDialog(QDialog):
    """Full CRUD table over one course's PracticeQuestions rows, plus CSV /
    pasted-text import and CSV export (build spec §5.6 "Manage: ...")."""

    def __init__(self, store: ExcelStore, courses: list[Course], initial_course_id: Optional[int],
                 on_status: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.store = store
        self._on_status = on_status
        self.setWindowTitle("Manage practice questions")
        self.resize(880, 560)

        outer = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Course:"))
        self.course_combo = QComboBox()
        for c in courses:
            label = f"{c.code} — {c.name}" if c.name else (c.code or f"Course {c.course_id}")
            self.course_combo.addItem(label, c.course_id)
        start_idx = 0
        if initial_course_id is not None:
            found = self.course_combo.findData(initial_course_id)
            if found >= 0:
                start_idx = found
        if self.course_combo.count():
            self.course_combo.setCurrentIndex(start_idx)
        self.course_combo.currentIndexChanged.connect(self._reload_table)
        top.addWidget(self.course_combo, 1)
        outer.addLayout(top)

        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Add")
        add_btn.clicked.connect(self._on_add)
        edit_btn = QPushButton("Edit selected")
        edit_btn.clicked.connect(self._on_edit)
        del_btn = QPushButton("Delete selected")
        del_btn.clicked.connect(self._on_delete)
        import_csv_btn = QPushButton("Import CSV")
        import_csv_btn.clicked.connect(self._on_import_csv)
        import_paste_btn = QPushButton("Import pasted")
        import_paste_btn.clicked.connect(self._on_import_pasted)
        export_csv_btn = QPushButton("Export CSV")
        export_csv_btn.clicked.connect(self._on_export_csv)
        for b in (add_btn, edit_btn, del_btn, import_csv_btn, import_paste_btn, export_csv_btn):
            toolbar.addWidget(b)
        toolbar.addStretch(1)
        outer.addLayout(toolbar)

        self.model = table_models.DataclassTableModel(table_models.PRACTICE_QUESTION_COLUMNS)
        self.model.on_edit = self._on_inline_edit
        self.table = QTableView()
        self.table.setModel(self.model)
        table_models.apply_delegates(self.table, self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.doubleClicked.connect(lambda _idx: self._on_edit())
        outer.addWidget(self.table, 1)

        close_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_box.rejected.connect(self.reject)
        outer.addWidget(close_box)

        self._reload_table()

    # -- helpers ------------------------------------------------------
    def _current_course_id(self) -> Optional[int]:
        return self.course_combo.currentData() if self.course_combo.count() else None

    def _report(self, message: str) -> None:
        try:
            self._on_status(message)
        except Exception:
            pass

    def _reload_table(self, *_args) -> None:
        try:
            cid = self._current_course_id()
            rows = self.store.list_practice_questions(cid) if cid is not None else []
            self.model.set_rows(rows)
        except Exception:
            log.exception("Manage questions: could not reload the table")
            self._report("Could not load practice questions.")

    def _selected_row(self) -> Optional[PracticeQuestion]:
        sel = self.table.selectionModel()
        if sel is None:
            return None
        rows = sel.selectedRows()
        if not rows:
            return None
        return self.model.row_object(rows[0].row())

    # -- CRUD -----------------------------------------------------------
    def _on_inline_edit(self, obj, attr, value):
        try:
            self.store.update_practice_question(obj)
            return True
        except Exception:
            log.exception("Manage questions: inline edit failed for question_id=%s",
                          getattr(obj, "question_id", "?"))
            self._report("Could not save that change.")
            return False

    def _on_add(self) -> None:
        try:
            cid = self._current_course_id()
            if cid is None:
                QMessageBox.information(self, "Add question", "Add a course first (Courses page).")
                return
            result = dialogs.edit_row(
                self, "Add practice question", _EDIT_COLUMNS, models.PracticeQuestion(course_id=cid),
                exclude_attrs={"question_id", "course_id"},
                multiline_attrs={"question_text", "answer_text", "explanation"},
            )
            if result is None:
                return
            result.course_id = cid
            self.store.add_practice_question(result)
            self._reload_table()
        except Exception:
            log.exception("Manage questions: add failed")
            self._report("Could not add the question.")

    def _on_edit(self) -> None:
        try:
            obj = self._selected_row()
            if obj is None:
                QMessageBox.information(self, "Edit question", "Select a question first.")
                return
            result = dialogs.edit_row(
                self, "Edit practice question", _EDIT_COLUMNS, obj,
                exclude_attrs={"question_id", "course_id"},
                multiline_attrs={"question_text", "answer_text", "explanation"},
            )
            if result is None:
                return
            result.question_id = obj.question_id
            result.course_id = obj.course_id
            self.store.update_practice_question(result)
            self._reload_table()
        except Exception:
            log.exception("Manage questions: edit failed for question_id=%s",
                          getattr(obj, "question_id", "?") if 'obj' in locals() else "?")
            self._report("Could not save the question.")

    def _on_delete(self) -> None:
        try:
            obj = self._selected_row()
            if obj is None:
                QMessageBox.information(self, "Delete question", "Select a question first.")
                return
            if not widgets.confirm(self, "Delete this practice question?"):
                return
            self.store.delete_practice_question(obj.question_id)
            self._reload_table()
        except Exception:
            log.exception("Manage questions: delete failed")
            self._report("Could not delete the question.")

    # -- import / export -------------------------------------------------
    def _on_import_csv(self) -> None:
        try:
            cid = self._current_course_id()
            if cid is None:
                QMessageBox.information(self, "Import CSV", "Add a course first (Courses page).")
                return
            path, _flt = QFileDialog.getOpenFileName(
                self, "Import practice questions CSV", str(config.DATA_DIR),
                "CSV files (*.csv);;All files (*)",
            )
            if not path:
                return
            csv_text = Path(path).read_text(encoding="utf-8", errors="replace")
            new_questions = importers.parse_practice_csv(csv_text, cid)
            for q in new_questions:
                self.store.add_practice_question(q)
            self._reload_table()
            QMessageBox.information(self, "Import CSV", f"Imported {len(new_questions)} question(s).")
        except Exception:
            log.exception("Manage questions: CSV import failed")
            self._report("Could not import that CSV file.")

    def _on_import_pasted(self) -> None:
        try:
            cid = self._current_course_id()
            if cid is None:
                QMessageBox.information(self, "Import pasted", "Add a course first (Courses page).")
                return
            text = _prompt_pasted_text(self)
            if not text or not text.strip():
                return
            new_questions = importers.parse_practice_pasted(text, cid)
            for q in new_questions:
                self.store.add_practice_question(q)
            self._reload_table()
            QMessageBox.information(self, "Import pasted", f"Imported {len(new_questions)} question(s).")
        except Exception:
            log.exception("Manage questions: pasted-text import failed")
            self._report("Could not import the pasted text.")

    def _on_export_csv(self) -> None:
        try:
            rows = self.model.rows()
            if not rows:
                QMessageBox.information(self, "Export CSV", "No questions to export for this course.")
                return
            path, _flt = QFileDialog.getSaveFileName(
                self, "Export practice questions CSV",
                str(config.DATA_DIR / "practice_questions_export.csv"), "CSV files (*.csv)",
            )
            if not path:
                return
            csv_text = importers.export_practice_csv(rows)
            Path(path).write_text(csv_text, encoding="utf-8")
            QMessageBox.information(self, "Export CSV", f"Exported {len(rows)} question(s) to:\n{path}")
        except Exception:
            log.exception("Manage questions: CSV export failed")
            self._report("Could not export that CSV file.")
