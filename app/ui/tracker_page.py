"""
Tracker page (build spec §5.4): a QTabWidget with four cross-course tabs -
Exams, Deadlines, Pre-labs, and a Mon-Fri Week view - built on top of
``table_models.DataclassTableModel``/``ColumnSpec`` so every row shown here
is a live view over the same rows a course's own Course Detail page shows.
Nothing is hardcoded per course/assessment/topic; a new workbook row shows
up on the next ``refresh()`` with no code change (build spec §1 "Dynamic
buttons").
"""
from __future__ import annotations

import logging
from datetime import date
from datetime import time as dtime
from datetime import timedelta
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QPushButton, QScrollArea, QTableView, QTabWidget,
    QVBoxLayout, QWidget,
)

from app import config, models
from app.excel_store import ExcelStore
from app.services import scheduler
from app.ui import dialogs, table_models, widgets
from app.ui.table_models import ColumnSpec
from app.ui.widgets import urgency_color

log = logging.getLogger("study_tracker")

_EXAM_TYPES = ("Midterm", "Final", "Quiz", "Practical Lab Assessment")
_START_HOUR = 8
_END_HOUR = 17
_NUM_HOUR_ROWS = _END_HOUR - _START_HOUR  # 9 hourly slots: 08:00-09:00 .. 16:00-17:00

# Extra columns (beyond ASSESSMENT_COLUMNS) needed on the Add/Edit dialog for
# an exam - the display table shows Week/Countdown/Coverage instead of these,
# but the row itself still needs topic_ids/estimated_hours settable somewhere.
_EXAM_DIALOG_EXTRA_COLUMNS = [
    ColumnSpec("topic_ids", "Topic IDs (comma-separated)", editable=True),
    ColumnSpec("estimated_hours", "Estimated hours", kind="float", editable=True),
]


def _countdown_text(d: Optional[date]) -> str:
    if d is None:
        return "date TBC"
    days = (d - date.today()).days
    if days < 0:
        n = abs(days)
        return f"overdue by {n} day" + ("s" if n != 1 else "")
    if days == 0:
        return "today"
    return f"in {days} day" + ("s" if days != 1 else "")


class TrackerPage(QWidget):
    """Cross-course Exams / Deadlines / Pre-labs / Week-view tabs."""

    statusMessage = Signal(str)
    navigateTo = Signal(str)

    def __init__(self, store: ExcelStore, parent=None):
        super().__init__(parent)
        self.store = store

        self._week1_monday: date = date.fromisoformat(config.SETTINGS_DEFAULTS["week1_monday"])
        self._courses: list[models.Course] = []
        self._course_map: dict[int, str] = {}
        self._course_obj_map: dict[int, models.Course] = {}
        self._topics_by_id: dict[int, models.Topic] = {}

        self._exams_show_all = False
        self._prelab_show_none = False
        self._prelab_course_filter = 0  # 0 = all courses
        self._week_monday: Optional[date] = None  # Monday of the displayed term week

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)

        self._build_exams_tab()
        self._build_deadlines_tab()
        self._build_prelabs_tab()
        self._build_week_tab()

        self.refresh()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _guard(self, fn, message: str = "Action failed — see log"):
        """Wrap a slot so it never raises: log + statusMessage instead."""
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                log.exception(message)
                self.statusMessage.emit(message)
        return wrapped

    def _row_from_view(self, view: QTableView, model: table_models.DataclassTableModel):
        sel = view.selectionModel()
        row = -1
        if sel is not None:
            rows = sel.selectedRows()
            if rows:
                row = rows[0].row()
        if row < 0:
            idx = view.currentIndex()
            if idx.isValid():
                row = idx.row()
        if 0 <= row < len(model.rows()):
            return model.row_object(row)
        return None

    # ------------------------------------------------------------------
    # refresh() - re-pulls everything from the store and rebuilds all tabs
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        try:
            self._week1_monday = self.store.setting_date("week1_monday") or self._week1_monday
            self._courses = self.store.list_courses()
            self._course_map = {c.course_id: c.code for c in self._courses}
            self._course_obj_map = {c.course_id: c for c in self._courses}
            self._topics_by_id = {t.topic_id: t for t in self.store.list_topics()}
        except Exception:
            log.exception("TrackerPage: failed to load base data")
            self.statusMessage.emit("Tracker page failed to refresh — see log.")

        for fn, label in (
            (self._refresh_exams, "exams"),
            (self._refresh_deadlines, "deadlines"),
            (self._refresh_prelabs, "pre-labs"),
            (self._refresh_week, "week view"),
        ):
            try:
                fn()
            except Exception:
                log.exception("TrackerPage: failed to refresh %s tab", label)
                self.statusMessage.emit(f"Could not refresh the {label} tab.")

    # ==================================================================
    # Tab 1 - Exams
    # ==================================================================
    def _build_exams_tab(self) -> None:
        tab = QWidget()
        v = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        self.show_all_types_cb = QCheckBox("Show all types")
        self.show_all_types_cb.toggled.connect(self._guard(self._on_toggle_show_all_types))
        toolbar.addWidget(self.show_all_types_cb)
        toolbar.addStretch(1)
        add_btn = QPushButton("+ Add")
        add_btn.clicked.connect(self._guard(self._on_add_exam))
        toolbar.addWidget(add_btn)
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._guard(self._on_edit_exam))
        toolbar.addWidget(edit_btn)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._guard(self._on_delete_exam))
        toolbar.addWidget(delete_btn)
        roadmap_btn = QPushButton("Generate roadmap for this exam")
        roadmap_btn.clicked.connect(self._guard(self._on_generate_roadmap_for_exam))
        toolbar.addWidget(roadmap_btn)
        v.addLayout(toolbar)

        self.exams_model = table_models.DataclassTableModel([], [])
        self.exams_model.on_edit = self._on_exam_cell_edited
        self.exams_view = QTableView()
        self.exams_view.setModel(self.exams_model)
        self.exams_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.exams_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.exams_view.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.exams_view, 1)

        self.tabs.addTab(tab, "Exams")

    def _exam_columns(self) -> list[ColumnSpec]:
        week1 = self._week1_monday
        topics_by_id = self._topics_by_id

        def week_fmt(v, row, week1=week1):
            return f"Week {scheduler.term_week_number(v, week1)}" if v else "TBC"

        def countdown_fmt(v, row):
            return _countdown_text(v)

        def coverage_fmt(v, row, topics_by_id=topics_by_id):
            ids = row.topic_ids or []
            if not ids:
                return "—"
            mastered = sum(
                1 for tid in ids
                if topics_by_id.get(tid) and topics_by_id[tid].status == "Mastered"
            )
            return f"{mastered}/{len(ids)} ({mastered / len(ids) * 100:.0f}%)"

        return (
            [table_models.course_column(self._course_map)]
            + list(table_models.ASSESSMENT_COLUMNS)
            + [
                ColumnSpec("due_date", "Week", formatter=week_fmt),
                ColumnSpec("due_date", "Countdown", formatter=countdown_fmt,
                           color_fn=lambda v, row: urgency_color(v, row.due_time)),
                ColumnSpec("topic_ids", "Coverage", formatter=coverage_fmt),
            ]
        )

    def _refresh_exams(self) -> None:
        try:
            all_assessments = self.store.list_assessments()
        except Exception:
            log.exception("TrackerPage: failed to load assessments (exams tab)")
            self.statusMessage.emit("Could not load exams.")
            all_assessments = []

        if self._exams_show_all:
            rows = list(all_assessments)
        else:
            rows = [a for a in all_assessments if a.type in _EXAM_TYPES]
        rows.sort(key=lambda a: (a.due_date or date.max, a.due_time or dtime(23, 59)))

        self.exams_model.columns = self._exam_columns()
        self.exams_model.set_rows(rows)
        table_models.apply_delegates(self.exams_view, self.exams_model)

    def _on_toggle_show_all_types(self, checked: bool) -> None:
        self._exams_show_all = bool(checked)
        self._refresh_exams()

    def _on_exam_cell_edited(self, obj, attr, value):
        try:
            self.store.update_assessment(obj)
            return True
        except Exception:
            log.exception("TrackerPage: failed to save exam edit (attr=%s)", attr)
            self.statusMessage.emit("Could not save that change.")
            return False

    def _on_add_exam(self) -> None:
        if not self._courses:
            self.statusMessage.emit("Add a course first (Courses page).")
            return
        codes = [c.code for c in self._courses]
        code, ok = QInputDialog.getItem(self, "Select course", "Course:", codes, 0, False)
        if not ok or not code:
            return
        course = next((c for c in self._courses if c.code == code), None)
        if course is None:
            return
        new_a = models.Assessment(course_id=course.course_id, type="Quiz", status="Not Started")
        columns = list(table_models.ASSESSMENT_COLUMNS) + _EXAM_DIALOG_EXTRA_COLUMNS
        result = dialogs.edit_row(
            self, f"Add assessment — {course.code}", columns, new_a,
            exclude_attrs={"assessment_id"}, multiline_attrs={"notes"},
        )
        if result is None:
            return
        self.store.add_assessment(result)
        self.refresh()

    def _on_edit_exam(self) -> None:
        a = self._row_from_view(self.exams_view, self.exams_model)
        if a is None:
            self.statusMessage.emit("Select an assessment first.")
            return
        course = self._course_obj_map.get(a.course_id)
        columns = list(table_models.ASSESSMENT_COLUMNS) + _EXAM_DIALOG_EXTRA_COLUMNS
        result = dialogs.edit_row(
            self, f"Edit assessment — {course.code if course else '?'}", columns, a,
            exclude_attrs={"assessment_id"}, multiline_attrs={"notes"},
        )
        if result is None:
            return
        self.store.update_assessment(result)
        self.refresh()

    def _on_delete_exam(self) -> None:
        a = self._row_from_view(self.exams_view, self.exams_model)
        if a is None:
            self.statusMessage.emit("Select an assessment first.")
            return
        if not widgets.confirm(self, f"Delete '{a.title}'? This cannot be undone."):
            return
        self.store.delete_assessment(a.assessment_id)
        self.refresh()

    def _on_generate_roadmap_for_exam(self) -> None:
        a = self._row_from_view(self.exams_view, self.exams_model)
        if a is None:
            self.statusMessage.emit("Select an exam first.")
            return
        if not a.due_date:
            self.statusMessage.emit("This exam has no due date yet — set one before generating a roadmap.")
            return
        self.store.delete_auto_roadmap(a.course_id)
        topics = self.store.list_topics(a.course_id)
        items = scheduler.generate_roadmap(
            a.course_id, topics, self._week1_monday, date.today(), a.due_date,
            target_topic_ids=set(a.topic_ids),
        )
        for item in items:
            self.store.add_roadmap_item(item)
        self.refresh()
        self.statusMessage.emit(f"Roadmap generated for {a.title} ({len(items)} milestone(s)).")

    # ==================================================================
    # Tab 2 - Deadlines
    # ==================================================================
    def _build_deadlines_tab(self) -> None:
        tab = QWidget()
        v = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        mark_btn = QPushButton("Mark submitted")
        mark_btn.clicked.connect(self._guard(self._on_mark_submitted))
        toolbar.addWidget(mark_btn)
        grade_btn = QPushButton("Enter grade")
        grade_btn.clicked.connect(self._guard(self._on_enter_grade))
        toolbar.addWidget(grade_btn)
        toolbar.addStretch(1)
        v.addLayout(toolbar)

        self.deadlines_model = table_models.DataclassTableModel([], [])
        self.deadlines_model.on_edit = self._on_deadline_cell_edited
        self.deadlines_view = QTableView()
        self.deadlines_view.setModel(self.deadlines_model)
        self.deadlines_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.deadlines_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.deadlines_view.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.deadlines_view, 1)

        self.tabs.addTab(tab, "Deadlines")

    def _free_hours(self, due_date: Optional[date]) -> float:
        """Free study hours between today and due_date: day-count (including
        weekends, per spec) x default_study_block_min/60."""
        if due_date is None:
            return 0.0
        days = max(0, (due_date - date.today()).days)
        block_min = self.store.setting_int("default_study_block_min", 50)
        return days * (block_min / 60.0)

    def _deadline_columns(self) -> list[ColumnSpec]:
        def hours_fmt(v, row):
            return "" if not v else f"{v:g}"

        def warn_fmt(v, row):
            free = self._free_hours(row.due_date)
            return "⚠" if (row.estimated_hours and row.estimated_hours > free) else ""

        return (
            [table_models.course_column(self._course_map)]
            + list(table_models.ASSESSMENT_COLUMNS)
            + [
                ColumnSpec("estimated_hours", "Est. hrs", kind="float", editable=True, width=60, formatter=hours_fmt),
                ColumnSpec("estimated_hours", "Hrs ⚠", width=40, formatter=warn_fmt),
            ]
        )

    def _refresh_deadlines(self) -> None:
        try:
            all_assessments = self.store.list_assessments()
        except Exception:
            log.exception("TrackerPage: failed to load deadlines")
            self.statusMessage.emit("Could not load deadlines.")
            all_assessments = []

        rows = [a for a in all_assessments if a.status not in ("Submitted", "Graded")]
        rows.sort(key=lambda a: (a.due_date or date.max, a.due_time or dtime(23, 59)))

        self.deadlines_model.columns = self._deadline_columns()
        self.deadlines_model.set_rows(rows)
        table_models.apply_delegates(self.deadlines_view, self.deadlines_model)

    def _on_deadline_cell_edited(self, obj, attr, value):
        try:
            self.store.update_assessment(obj)
            return True
        except Exception:
            log.exception("TrackerPage: failed to save deadline edit (attr=%s)", attr)
            self.statusMessage.emit("Could not save that change.")
            return False

    def _on_mark_submitted(self) -> None:
        a = self._row_from_view(self.deadlines_view, self.deadlines_model)
        if a is None:
            self.statusMessage.emit("Select a deadline first.")
            return
        a.status = "Submitted"
        self.store.update_assessment(a)
        self.refresh()

    def _on_enter_grade(self) -> None:
        a = self._row_from_view(self.deadlines_view, self.deadlines_model)
        if a is None:
            self.statusMessage.emit("Select a deadline first.")
            return
        max_score = a.max_score if a.max_score else 100.0
        score, ok = QInputDialog.getDouble(
            self, "Enter grade", f"Score for {a.title} (out of {max_score:g}):",
            a.score if a.score is not None else 0.0, 0.0, 1_000_000.0, 2,
        )
        if not ok:
            return
        a.score = score
        a.status = "Graded"
        self.store.update_assessment(a)
        self.refresh()

    # ==================================================================
    # Tab 3 - Pre-labs
    # ==================================================================
    def _build_prelabs_tab(self) -> None:
        tab = QWidget()
        v = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        self.prelab_show_none_cb = QCheckBox("Show holiday / no-lab rows")
        self.prelab_show_none_cb.toggled.connect(self._guard(self._on_toggle_prelab_show_none))
        toolbar.addWidget(self.prelab_show_none_cb)
        toolbar.addWidget(QLabel("Course:"))
        self.prelab_course_combo = QComboBox()
        self.prelab_course_combo.addItem("All courses", 0)
        self.prelab_course_combo.currentIndexChanged.connect(self._guard(self._on_prelab_course_filter_changed))
        toolbar.addWidget(self.prelab_course_combo)
        toolbar.addStretch(1)
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._guard(self._on_edit_prelab))
        toolbar.addWidget(edit_btn)
        done_btn = QPushButton("Mark done")
        done_btn.clicked.connect(self._guard(self._on_mark_prelab_done))
        toolbar.addWidget(done_btn)
        v.addLayout(toolbar)

        pinned_lbl = QLabel("Today / Tomorrow")
        pinned_lbl.setStyleSheet("font-weight: 700; margin-top: 4px;")
        v.addWidget(pinned_lbl)

        self.prelab_pinned_model = table_models.DataclassTableModel([], [])
        self.prelab_pinned_model.on_edit = self._on_prelab_cell_edited
        self.prelab_pinned_view = QTableView()
        self.prelab_pinned_view.setModel(self.prelab_pinned_model)
        self.prelab_pinned_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.prelab_pinned_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.prelab_pinned_view.horizontalHeader().setStretchLastSection(True)
        self.prelab_pinned_view.setFixedHeight(110)
        v.addWidget(self.prelab_pinned_view)

        all_lbl = QLabel("All pre-labs")
        all_lbl.setStyleSheet("font-weight: 700; margin-top: 8px;")
        v.addWidget(all_lbl)

        self.prelab_model = table_models.DataclassTableModel([], [])
        self.prelab_model.on_edit = self._on_prelab_cell_edited
        self.prelab_view = QTableView()
        self.prelab_view.setModel(self.prelab_model)
        self.prelab_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.prelab_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.prelab_view.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.prelab_view, 1)

        self.tabs.addTab(tab, "Pre-labs")

    def _prelab_columns(self) -> list[ColumnSpec]:
        return [table_models.course_column(self._course_map)] + list(table_models.PRELAB_COLUMNS)

    def _refresh_prelabs(self) -> None:
        try:
            all_prelabs = self.store.list_prelabs()
        except Exception:
            log.exception("TrackerPage: failed to load pre-labs")
            self.statusMessage.emit("Could not load pre-labs.")
            all_prelabs = []

        # Repopulate the course filter combo, preserving the current filter.
        self.prelab_course_combo.blockSignals(True)
        self.prelab_course_combo.clear()
        self.prelab_course_combo.addItem("All courses", 0)
        for c in self._courses:
            self.prelab_course_combo.addItem(c.code, c.course_id)
        match_idx = self.prelab_course_combo.findData(self._prelab_course_filter)
        self.prelab_course_combo.setCurrentIndex(match_idx if match_idx >= 0 else 0)
        self.prelab_course_combo.blockSignals(False)

        columns = self._prelab_columns()

        today = date.today()
        tomorrow = today + timedelta(days=1)
        pinned = sorted(
            (p for p in all_prelabs if p.lab_type != "None" and p.lab_date in (today, tomorrow)),
            key=lambda p: (p.lab_date, p.lab_time or dtime(23, 59)),
        )
        self.prelab_pinned_model.columns = columns
        self.prelab_pinned_model.set_rows(pinned)
        table_models.apply_delegates(self.prelab_pinned_view, self.prelab_pinned_model)

        rows = [
            p for p in all_prelabs
            if (self._prelab_show_none or p.lab_type != "None")
            and (not self._prelab_course_filter or p.course_id == self._prelab_course_filter)
        ]
        rows.sort(key=lambda p: (p.lab_date or date.max, p.lab_time or dtime(23, 59)))
        self.prelab_model.columns = columns
        self.prelab_model.set_rows(rows)
        table_models.apply_delegates(self.prelab_view, self.prelab_model)

    def _on_prelab_cell_edited(self, obj, attr, value):
        try:
            if attr == "completed":
                obj.completed_on = date.today() if value else None
            self.store.update_prelab(obj)
            return True
        except Exception:
            log.exception("TrackerPage: failed to save pre-lab edit (attr=%s)", attr)
            self.statusMessage.emit("Could not save that change.")
            return False

    def _on_toggle_prelab_show_none(self, checked: bool) -> None:
        self._prelab_show_none = bool(checked)
        self._refresh_prelabs()

    def _on_prelab_course_filter_changed(self, index: int) -> None:
        self._prelab_course_filter = self.prelab_course_combo.currentData() or 0
        self._refresh_prelabs()

    def _selected_prelab(self):
        obj = self._row_from_view(self.prelab_view, self.prelab_model)
        if obj is None:
            obj = self._row_from_view(self.prelab_pinned_view, self.prelab_pinned_model)
        return obj

    def _on_mark_prelab_done(self) -> None:
        p = self._selected_prelab()
        if p is None:
            self.statusMessage.emit("Select a pre-lab first.")
            return
        p.completed = True
        p.completed_on = date.today()
        self.store.update_prelab(p)
        self.refresh()

    def _on_edit_prelab(self) -> None:
        p = self._selected_prelab()
        if p is None:
            self.statusMessage.emit("Select a pre-lab first.")
            return
        course = self._course_obj_map.get(p.course_id)
        title = f"Edit pre-lab — {course.code if course else '?'} Lab {p.lab_number}"
        result = dialogs.edit_row(
            self, title, table_models.PRELAB_COLUMNS, p,
            exclude_attrs={"prelab_id", "course_id"}, multiline_attrs={"notes"},
        )
        if result is None:
            return
        self.store.update_prelab(result)
        self.refresh()

    # ==================================================================
    # Tab 4 - Week view
    # ==================================================================
    def _build_week_tab(self) -> None:
        tab = QWidget()
        v = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        prev_btn = QPushButton("◀ Previous week")
        prev_btn.clicked.connect(self._guard(self._on_prev_week))
        toolbar.addWidget(prev_btn)
        self.week_label = QLabel("")
        self.week_label.setAlignment(Qt.AlignCenter)
        self.week_label.setStyleSheet("font-weight: 700; font-size: 12pt;")
        toolbar.addWidget(self.week_label, 1)
        next_btn = QPushButton("Next week ▶")
        next_btn.clicked.connect(self._guard(self._on_next_week))
        toolbar.addWidget(next_btn)
        v.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.week_grid_host = QWidget()
        self.week_grid = QGridLayout(self.week_grid_host)
        self.week_grid.setSpacing(2)
        scroll.setWidget(self.week_grid_host)
        v.addWidget(scroll, 1)

        self.tabs.addTab(tab, "Week view")

    def _default_week_monday(self) -> date:
        return scheduler.week_start(
            scheduler.term_week_number(date.today(), self._week1_monday), self._week1_monday
        )

    def _on_prev_week(self) -> None:
        self._week_monday = (self._week_monday or self._default_week_monday()) - timedelta(days=7)
        self._refresh_week()

    def _on_next_week(self) -> None:
        self._week_monday = (self._week_monday or self._default_week_monday()) + timedelta(days=7)
        self._refresh_week()

    def _start_row(self, t: Optional[dtime]) -> int:
        if t is None:
            return 0
        minutes = t.hour * 60 + t.minute
        row = (minutes - _START_HOUR * 60) // 60
        return max(0, min(_NUM_HOUR_ROWS - 1, row))

    def _end_row(self, t: Optional[dtime]) -> int:
        if t is None:
            return _NUM_HOUR_ROWS
        minutes = t.hour * 60 + t.minute
        row = (minutes - _START_HOUR * 60 + 59) // 60  # ceil
        return max(1, min(_NUM_HOUR_ROWS, row))

    def _item_row_span(self, item) -> tuple[int, int]:
        start_row = self._start_row(item.start_time)
        end_row = self._end_row(item.end_time) if item.end_time else start_row + 1
        if end_row <= start_row:
            end_row = start_row + 1
        end_row = min(_NUM_HOUR_ROWS, end_row)
        return start_row, max(1, end_row - start_row)

    def _make_item_widget(self, item) -> QLabel:
        course = self._course_obj_map.get(item.course_id)
        color = course.color_hex if course else config.DEFAULT_COURSE_COLOR
        code = course.code if course else "?"
        t_s = item.start_time.strftime("%H:%M") if item.start_time else ""
        text = f"{t_s} {code} {item.title}".strip()
        if item.room:
            text += f" · {item.room}"
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"background-color: {color}; color: white; border-radius: 4px; "
            f"padding: 3px; font-size: 8pt;"
        )
        lbl.setToolTip(("⚠ flagged — " if getattr(item, "flagged", False) else "") + text)
        return lbl

    def _refresh_week(self) -> None:
        widgets.clear_layout(self.week_grid)
        try:
            if self._week_monday is None:
                self._week_monday = self._default_week_monday()
            monday = self._week_monday
            schedule = self.store.list_schedule()
            holidays = self.store.list_holidays()
            prelabs = self.store.list_prelabs()
            assessments = self.store.list_assessments()
            view = scheduler.week_view(monday, schedule, holidays, prelabs, assessments)
        except Exception:
            log.exception("TrackerPage: failed to build week view")
            self.statusMessage.emit("Could not load the week view.")
            return

        wk_num = scheduler.term_week_number(monday, self._week1_monday)
        friday = monday + timedelta(days=4)
        self.week_label.setText(f"Week {wk_num}: {monday.strftime('%b %d')} – {friday.strftime('%b %d')}")

        today = date.today()
        self.week_grid.addWidget(QLabel(""), 0, 0)
        for day_idx in range(5):
            d = monday + timedelta(days=day_idx)
            header = QLabel(f"{config.WEEKDAY_NAMES[day_idx][:3]} {d.strftime('%b %d')}")
            header.setAlignment(Qt.AlignCenter)
            is_today = d == today
            is_holiday = scheduler.is_holiday(d, holidays)
            if is_today:
                bg = "#2563eb"
            elif is_holiday:
                bg = "#4b5563"
            else:
                bg = "#1e293b"
            header.setStyleSheet(
                f"background-color: {bg}; color: white; font-weight: 700; "
                f"padding: 4px; border-radius: 4px;"
            )
            self.week_grid.addWidget(header, 0, day_idx + 1)

        for row_i in range(_NUM_HOUR_ROWS):
            hour = _START_HOUR + row_i
            hour_lbl = QLabel(f"{hour:02d}:00")
            hour_lbl.setStyleSheet("color: #94a3b8;")
            self.week_grid.addWidget(hour_lbl, row_i + 1, 0)

        for day_idx in range(5):
            d = monday + timedelta(days=day_idx)
            items = view.get(d, [])
            by_row_start: dict[int, list] = {}
            for item in items:
                row_start, span = self._item_row_span(item)
                by_row_start.setdefault(row_start, []).append((item, span))
            for row_start, entries in by_row_start.items():
                if len(entries) == 1:
                    item, span = entries[0]
                    self.week_grid.addWidget(self._make_item_widget(item), row_start + 1, day_idx + 1, span, 1)
                else:
                    container = QWidget()
                    cl = QVBoxLayout(container)
                    cl.setContentsMargins(0, 0, 0, 0)
                    cl.setSpacing(2)
                    max_span = 1
                    for item, span in entries:
                        cl.addWidget(self._make_item_widget(item))
                        max_span = max(max_span, span)
                    self.week_grid.addWidget(container, row_start + 1, day_idx + 1, max_span, 1)

        for col in range(1, 6):
            self.week_grid.setColumnStretch(col, 1)
        for row_i in range(1, _NUM_HOUR_ROWS + 1):
            self.week_grid.setRowMinimumHeight(row_i, 34)
