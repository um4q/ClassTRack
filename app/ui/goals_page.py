"""
Goals page (build spec §5.10): a table of Goals rows (add/edit/delete through
the generic ``dialogs.edit_row`` form) plus one progress bar per goal below
it. ``refresh()`` recomputes every goal's ``current_value``/``status`` from
the live workbook data per §6.9/§6.10 before redrawing either the table or
the progress-bar list, so this page is always the source of truth for a
goal's numbers - editing a goal elsewhere never desyncs it.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QInputDialog, QLabel, QPushButton,
    QScrollArea, QTableView, QVBoxLayout, QWidget,
)

from app import config, models
from app.excel_store import ExcelStore
from app.services import grades, progress
from app.ui import dialogs, table_models, widgets

log = logging.getLogger("study_tracker")

# Fields hand-entered through the Add/Edit form. goal_id is the primary key
# (never edited); current_value is computed by refresh(), never hand-entered.
_DIALOG_EXCLUDE = {"goal_id", "current_value"}


class GoalsPage(QWidget):
    """Goals: editable table + per-goal progress bars (build spec §5.10)."""

    statusMessage = Signal(str)
    navigateTo = Signal(str)

    def __init__(self, store: ExcelStore, parent=None):
        super().__init__(parent)
        self.store = store

        self._courses: list[models.Course] = []
        self._course_map: dict[Optional[int], str] = {}

        outer = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        title = QLabel("Goals")
        title.setStyleSheet("font-size: 16pt; font-weight: 700;")
        toolbar.addWidget(title)
        toolbar.addStretch(1)
        add_btn = QPushButton("+ Add goal")
        add_btn.clicked.connect(self._guard(self._on_add_goal))
        toolbar.addWidget(add_btn)
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._guard(self._on_edit_goal))
        toolbar.addWidget(edit_btn)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._guard(self._on_delete_goal))
        toolbar.addWidget(delete_btn)
        outer.addLayout(toolbar)

        self.table_model = table_models.DataclassTableModel([], [])
        self.table_model.on_edit = self._on_cell_edited
        self.table_view = QTableView()
        self.table_view.setModel(self.table_model)
        self.table_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table_view.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.table_view, 1)

        progress_lbl = QLabel("Progress")
        progress_lbl.setStyleSheet("font-weight: 700; margin-top: 8px;")
        outer.addWidget(progress_lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._cards_host = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.addStretch(1)
        scroll.setWidget(self._cards_host)
        outer.addWidget(scroll, 1)

        self.refresh()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _guard(self, fn):
        """Wrap a slot so it never raises: log + statusMessage instead."""
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                log.exception("GoalsPage: action failed")
                self.statusMessage.emit("That action failed - see log.")
        return wrapped

    def _selected_goal(self) -> Optional[models.Goal]:
        sel = self.table_view.selectionModel()
        row = -1
        if sel is not None:
            rows = sel.selectedRows()
            if rows:
                row = rows[0].row()
        if row < 0:
            idx = self.table_view.currentIndex()
            if idx.isValid():
                row = idx.row()
        if 0 <= row < len(self.table_model.rows()):
            return self.table_model.row_object(row)
        return None

    # ------------------------------------------------------------------
    # refresh() - recompute every goal, then redraw table + progress bars
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        today = date.today()
        try:
            self._courses = self.store.list_courses(active_only=True)
        except Exception:
            log.exception("GoalsPage: failed to load courses")
            self.statusMessage.emit("Could not load courses.")
            self._courses = []
        self._course_map = {c.course_id: c.code for c in self._courses}
        # Every course (active or not) so an old course-scoped goal still
        # shows a code instead of "?"; "—" for semester/weekly goals.
        try:
            all_courses = self.store.list_courses()
        except Exception:
            all_courses = self._courses
        display_map: dict[Optional[int], str] = {c.course_id: c.code for c in all_courses}
        display_map[None] = "—"

        try:
            goals = self.store.list_goals()
        except Exception:
            log.exception("GoalsPage: failed to load goals")
            self.statusMessage.emit("Could not load goals.")
            goals = []

        try:
            all_topics = self.store.list_topics()
        except Exception:
            log.exception("GoalsPage: failed to load topics")
            all_topics = []
        try:
            all_prelabs = self.store.list_prelabs()
        except Exception:
            log.exception("GoalsPage: failed to load pre-labs")
            all_prelabs = []
        active_course_ids = {c.course_id for c in self._courses}

        for goal in goals:
            try:
                self._recompute_goal(goal, today, all_topics, all_prelabs, active_course_ids)
            except Exception:
                log.exception("GoalsPage: failed to recompute goal_id=%s", getattr(goal, "goal_id", "?"))
                self.statusMessage.emit(
                    f"Could not recompute progress for goal '{getattr(goal, 'description', '') or '?'}'."
                )

        columns = [table_models.course_column(display_map)] + list(table_models.GOAL_COLUMNS)
        self.table_model.columns = columns
        self.table_model.set_rows(goals)
        table_models.apply_delegates(self.table_view, self.table_model)

        self._rebuild_cards(goals, display_map)

    def _recompute_goal(
        self,
        goal: models.Goal,
        today: date,
        all_topics: list[models.Topic],
        all_prelabs: list[models.PreLab],
        active_course_ids: set[int],
    ) -> None:
        """Recompute one Goal's current_value/status in place per §6.9/§6.10
        and persist only if something actually changed."""
        if goal.course_id:
            topics = [t for t in all_topics if t.course_id == goal.course_id]
            prelabs = [p for p in all_prelabs if p.course_id == goal.course_id]
            study_log = self.store.list_study_log(goal.course_id)
            questions = self.store.list_practice_questions(goal.course_id)
        else:
            topics = [t for t in all_topics if t.course_id in active_course_ids]
            prelabs = [p for p in all_prelabs if p.course_id in active_course_ids]
            study_log = self.store.list_study_log()
            questions = self.store.list_practice_questions()

        current_grade = None
        if goal.metric == "grade_at_least" and goal.course_id:
            course_assessments = self.store.list_assessments(goal.course_id)
            course_weights = self.store.list_grade_weights(goal.course_id)
            current_grade = grades.compute_grade(course_assessments, course_weights).current

        new_value = progress.compute_goal_progress(
            goal, today,
            topics=topics, study_log=study_log, prelabs=prelabs,
            questions=questions, current_grade=current_grade,
        )

        changed = False
        if new_value != goal.current_value:
            goal.current_value = new_value
            changed = True
        new_status = progress.goal_status(goal)
        if new_status != goal.status:
            goal.status = new_status
            changed = True
        if changed:
            self.store.update_goal(goal)

    # ------------------------------------------------------------------
    # Progress-bar cards
    # ------------------------------------------------------------------
    def _rebuild_cards(self, goals: list[models.Goal], display_map: dict[Optional[int], str]) -> None:
        widgets.clear_layout(self._cards_layout)
        for goal in goals:
            try:
                card = self._build_card(goal, display_map)
            except Exception:
                log.exception("GoalsPage: failed to build card for goal_id=%s", getattr(goal, "goal_id", "?"))
                continue
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
        if not goals:
            self._cards_layout.insertWidget(0, QLabel("No goals yet - add one above."))

    def _build_card(self, goal: models.Goal, display_map: dict[Optional[int], str]) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(frame)

        course_str = display_map.get(goal.course_id, "?")
        heading = goal.description.strip() if goal.description else goal.metric
        head_lbl = QLabel(f"[{goal.scope}] {course_str} — {heading}")
        head_lbl.setStyleSheet("font-weight: 600;")
        head_lbl.setWordWrap(True)
        layout.addWidget(head_lbl)

        current = goal.current_value
        current_str = "—" if current is None else f"{current:g}"
        sub_lbl = QLabel(f"{current_str} / {goal.target_value:g} ({goal.metric}) · {goal.status}")
        layout.addWidget(sub_lbl)

        pct = 0.0
        if goal.target_value:
            pct = min(100.0, (current or 0.0) / goal.target_value * 100)
        color = config.STATUS_BADGE_COLORS.get(goal.status)
        layout.addWidget(widgets.colored_progress_bar(pct, color))

        return frame

    # ------------------------------------------------------------------
    # Add / edit / delete
    # ------------------------------------------------------------------
    def _pick_course_or_none(self, prompt_title: str) -> tuple[bool, Optional[models.Course]]:
        """QInputDialog course picker; returns (ok, course_or_None). Course
        columns aren't part of GOAL_COLUMNS (mirrors how Tracker's Assessment
        add flow picks the course before the generic edit_row form)."""
        choices = ["(none - semester/weekly goal)"] + [c.code for c in self._courses]
        choice, ok = QInputDialog.getItem(self, prompt_title, "Course:", choices, 0, False)
        if not ok:
            return False, None
        course = next((c for c in self._courses if c.code == choice), None)
        return True, course

    def _on_add_goal(self) -> None:
        ok, course = self._pick_course_or_none("New goal")
        if not ok:
            return
        new_goal = models.Goal(
            course_id=course.course_id if course else None,
            scope="Course" if course else "Weekly",
            status="Active",
            start_date=date.today(),
        )
        result = dialogs.edit_row(
            self, "Add goal", table_models.GOAL_COLUMNS, new_goal,
            exclude_attrs=_DIALOG_EXCLUDE, multiline_attrs={"description"},
        )
        if result is None:
            return
        self.store.add_goal(result)
        self.refresh()

    def _on_edit_goal(self) -> None:
        goal = self._selected_goal()
        if goal is None:
            self.statusMessage.emit("Select a goal first.")
            return
        course = self.store.get_course(goal.course_id) if goal.course_id else None
        title = f"Edit goal — {course.code}" if course else "Edit goal"
        result = dialogs.edit_row(
            self, title, table_models.GOAL_COLUMNS, goal,
            exclude_attrs=_DIALOG_EXCLUDE, multiline_attrs={"description"},
        )
        if result is None:
            return
        self.store.update_goal(result)
        self.refresh()

    def _on_delete_goal(self) -> None:
        goal = self._selected_goal()
        if goal is None:
            self.statusMessage.emit("Select a goal first.")
            return
        label = goal.description or goal.metric
        if not widgets.confirm(self, f"Delete goal '{label}'? This cannot be undone."):
            return
        self.store.delete_goal(goal.goal_id)
        self.refresh()

    def _on_cell_edited(self, obj, attr, value):
        try:
            self.store.update_goal(obj)
            return True
        except Exception:
            log.exception("GoalsPage: failed to save goal edit (attr=%s)", attr)
            self.statusMessage.emit("Could not save that change.")
            return False
