"""
Mock Exam page (build spec section 5.7).

Three sub-views inside a single QStackedWidget, switched by internal state:
    0. Setup   - course/question-count/time-limit/source/shuffle controls,
                 plus a History table + score-over-time chart for the
                 selected course.
    1. Exam    - one full-page question at a time, a numbered nav strip
                 (answered/flagged/unanswered), a Flag toggle, and a
                 countdown that turns red under 2 minutes and auto-submits
                 at 0.
    2. Results - score, elapsed time, self-grading for Short Answer/Numeric
                 questions, a per-topic breakdown table, a missed-questions
                 list, "Set topics below 60% to Needs Focus", and "Save"
                 (writes a MockExams row).

Sampling for the exam pulls from ``store.list_practice_questions(course_id)``
using ``random.sample``/``random.choices`` per the chosen source - this is
normal application runtime behavior, not a build script, so real randomness
is fine here.
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from PySide6.QtCharts import (
    QChart, QChartView, QDateTimeAxis, QLineSeries, QValueAxis,
)
from PySide6.QtCore import QDate, QDateTime, QTime, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QRadioButton, QScrollArea, QSpinBox,
    QStackedWidget, QTableView, QVBoxLayout, QWidget,
)

from app import config, models
from app.excel_store import ExcelStore
from app.models import PracticeQuestion
from app.services import progress
from app.ui import table_models, widgets
from app.ui.table_models import ColumnSpec

log = logging.getLogger("study_tracker")

_SOURCE_ALL = "All"
_SOURCE_WEIGHTED = "Needs Focus & Learning weighted 2×"
_SOURCE_CHOSEN = "Chosen topics"
_SELF_GRADED_TYPES = ("Short Answer", "Numeric")
_CHOICE_TYPES = ("MCQ", "True/False")


# --------------------------------------------------------------------------
# Local per-topic breakdown row (Results view) - a plain dataclass rendered
# through the same generic DataclassTableModel every other table uses.
# --------------------------------------------------------------------------
@dataclass
class _TopicBreakdownRow:
    topic_id: int
    topic_label: str
    correct: int
    total: int
    pct: float


_BREAKDOWN_COLUMNS: list[ColumnSpec] = [
    ColumnSpec("topic_label", "Topic"),
    ColumnSpec("correct", "Correct", formatter=lambda v, row: f"{v}/{row.total}"),
    ColumnSpec(
        "pct", "% Correct", formatter=lambda v, row: f"{v:.0f}%",
        color_fn=lambda v, row: (
            config.URGENCY_OVERDUE if v < 60 else config.TOPIC_STATUS_COLORS["Mastered"]
        ),
    ),
]


class MockExamPage(QWidget):
    statusMessage = Signal(str)
    navigateTo = Signal(str)

    def __init__(self, store: ExcelStore, parent=None):
        super().__init__(parent)
        self.store = store

        # -- active exam state ------------------------------------------
        self._exam_course_id: Optional[int] = None
        self._exam_questions: list[PracticeQuestion] = []
        self._exam_options: dict[int, list[str]] = {}
        self._answers: dict[int, str] = {}
        self._flagged: set[int] = set()
        self._final_correct: dict[int, bool] = {}
        self._current_index: int = 0
        self._time_limit_min: int = 30
        self._remaining_seconds: int = 0
        self._elapsed_seconds: int = 0
        self._start_dt: Optional[datetime] = None
        self._submitted: bool = False
        self._exam_saved: bool = False
        self._answer_group: Optional[QButtonGroup] = None
        self._answer_line: Optional[QLineEdit] = None

        self._exam_timer = QTimer(self)
        self._exam_timer.setInterval(1000)
        self._exam_timer.timeout.connect(self._guard(self._on_exam_tick, "Timer error"))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_setup_view())    # index 0
        self._stack.addWidget(self._build_exam_view())      # index 1
        self._stack.addWidget(self._build_results_view())   # index 2
        outer.addWidget(self._stack, 1)

        self.store.dataChanged.connect(lambda _sheet: self.refresh())

        self.refresh()

    # ------------------------------------------------------------------
    # Error-safe wrapper for button-click / signal handlers.
    # ------------------------------------------------------------------
    def _guard(self, fn, message: str = "Action failed - see log"):
        def wrapped(*args, **kwargs):
            try:
                fn(*args, **kwargs)
            except Exception:
                log.exception(message)
                self.statusMessage.emit(message)
        return wrapped

    # ------------------------------------------------------------------
    # refresh() - safe to call any time, including mid-exam (it only
    # touches the Setup course combo / topic list and the History
    # table+chart; the active exam's local state is untouched).
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        try:
            self._refresh_setup_courses()
            self._refresh_history()
        except Exception:
            log.exception("MockExamPage failed to refresh")
            self.statusMessage.emit("Mock Exam page failed to refresh - see log")

    # ==================================================================
    # Setup view
    # ==================================================================
    def _build_setup_view(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        scroll.setWidget(content)

        setup_box = QGroupBox("New Mock Exam")
        form = QFormLayout(setup_box)

        self._course_combo = QComboBox()
        self._course_combo.currentIndexChanged.connect(self._guard(
            lambda _i: self._on_setup_course_changed(), "Could not update course selection"
        ))
        form.addRow("Course:", self._course_combo)

        self._num_spin = QSpinBox()
        self._num_spin.setRange(1, 200)
        self._num_spin.setValue(10)
        form.addRow("Number of questions:", self._num_spin)

        self._time_spin = QSpinBox()
        self._time_spin.setRange(1, 240)
        self._time_spin.setValue(30)
        self._time_spin.setSuffix(" min")
        form.addRow("Time limit:", self._time_spin)

        self._source_combo = QComboBox()
        self._source_combo.addItems([_SOURCE_ALL, _SOURCE_WEIGHTED, _SOURCE_CHOSEN])
        self._source_combo.currentTextChanged.connect(self._guard(
            lambda _t: self._on_source_changed(), "Could not update source"
        ))
        form.addRow("Source:", self._source_combo)

        self._topics_list = QListWidget()
        self._topics_list.setMaximumHeight(160)
        self._topics_list.setVisible(False)
        form.addRow("Topics:", self._topics_list)

        self._shuffle_check = QCheckBox("Shuffle question & answer order")
        self._shuffle_check.setChecked(True)
        form.addRow("", self._shuffle_check)

        self._start_btn = QPushButton("Start")
        self._start_btn.setObjectName("Primary")
        self._start_btn.clicked.connect(self._guard(self._on_start, "Could not start the mock exam"))
        form.addRow("", self._start_btn)

        content_layout.addWidget(setup_box)

        history_box = QGroupBox("History")
        history_layout = QVBoxLayout(history_box)
        self._history_model = table_models.DataclassTableModel(table_models.MOCKEXAM_COLUMNS)
        self._history_table = QTableView()
        self._history_table.setModel(self._history_model)
        table_models.apply_delegates(self._history_table, self._history_model)
        self._history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._history_table.setMaximumHeight(200)
        history_layout.addWidget(self._history_table)

        self._history_chart_view = QChartView()
        self._history_chart_view.setRenderHint(QPainter.Antialiasing)
        self._history_chart_view.setMinimumHeight(200)
        history_layout.addWidget(self._history_chart_view)

        content_layout.addWidget(history_box)
        content_layout.addStretch(1)
        return page

    def _refresh_setup_courses(self) -> None:
        current = self._course_combo.currentData()
        self._course_combo.blockSignals(True)
        self._course_combo.clear()
        courses = self.store.list_courses(active_only=True)
        for c in courses:
            self._course_combo.addItem(f"{c.code} — {c.name}", c.course_id)
        idx = self._course_combo.findData(current)
        if idx < 0 and courses:
            idx = 0
        if idx >= 0:
            self._course_combo.setCurrentIndex(idx)
        self._course_combo.blockSignals(False)
        self._refresh_topics_list(self._course_combo.currentData())

    def _on_setup_course_changed(self) -> None:
        course_id = self._course_combo.currentData()
        self._refresh_topics_list(course_id)
        self._refresh_history()

    def _on_source_changed(self) -> None:
        self._topics_list.setVisible(self._source_combo.currentText() == _SOURCE_CHOSEN)

    def _refresh_topics_list(self, course_id: Optional[int]) -> None:
        checked = self._selected_topic_ids() if course_id is not None else set()
        self._topics_list.clear()
        if course_id is None:
            return
        try:
            topics = self.store.list_topics(course_id)
        except Exception:
            log.exception("MockExamPage: failed to load topics for course_id=%s", course_id)
            topics = []
        for t in topics:
            label = f"{t.section} {t.title}".strip() or f"Topic {t.topic_id}"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if t.topic_id in checked else Qt.Unchecked)
            item.setData(Qt.UserRole, t.topic_id)
            self._topics_list.addItem(item)

    def _selected_topic_ids(self) -> set[int]:
        ids: set[int] = set()
        for i in range(self._topics_list.count()):
            item = self._topics_list.item(i)
            if item is not None and item.checkState() == Qt.Checked:
                tid = item.data(Qt.UserRole)
                if tid:
                    ids.add(tid)
        return ids

    def _refresh_history(self) -> None:
        course_id = self._course_combo.currentData()
        try:
            rows = self.store.list_mock_exams(course_id) if course_id is not None else []
        except Exception:
            log.exception("MockExamPage: failed to load mock exam history for course_id=%s", course_id)
            rows = []
        self._history_model.set_rows(rows)
        self._build_history_chart(rows)

    def _build_history_chart(self, rows: list[models.MockExam]) -> None:
        chart = QChart()
        chart.legend().hide()
        points = [
            (m.date_taken, m.score / m.max_score * 100)
            for m in rows
            if m.date_taken and m.score is not None and m.max_score
        ]
        points.sort(key=lambda p: p[0])
        if not points:
            chart.setTitle("Score history (no mock exams saved yet)")
            self._history_chart_view.setChart(chart)
            return
        chart.setTitle("Score % over time")

        series = QLineSeries()
        series.setName("Score %")
        for d, pct in points:
            qdt = QDateTime(QDate(d.year, d.month, d.day), QTime(0, 0))
            series.append(float(qdt.toMSecsSinceEpoch()), pct)
        chart.addSeries(series)

        axis_x = QDateTimeAxis()
        axis_x.setFormat("MMM d")
        axis_x.setTitleText("Date")
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setRange(0, 100)
        axis_y.setTitleText("Score %")
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        self._history_chart_view.setChart(chart)

    # ------------------------------------------------------------------
    # Sampling helpers (§5.7 "sampled ... using random.sample/random.choices")
    # ------------------------------------------------------------------
    @staticmethod
    def _weighted_sample(pool: list[PracticeQuestion], weights: list[float], k: int) -> list[PracticeQuestion]:
        items = list(pool)
        w = list(weights)
        picked: list[PracticeQuestion] = []
        for _ in range(min(k, len(items))):
            idx = random.choices(range(len(items)), weights=w, k=1)[0]
            picked.append(items.pop(idx))
            w.pop(idx)
        return picked

    def _on_start(self) -> None:
        course_id = self._course_combo.currentData()
        if course_id is None:
            self.statusMessage.emit("Add a course first.")
            return
        n = self._num_spin.value()
        time_limit = self._time_spin.value()
        source = self._source_combo.currentText()
        shuffle = self._shuffle_check.isChecked()

        try:
            all_q = self.store.list_practice_questions(course_id)
        except Exception:
            log.exception("MockExamPage: failed to load practice questions for course_id=%s", course_id)
            self.statusMessage.emit("Could not load practice questions for that course.")
            return
        if not all_q:
            self.statusMessage.emit("This course has no practice questions yet.")
            return

        if source == _SOURCE_CHOSEN:
            selected_ids = self._selected_topic_ids()
            if not selected_ids:
                self.statusMessage.emit("Select at least one topic first.")
                return
            pool = [q for q in all_q if q.topic_id in selected_ids]
            sampled = random.sample(pool, min(n, len(pool)))
        elif source == _SOURCE_WEIGHTED:
            try:
                topics_map = {t.topic_id: t for t in self.store.list_topics(course_id)}
            except Exception:
                log.exception("MockExamPage: failed to load topics for weighting, course_id=%s", course_id)
                topics_map = {}
            weights = [
                2.0 if (q.topic_id and topics_map.get(q.topic_id)
                        and topics_map[q.topic_id].status in ("Needs Focus", "Learning")) else 1.0
                for q in all_q
            ]
            sampled = self._weighted_sample(all_q, weights, n)
        else:
            sampled = random.sample(all_q, min(n, len(all_q)))

        if not sampled:
            self.statusMessage.emit("No practice questions match that selection.")
            return

        if shuffle:
            random.shuffle(sampled)
        else:
            sampled.sort(key=lambda q: q.question_id)

        self._begin_exam(course_id, sampled, time_limit, shuffle)

    def _begin_exam(self, course_id: int, questions: list[PracticeQuestion], time_limit_min: int, shuffle: bool) -> None:
        self._exam_course_id = course_id
        self._exam_questions = list(questions)
        self._exam_options = {}
        for q in self._exam_questions:
            if q.question_type == "MCQ":
                opts = list(q.options)
            elif q.question_type == "True/False":
                opts = list(q.options) if q.options else ["True", "False"]
            else:
                opts = []
            if shuffle and opts:
                random.shuffle(opts)
            self._exam_options[q.question_id] = opts

        self._answers = {}
        self._flagged = set()
        self._final_correct = {}
        self._current_index = 0
        self._time_limit_min = time_limit_min
        self._remaining_seconds = time_limit_min * 60
        self._elapsed_seconds = 0
        self._start_dt = datetime.now()
        self._submitted = False
        self._exam_saved = False

        self._update_countdown_label()
        self._exam_timer.start()
        self._render_question(0)
        self._stack.setCurrentIndex(1)
        self.statusMessage.emit(
            f"Started mock exam: {len(self._exam_questions)} question(s), {time_limit_min} min."
        )

    # ==================================================================
    # Exam view
    # ==================================================================
    def _build_exam_view(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        top_row = QHBoxLayout()
        self._exam_progress_label = QLabel("Question 1 / 1")
        self._exam_progress_label.setStyleSheet("font-weight: 700; font-size: 12pt;")
        top_row.addWidget(self._exam_progress_label)
        top_row.addStretch(1)
        self._countdown_label = QLabel("--:--")
        self._countdown_label.setStyleSheet("font-weight: 700; font-size: 12pt;")
        top_row.addWidget(self._countdown_label)
        outer.addLayout(top_row)

        self._nav_strip_container = QWidget()
        self._nav_strip_layout = QVBoxLayout(self._nav_strip_container)
        self._nav_strip_layout.setContentsMargins(0, 0, 0, 0)
        nav_scroll = QScrollArea()
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setFrameShape(QFrame.NoFrame)
        nav_scroll.setMaximumHeight(130)
        nav_scroll.setWidget(self._nav_strip_container)
        outer.addWidget(nav_scroll)

        self._question_card = QFrame()
        self._question_card.setObjectName("Card")
        self._question_layout = QVBoxLayout(self._question_card)
        outer.addWidget(self._question_card, 1)

        bottom_row = QHBoxLayout()
        self._flag_btn = QPushButton("\U0001F6A9 Flag for review")
        self._flag_btn.setCheckable(True)
        self._flag_btn.toggled.connect(self._guard(self._on_flag_toggled, "Could not toggle flag"))
        bottom_row.addWidget(self._flag_btn)
        bottom_row.addStretch(1)
        prev_btn = QPushButton("◀ Previous")
        prev_btn.clicked.connect(self._guard(self._on_prev, "Could not go to the previous question"))
        next_btn = QPushButton("Next ▶")
        next_btn.clicked.connect(self._guard(self._on_next, "Could not go to the next question"))
        submit_btn = QPushButton("Submit exam")
        submit_btn.setObjectName("Primary")
        submit_btn.clicked.connect(self._guard(self._on_submit_clicked, "Could not submit the exam"))
        bottom_row.addWidget(prev_btn)
        bottom_row.addWidget(next_btn)
        bottom_row.addWidget(submit_btn)
        outer.addLayout(bottom_row)
        return page

    def _render_question(self, idx: int) -> None:
        if not self._exam_questions:
            return
        self._save_current_answer()
        self._current_index = max(0, min(idx, len(self._exam_questions) - 1))
        q = self._exam_questions[self._current_index]
        self._exam_progress_label.setText(f"Question {self._current_index + 1} / {len(self._exam_questions)}")

        widgets.clear_layout(self._question_layout)

        type_lbl = QLabel(q.question_type)
        type_lbl.setStyleSheet("color: #94a3b8; font-weight: 600;")
        self._question_layout.addWidget(type_lbl)

        text_lbl = QLabel(q.question_text or "(no question text)")
        text_lbl.setWordWrap(True)
        text_lbl.setStyleSheet("font-size: 13pt;")
        self._question_layout.addWidget(text_lbl)

        self._answer_group = None
        self._answer_line = None
        saved = self._answers.get(q.question_id)

        if q.question_type in _CHOICE_TYPES:
            options = self._exam_options.get(q.question_id, [])
            if not options:
                self._question_layout.addWidget(QLabel("(no options configured for this question)"))
            else:
                group = QButtonGroup(self._question_card)
                group.setExclusive(True)
                for opt in options:
                    rb = QRadioButton(opt)
                    if saved is not None and str(saved) == str(opt):
                        rb.setChecked(True)
                    group.addButton(rb)
                    self._question_layout.addWidget(rb)
                self._answer_group = group
        else:
            line = QLineEdit()
            if saved is not None:
                line.setText(str(saved))
            line.setPlaceholderText(
                "Enter a numeric answer" if q.question_type == "Numeric" else "Type your answer"
            )
            self._question_layout.addWidget(line)
            self._answer_line = line

        self._question_layout.addStretch(1)

        self._flag_btn.blockSignals(True)
        self._flag_btn.setChecked(q.question_id in self._flagged)
        self._flag_btn.blockSignals(False)
        self._update_flag_btn_style()

        self._rebuild_nav_strip()

    def _save_current_answer(self) -> None:
        if not self._exam_questions:
            return
        q = self._exam_questions[self._current_index]
        if self._answer_group is not None:
            btn = self._answer_group.checkedButton()
            if btn is not None:
                self._answers[q.question_id] = btn.text()
        elif self._answer_line is not None:
            text = self._answer_line.text().strip()
            if text:
                self._answers[q.question_id] = text
            else:
                self._answers.pop(q.question_id, None)

    def _rebuild_nav_strip(self) -> None:
        widgets.clear_layout(self._nav_strip_layout)
        items = []
        for i, q in enumerate(self._exam_questions):
            if i == self._current_index:
                color = "#2563eb"
            elif q.question_id in self._flagged:
                color = config.URGENCY_SOON
            elif q.question_id in self._answers:
                color = config.TOPIC_STATUS_COLORS["Mastered"]
            else:
                color = config.TOPIC_STATUS_COLORS["Not Started"]
            items.append((str(i + 1), color, self._guard(
                lambda i=i: self._render_question(i), "Could not jump to that question"
            )))
        grid = widgets.make_button_grid(items, columns=12)
        self._nav_strip_layout.addWidget(grid)

    def _on_prev(self) -> None:
        self._render_question(self._current_index - 1)

    def _on_next(self) -> None:
        self._render_question(self._current_index + 1)

    def _on_flag_toggled(self, checked: bool) -> None:
        if not self._exam_questions:
            return
        q = self._exam_questions[self._current_index]
        if checked:
            self._flagged.add(q.question_id)
        else:
            self._flagged.discard(q.question_id)
        self._update_flag_btn_style()
        self._rebuild_nav_strip()

    def _update_flag_btn_style(self) -> None:
        if self._flag_btn.isChecked():
            self._flag_btn.setStyleSheet(f"background-color: {config.URGENCY_SOON}; color: white; font-weight: 700;")
        else:
            self._flag_btn.setStyleSheet("")

    # -- countdown ------------------------------------------------------
    def _on_exam_tick(self) -> None:
        if not self._exam_questions or self._submitted:
            self._exam_timer.stop()
            return
        self._remaining_seconds -= 1
        self._update_countdown_label()
        if self._remaining_seconds <= 0:
            self._exam_timer.stop()
            self._do_submit(auto=True)

    def _update_countdown_label(self) -> None:
        secs = max(0, self._remaining_seconds)
        mm, ss = divmod(secs, 60)
        self._countdown_label.setText(f"{mm:02d}:{ss:02d}")
        if secs < 120:
            self._countdown_label.setStyleSheet("font-weight: 700; font-size: 12pt; color: #ef4444;")
        else:
            self._countdown_label.setStyleSheet("font-weight: 700; font-size: 12pt;")

    # -- submit / grading -------------------------------------------------
    def _on_submit_clicked(self) -> None:
        if not self._exam_questions:
            return
        self._save_current_answer()
        unanswered = sum(1 for q in self._exam_questions if q.question_id not in self._answers)
        prompt = (
            f"{unanswered} question(s) unanswered. Submit anyway?" if unanswered
            else "Submit the exam now?"
        )
        if not widgets.confirm(self, prompt):
            return
        self._exam_timer.stop()
        self._do_submit(auto=False)

    def _do_submit(self, auto: bool) -> None:
        self._save_current_answer()
        self._submitted = True
        self._elapsed_seconds = (
            int((datetime.now() - self._start_dt).total_seconds()) if self._start_dt
            else self._time_limit_min * 60
        )
        self._grade_exam()
        self._render_results()
        self._stack.setCurrentIndex(2)
        self.statusMessage.emit("Time's up — exam auto-submitted." if auto else "Exam submitted.")

    def _grade_exam(self) -> None:
        self._final_correct = {}
        for q in self._exam_questions:
            selected = self._answers.get(q.question_id)
            if q.question_type in _CHOICE_TYPES:
                correct = selected is not None and str(selected).strip().lower() == str(q.answer_text or "").strip().lower()
                self._final_correct[q.question_id] = correct
            elif q.question_type == "Numeric":
                auto = False
                if selected not in (None, ""):
                    try:
                        auto = abs(float(selected) - float(q.answer_text)) < 1e-6
                    except (TypeError, ValueError):
                        auto = False
                self._final_correct[q.question_id] = bool(auto)
            else:  # Short Answer - no auto grade; student sets it in Results
                self._final_correct[q.question_id] = False

    # ==================================================================
    # Results view
    # ==================================================================
    def _build_results_view(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll, 1)

        content = QWidget()
        self._results_layout = QVBoxLayout(content)
        scroll.setWidget(content)
        return page

    def _render_results(self) -> None:
        widgets.clear_layout(self._results_layout)
        if not self._exam_questions:
            self._results_layout.addWidget(QLabel("No exam results yet."))
            return

        total = len(self._exam_questions)
        correct_n = sum(1 for v in self._final_correct.values() if v)
        pct = correct_n / total * 100 if total else 0.0

        summary_box = QGroupBox("Result")
        summary_layout = QHBoxLayout(summary_box)
        score_lbl = QLabel(f"Score: {correct_n}/{total} ({pct:.0f}%)")
        score_lbl.setStyleSheet("font-size: 16pt; font-weight: 700;")
        summary_layout.addWidget(score_lbl)
        mm, ss = divmod(max(0, self._elapsed_seconds), 60)
        summary_layout.addWidget(QLabel(f"Elapsed: {mm:02d}:{ss:02d}"))
        summary_layout.addStretch(1)
        self._results_layout.addWidget(summary_box)

        # -- self-grading for Short Answer / Numeric ---------------------
        self_graded_qs = [q for q in self._exam_questions if q.question_type in _SELF_GRADED_TYPES]
        if self_graded_qs:
            sg_box = QGroupBox("Self-grade — Short Answer / Numeric")
            sg_layout = QVBoxLayout(sg_box)
            for q in self_graded_qs:
                row = QFrame()
                row.setFrameShape(QFrame.StyledPanel)
                row_l = QVBoxLayout(row)
                qt_lbl = QLabel(q.question_text or "(no question text)")
                qt_lbl.setWordWrap(True)
                qt_lbl.setStyleSheet("font-weight: 600;")
                row_l.addWidget(qt_lbl)
                row_l.addWidget(QLabel(f"Your answer: {self._answers.get(q.question_id, '(no answer)')}"))
                row_l.addWidget(QLabel(f"Model answer: {q.answer_text or '—'}"))
                if q.explanation:
                    exp_lbl = QLabel(f"Explanation: {q.explanation}")
                    exp_lbl.setWordWrap(True)
                    exp_lbl.setStyleSheet("color: #94a3b8;")
                    row_l.addWidget(exp_lbl)
                checkbox = QCheckBox("Correct")
                checkbox.setChecked(bool(self._final_correct.get(q.question_id, False)))
                checkbox.toggled.connect(self._guard(
                    lambda checked, qid=q.question_id: self._on_self_grade_toggled(qid, checked),
                    "Could not update self-grade",
                ))
                row_l.addWidget(checkbox)
                sg_layout.addWidget(row)
            self._results_layout.addWidget(sg_box)

        # -- per-topic breakdown ------------------------------------------
        breakdown = self._compute_breakdown()
        bd_box = QGroupBox("Per-topic breakdown")
        bd_layout = QVBoxLayout(bd_box)
        bd_model = table_models.DataclassTableModel(_BREAKDOWN_COLUMNS, breakdown)
        bd_table = QTableView()
        bd_table.setModel(bd_model)
        bd_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        bd_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        bd_table.setMaximumHeight(200)
        bd_layout.addWidget(bd_table)
        self._results_layout.addWidget(bd_box)

        # -- missed questions -----------------------------------------------
        missed = [q for q in self._exam_questions if not self._final_correct.get(q.question_id, False)]
        missed_box = QGroupBox(f"Missed questions ({len(missed)})")
        missed_layout = QVBoxLayout(missed_box)
        if not missed:
            missed_layout.addWidget(QLabel("None — great job!"))
        else:
            missed_list = QListWidget()
            missed_list.setMaximumHeight(160)
            for q in missed:
                item = QListWidgetItem(f"{q.question_text}  —  correct answer: {q.answer_text or '—'}")
                if q.explanation:
                    item.setToolTip(q.explanation)
                missed_list.addItem(item)
            missed_layout.addWidget(missed_list)
        self._results_layout.addWidget(missed_box)

        # -- actions ----------------------------------------------------
        actions = QHBoxLayout()
        focus_btn = QPushButton("Set topics below 60% to Needs Focus")
        focus_btn.clicked.connect(self._guard(self._on_set_needs_focus, "Could not update topic statuses"))
        actions.addWidget(focus_btn)
        save_btn = QPushButton("Saved ✓" if self._exam_saved else "Save")
        save_btn.setObjectName("Primary")
        save_btn.setEnabled(not self._exam_saved)
        save_btn.clicked.connect(self._guard(self._on_save_exam, "Could not save the mock exam"))
        actions.addWidget(save_btn)
        new_btn = QPushButton("New exam")
        new_btn.clicked.connect(self._guard(self._on_new_exam, "Could not return to setup"))
        actions.addWidget(new_btn)
        actions.addStretch(1)
        self._results_layout.addLayout(actions)
        self._results_layout.addStretch(1)

    def _on_self_grade_toggled(self, qid: int, checked: bool) -> None:
        self._final_correct[qid] = checked
        self._render_results()

    def _compute_breakdown(self) -> list[_TopicBreakdownRow]:
        topics_map: dict[int, models.Topic] = {}
        if self._exam_course_id is not None:
            try:
                topics_map = {t.topic_id: t for t in self.store.list_topics(self._exam_course_id)}
            except Exception:
                log.exception("MockExamPage: failed to load topics for breakdown, course_id=%s", self._exam_course_id)

        groups: dict[Optional[int], list[PracticeQuestion]] = {}
        for q in self._exam_questions:
            groups.setdefault(q.topic_id, []).append(q)

        rows: list[_TopicBreakdownRow] = []
        for tid, qs in groups.items():
            topic = topics_map.get(tid) if tid else None
            label = f"{topic.section} {topic.title}".strip() if topic else "No topic"
            correct = sum(1 for q in qs if self._final_correct.get(q.question_id, False))
            total = len(qs)
            pct = correct / total * 100 if total else 0.0
            rows.append(_TopicBreakdownRow(topic_id=tid or 0, topic_label=label, correct=correct, total=total, pct=pct))
        rows.sort(key=lambda r: r.topic_label)
        return rows

    def _on_set_needs_focus(self) -> None:
        candidates = [r for r in self._compute_breakdown() if r.topic_id and r.pct < 60]
        if not candidates:
            self.statusMessage.emit("No topics scored below 60%.")
            return
        if not widgets.confirm(self, f"Set {len(candidates)} topic(s) below 60% to 'Needs Focus'?"):
            return
        today = date.today()
        n = 0
        for r in candidates:
            topic = self.store.get_topic(r.topic_id)
            if topic is None:
                continue
            progress.topic_status_changed(topic, "Needs Focus", today)
            self.store.update_topic(topic)
            n += 1
        self.statusMessage.emit(f"Set {n} topic(s) to Needs Focus.")

    def _on_save_exam(self) -> None:
        if self._exam_saved:
            self.statusMessage.emit("This exam is already saved.")
            return
        total = len(self._exam_questions)
        score = float(sum(1 for v in self._final_correct.values() if v))
        course = self.store.get_course(self._exam_course_id) if self._exam_course_id else None
        code = course.code if course else "?"

        results = {
            "questions": [
                {
                    "question_id": q.question_id,
                    "question_type": q.question_type,
                    "selected": self._answers.get(q.question_id),
                    "correct": bool(self._final_correct.get(q.question_id, False)),
                    "flagged": q.question_id in self._flagged,
                }
                for q in self._exam_questions
            ],
        }

        m = models.MockExam(
            course_id=self._exam_course_id or 0,
            name=f"{code} Mock Exam — {date.today().isoformat()}",
            date_taken=date.today(),
            time_limit_min=self._time_limit_min,
            question_ids=[q.question_id for q in self._exam_questions],
            score=score,
            max_score=float(total),
            results_json=json.dumps(results),
            duration_min=round(self._elapsed_seconds / 60.0, 1),
        )
        self.store.add_mock_exam(m)
        self._exam_saved = True
        self._render_results()
        self._refresh_history()
        self.statusMessage.emit(f"Saved mock exam ({score:g}/{total:g}).")

    def _on_new_exam(self) -> None:
        self._exam_timer.stop()
        self._exam_questions = []
        self._exam_options = {}
        self._answers = {}
        self._flagged = set()
        self._final_correct = {}
        self._submitted = False
        self._answer_group = None
        self._answer_line = None
        self._stack.setCurrentIndex(0)
        self.refresh()
