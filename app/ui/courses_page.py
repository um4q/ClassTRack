"""
Courses page (build spec §5.3, top half only - Course Detail lives in its own
page/file). A grid of large, dynamic, per-course buttons built from
``store.list_courses(active_only=True)`` - one button per row, colored by
``course.color_hex``, summarizing current grade / target, topic-progress %,
labs completed, and the next scheduled lab. Clicking a button asks the shell
to open Course Detail; right-click offers Edit / Archive / Delete.
"""
from __future__ import annotations

import logging
from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from app import config, models
from app.excel_store import ExcelStore
from app.services import grades, progress
from app.ui import dialogs, widgets
from app.ui.table_models import ColumnSpec

log = logging.getLogger("study_tracker")

# --------------------------------------------------------------------------
# Column spec for the Add/Edit course dialog (there is no predefined
# COURSE_COLUMNS in table_models.py - Courses is never shown as a QTableView).
# --------------------------------------------------------------------------
COURSE_COLUMNS: list[ColumnSpec] = [
    ColumnSpec("code", "Code", editable=True),
    ColumnSpec("name", "Name", editable=True),
    ColumnSpec("term", "Term", editable=True),
    ColumnSpec("instructor", "Instructor", editable=True),
    ColumnSpec("email", "Email", editable=True),
    ColumnSpec("office", "Office", editable=True),
    ColumnSpec("lecture_section", "Lecture section", editable=True),
    ColumnSpec("lab_section", "Lab section", editable=True),
    ColumnSpec("target_grade", "Target grade %", kind="float", editable=True),
    ColumnSpec("color_hex", "Color (hex)", editable=True),
    ColumnSpec("syllabus_path", "Syllabus path", editable=True),
    ColumnSpec("theory_coursepack", "Theory coursepack", editable=True),
    ColumnSpec("lab_coursepack", "Lab coursepack", editable=True),
    ColumnSpec("min_lab_completion_pct", "Min lab completion %", kind="float", editable=True),
    ColumnSpec("notes", "Notes", editable=True),
    ColumnSpec("active", "Active", kind="bool", editable=True),
]

_GRID_COLUMNS = 2


class CoursesPage(QWidget):
    """Top-level "Courses" page: a grid of course buttons + "+ Add course"."""

    statusMessage = Signal(str)
    navigateTo = Signal(str)
    openCourse = Signal(int)  # course_id

    def __init__(self, store: ExcelStore, parent=None):
        super().__init__(parent)
        self.store = store

        outer = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        title = QLabel("Courses")
        title.setStyleSheet("font-size: 16pt; font-weight: 700;")
        toolbar.addWidget(title)
        toolbar.addStretch(1)
        add_btn = QPushButton("+ Add course")
        add_btn.clicked.connect(self._on_add_course)
        toolbar.addWidget(add_btn)
        outer.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._grid_host = QWidget()
        self._grid_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._grid_layout = QVBoxLayout(self._grid_host)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.addStretch(1)
        scroll.setWidget(self._grid_host)
        outer.addWidget(scroll, 1)

        self.refresh()

    # ----------------------------------------------------------------
    # refresh
    # ----------------------------------------------------------------
    def refresh(self) -> None:
        try:
            courses = self.store.list_courses(active_only=True)
        except Exception:
            log.exception("CoursesPage.refresh: could not list courses")
            self.statusMessage.emit("Could not load courses.")
            courses = []

        widgets.clear_layout(self._grid_layout)
        today = date.today()

        items: list[tuple[str, str, object]] = []
        ordered_courses: list[models.Course] = []
        for course in courses:
            try:
                label = self._build_label(course, today)
            except Exception:
                log.exception(
                    "CoursesPage.refresh: could not build label for course_id=%s",
                    getattr(course, "course_id", "?"),
                )
                label = f"{course.code or '?'} — {course.name or '?'}\n(data unavailable)"
            color = course.color_hex or config.DEFAULT_COURSE_COLOR
            cid = course.course_id
            items.append((label, color, (lambda cid=cid: self._on_course_clicked(cid))))
            ordered_courses.append(course)

        grid_widget = widgets.make_button_grid(items, columns=_GRID_COLUMNS)
        for btn in grid_widget.findChildren(QPushButton):
            btn.setMinimumHeight(72)
        buttons = grid_widget.findChildren(QPushButton)
        for btn, course in zip(buttons, ordered_courses):
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, b=btn, c=course: self._show_context_menu(b, pos, c)
            )
        self._grid_layout.insertWidget(self._grid_layout.count() - 1, grid_widget)

    def _build_label(self, course: models.Course, today: date) -> str:
        assessments = self.store.list_assessments(course.course_id)
        weights = self.store.list_grade_weights(course.course_id)
        topics = self.store.list_topics(course.course_id)
        prelabs = self.store.list_prelabs(course.course_id)

        grade_result = grades.compute_grade(assessments, weights)
        current = grade_result.current
        target = course.target_grade
        grade_str = f"{current:.1f}%" if current is not None else "—"
        target_str = f"{target:.1f}%" if target is not None else "—"

        topic_pct = progress.topic_progress_pct(topics)

        done, _due, total_term = grades.labs_completed_counts(prelabs, today)

        upcoming = [
            p for p in prelabs
            if p.lab_date is not None and p.lab_date >= today and p.lab_type != "None"
        ]
        next_lab = min(upcoming, key=lambda p: p.lab_date).lab_date if upcoming else None
        next_lab_str = next_lab.strftime("%Y-%m-%d") if next_lab else "none scheduled"

        return (
            f"{course.code or '?'} — {course.name or '?'}\n"
            f"Grade: {grade_str} / {target_str}    Topics: {topic_pct:.0f}%\n"
            f"Labs: {done}/{total_term} done    Next lab: {next_lab_str}"
        )

    # ----------------------------------------------------------------
    # slots
    # ----------------------------------------------------------------
    def _on_course_clicked(self, course_id: int) -> None:
        try:
            self.openCourse.emit(course_id)
        except Exception:
            log.exception("CoursesPage: openCourse emit failed for course_id=%s", course_id)
            self.statusMessage.emit("Could not open that course.")

    def _on_add_course(self) -> None:
        try:
            result = dialogs.edit_row(
                self, "Add course", COURSE_COLUMNS, models.Course(),
                exclude_attrs={"course_id"}, multiline_attrs={"notes"},
            )
            if result is None:
                return
            self.store.add_course(result)
            self.refresh()
        except Exception:
            log.exception("CoursesPage: failed to add course")
            self.statusMessage.emit("Could not add the course.")

    def _show_context_menu(self, button: QPushButton, pos, course: models.Course) -> None:
        try:
            menu = QMenu(button)
            edit_action = menu.addAction("Edit")
            archive_action = menu.addAction("Archive")
            delete_action = menu.addAction("Delete")
            chosen = menu.exec(button.mapToGlobal(pos))
            if chosen is edit_action:
                self._on_edit_course(course)
            elif chosen is archive_action:
                self._on_archive_course(course)
            elif chosen is delete_action:
                self._on_delete_course(course)
        except Exception:
            log.exception("CoursesPage: context menu failed for course_id=%s", getattr(course, "course_id", "?"))
            self.statusMessage.emit("Could not complete that action.")

    def _on_edit_course(self, course: models.Course) -> None:
        try:
            result = dialogs.edit_row(
                self, f"Edit course — {course.code}", COURSE_COLUMNS, course,
                exclude_attrs={"course_id"}, multiline_attrs={"notes"},
            )
            if result is None:
                return
            self.store.update_course(result)
            self.refresh()
        except Exception:
            log.exception("CoursesPage: failed to edit course_id=%s", getattr(course, "course_id", "?"))
            self.statusMessage.emit("Could not save the course.")

    def _on_archive_course(self, course: models.Course) -> None:
        try:
            if not widgets.confirm(
                self, f"Archive {course.code} — {course.name}? "
                      f"It will be hidden from the active course list."
            ):
                return
            course.active = False
            self.store.update_course(course)
            self.refresh()
        except Exception:
            log.exception("CoursesPage: failed to archive course_id=%s", getattr(course, "course_id", "?"))
            self.statusMessage.emit("Could not archive the course.")

    def _on_delete_course(self, course: models.Course) -> None:
        try:
            if not widgets.confirm(
                self, f"Delete {course.code} — {course.name}? "
                      f"This permanently removes the course and all its rows, and cannot be undone."
            ):
                return
            self.store.delete_course(course.course_id)
            self.refresh()
        except Exception:
            log.exception("CoursesPage: failed to delete course_id=%s", getattr(course, "course_id", "?"))
            self.statusMessage.emit("Could not delete the course.")
