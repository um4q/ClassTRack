"""
Roadmap page (build spec §5.5): a per-course study roadmap with two views -
a visual Timeline (weeks 1-16 as columns, one lane since this view is scoped
to the selected course, drawn with QGraphicsView/QGraphicsScene) and an
editable List (table_models.DataclassTableModel + ROADMAP_COLUMNS).

Nothing here is hardcoded per course/topic; every course/milestone/exam is
pulled fresh from ExcelStore at refresh() time (build spec §1 "Dynamic
buttons").
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGraphicsPolygonItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsTextItem, QGraphicsView, QHBoxLayout,
    QInputDialog, QLabel, QPushButton, QTableView, QTabWidget, QVBoxLayout,
    QWidget,
)

from app import config, models
from app.excel_store import ExcelStore
from app.services import scheduler
from app.ui import dialogs, table_models, widgets

log = logging.getLogger("study_tracker")

# --------------------------------------------------------------------------
# Timeline layout constants
# --------------------------------------------------------------------------
NUM_WEEKS = 16
COL_WIDTH = 100
HEADER_H = 34
LANE_Y = HEADER_H + 12
LANE_H = 56
TOTAL_H = LANE_Y + LANE_H + 24

_EXAM_TYPES = ("Midterm", "Final", "Quiz", "Practical Lab Assessment")

_SEPARATOR_COLOR = "#334155"
_HEADER_TEXT_COLOR = "#cbd5e1"
_SCENE_BG_COLOR = "#0f172a"
_HOLIDAY_SHADE = QColor(239, 68, 68, 35)
_TODAY_COLOR = "#ef4444"

_ADD_EDIT_EXCLUDE = {"roadmap_id", "course_id", "auto_generated"}


class RoadmapPage(QWidget):
    """Course-scoped roadmap: visual timeline + editable list."""

    statusMessage = Signal(str)
    navigateTo = Signal(str)

    def __init__(self, store: ExcelStore, parent=None):
        super().__init__(parent)
        self.store = store

        self._courses: list[models.Course] = []
        self._course_id: Optional[int] = None
        self._week1_monday: date = date.fromisoformat(config.SETTINGS_DEFAULTS["week1_monday"])
        self._semester_end: date = date.fromisoformat(config.SETTINGS_DEFAULTS["semester_end"])

        outer = QVBoxLayout(self)

        # -- Course selector --------------------------------------------
        top = QHBoxLayout()
        top.addWidget(QLabel("Course:"))
        self.course_combo = QComboBox()
        self.course_combo.setMinimumWidth(260)
        self.course_combo.currentIndexChanged.connect(self._guard(self._on_course_changed))
        top.addWidget(self.course_combo)
        top.addStretch(1)
        outer.addLayout(top)

        # -- Summary strip ------------------------------------------------
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet("font-weight: 600; padding: 2px 0 6px 0;")
        self.summary_label.setWordWrap(True)
        outer.addWidget(self.summary_label)

        # -- Toolbar --------------------------------------------------------
        toolbar = QHBoxLayout()
        add_btn = QPushButton("Add milestone")
        add_btn.clicked.connect(self._guard(self._on_add_milestone))
        toolbar.addWidget(add_btn)
        gen_btn = QPushButton("Generate from syllabus")
        gen_btn.clicked.connect(self._guard(self._on_generate_from_syllabus))
        toolbar.addWidget(gen_btn)
        slip_btn = QPushButton("Mark slipped")
        slip_btn.clicked.connect(self._guard(self._on_mark_slipped))
        toolbar.addWidget(slip_btn)
        shift_btn = QPushButton("Shift remaining by N days")
        shift_btn.clicked.connect(self._guard(self._on_shift_remaining))
        toolbar.addWidget(shift_btn)
        toolbar.addStretch(1)
        outer.addLayout(toolbar)

        # -- Tabs: Timeline / List -----------------------------------------
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self._build_timeline_tab()
        self._build_list_tab()

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

    def _current_course(self) -> Optional[models.Course]:
        return next((c for c in self._courses if c.course_id == self._course_id), None)

    # ------------------------------------------------------------------
    # Timeline tab
    # ------------------------------------------------------------------
    def _build_timeline_tab(self) -> None:
        tab = QWidget()
        v = QVBoxLayout(tab)
        self.scene = QGraphicsScene()
        self.timeline_view = QGraphicsView(self.scene)
        self.timeline_view.setRenderHint(QPainter.Antialiasing)
        self.timeline_view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.timeline_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.timeline_view.setMinimumHeight(TOTAL_H + 40)
        v.addWidget(self.timeline_view, 1)
        legend = QLabel(
            "Bar color = milestone status · dashed line = this course's exam due dates · "
            "red shading = holiday week · red vertical line = today"
        )
        legend.setWordWrap(True)
        legend.setStyleSheet("color: #94a3b8; font-size: 9pt;")
        v.addWidget(legend)
        self.tabs.addTab(tab, "Timeline")

    def _date_to_x(self, d: date, week1_monday: date) -> float:
        """Continuous x-coordinate for a date: (week_number - 1 + day-of-week
        fraction) * COL_WIDTH, clamped to the visible 16-week horizon."""
        horizon_end_excl = week1_monday + timedelta(weeks=NUM_WEEKS)
        d_clamped = max(week1_monday, min(d, horizon_end_excl - timedelta(days=1)))
        wk = max(1, min(NUM_WEEKS, scheduler.term_week_number(d_clamped, week1_monday)))
        wk_monday = scheduler.week_start(wk, week1_monday)
        day_offset = (d_clamped - wk_monday).days
        frac = max(0.0, min(1.0, day_offset / 7.0))
        return (wk - 1 + frac) * COL_WIDTH

    def _refresh_timeline(self) -> None:
        self.scene.clear()
        self.scene.setBackgroundBrush(QBrush(QColor(_SCENE_BG_COLOR)))
        week1 = self._week1_monday

        if not self._course_id:
            msg = QGraphicsTextItem("Add a course first (Courses page) to see a roadmap timeline.")
            msg.setDefaultTextColor(QColor(_HEADER_TEXT_COLOR))
            msg.setPos(8, 8)
            self.scene.addItem(msg)
            self.scene.setSceneRect(0, 0, 440, 60)
            return

        try:
            holidays = self.store.list_holidays()
            roadmap_items = sorted(
                self.store.list_roadmap(self._course_id),
                key=lambda r: (r.sort_order, r.start_date or date.max),
            )
            assessments = [
                a for a in self.store.list_assessments(self._course_id)
                if a.type in _EXAM_TYPES and a.due_date is not None
            ]
        except Exception:
            log.exception("RoadmapPage: failed to load timeline data for course_id=%s", self._course_id)
            self.statusMessage.emit("Could not load the roadmap timeline.")
            holidays, roadmap_items, assessments = [], [], []

        total_w = NUM_WEEKS * COL_WIDTH
        self.scene.setSceneRect(0, 0, total_w, TOTAL_H)

        # -- Week columns: header labels + holiday shading + separators --
        for wk in range(1, NUM_WEEKS + 1):
            x0 = (wk - 1) * COL_WIDTH
            wk_monday = scheduler.week_start(wk, week1)
            wk_days = [wk_monday + timedelta(days=i) for i in range(7)]
            if any(scheduler.is_holiday(d, holidays) for d in wk_days):
                shade = QGraphicsRectItem(x0, 0, COL_WIDTH, TOTAL_H)
                shade.setBrush(QBrush(_HOLIDAY_SHADE))
                shade.setPen(QPen(Qt.NoPen))
                shade.setZValue(-10)
                self.scene.addItem(shade)

            sep = self.scene.addLine(x0, 0, x0, TOTAL_H, QPen(QColor(_SEPARATOR_COLOR)))
            sep.setZValue(-5)

            header = QGraphicsTextItem(f"W{wk}\n{wk_monday.strftime('%b %d')}")
            hf = header.font()
            hf.setPointSize(8)
            hf.setBold(True)
            header.setFont(hf)
            header.setDefaultTextColor(QColor(_HEADER_TEXT_COLOR))
            header.setPos(x0 + 4, 0)
            self.scene.addItem(header)
        self.scene.addLine(total_w, 0, total_w, TOTAL_H, QPen(QColor(_SEPARATOR_COLOR)))

        # -- Milestone bars (single lane) ---------------------------------
        for item in roadmap_items:
            if item.start_date is None or item.end_date is None:
                continue
            x1 = self._date_to_x(item.start_date, week1)
            x2 = self._date_to_x(item.end_date + timedelta(days=1), week1)
            w = max(x2 - x1, 14)
            color = QColor(config.STATUS_BADGE_COLORS.get(item.status, "#6b7280"))

            rect = QGraphicsRectItem(x1, LANE_Y, w, LANE_H)
            rect.setBrush(QBrush(color))
            rect.setPen(QPen(color.darker(140), 1))
            tip = (
                f"{item.milestone}\n{item.start_date.isoformat()} – {item.end_date.isoformat()}\n"
                f"Status: {item.status}"
            )
            if item.auto_generated:
                tip += "\n(auto-generated — replaced by re-running \"Generate from syllabus\")"
            rect.setToolTip(tip)
            rect.setZValue(0)
            self.scene.addItem(rect)

            text = QGraphicsTextItem(item.milestone)
            text.setTextWidth(max(w - 8, 20))
            text.setDefaultTextColor(QColor("white"))
            tf = text.font()
            tf.setPointSize(8)
            text.setFont(tf)
            text.setPos(x1 + 4, LANE_Y + 4)
            text.setToolTip(tip)
            text.setZValue(1)
            self.scene.addItem(text)

        # -- Exam due-date markers -----------------------------------------
        for a in assessments:
            x = self._date_to_x(a.due_date, week1)
            color = QColor(widgets.urgency_color(a.due_date, a.due_time))
            line = self.scene.addLine(x, 0, x, TOTAL_H, QPen(color, 2, Qt.DashLine))
            line.setZValue(5)

            poly = QPolygonF([QPointF(x - 6, HEADER_H), QPointF(x + 6, HEADER_H), QPointF(x, HEADER_H + 10)])
            triangle = QGraphicsPolygonItem(poly)
            triangle.setBrush(QBrush(color))
            triangle.setPen(QPen(color.darker(150)))
            tip = f"{a.type}: {a.title}\nDue {a.due_date.isoformat()}"
            if a.due_time:
                tip += f" {a.due_time.strftime('%H:%M')}"
            if a.is_flagged:
                tip += f"\n⚠ {a.notes}"
            triangle.setToolTip(tip)
            triangle.setZValue(6)
            self.scene.addItem(triangle)

        # -- "Today" line ----------------------------------------------------
        today = date.today()
        if week1 <= today < week1 + timedelta(weeks=NUM_WEEKS):
            x = self._date_to_x(today, week1)
            today_line = self.scene.addLine(x, 0, x, TOTAL_H, QPen(QColor(_TODAY_COLOR), 2))
            today_line.setZValue(10)
            today_text = QGraphicsTextItem("Today")
            today_text.setDefaultTextColor(QColor(_TODAY_COLOR))
            ttf = today_text.font()
            ttf.setBold(True)
            ttf.setPointSize(8)
            today_text.setFont(ttf)
            today_text.setPos(x + 2, TOTAL_H - 16)
            today_text.setZValue(10)
            self.scene.addItem(today_text)

    # ------------------------------------------------------------------
    # List tab
    # ------------------------------------------------------------------
    def _build_list_tab(self) -> None:
        tab = QWidget()
        v = QVBoxLayout(tab)

        self.list_model = table_models.DataclassTableModel(table_models.ROADMAP_COLUMNS)
        self.list_model.on_edit = self._on_list_cell_edited
        self.list_view = QTableView()
        self.list_view.setModel(self.list_model)
        self.list_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.list_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_view.horizontalHeader().setStretchLastSection(True)
        self.list_view.doubleClicked.connect(self._guard(self._on_edit_selected))
        table_models.apply_delegates(self.list_view, self.list_model)
        v.addWidget(self.list_view, 1)

        row_btns = QHBoxLayout()
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._guard(self._on_edit_selected))
        row_btns.addWidget(edit_btn)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._guard(self._on_delete_selected))
        row_btns.addWidget(del_btn)
        row_btns.addStretch(1)
        v.addLayout(row_btns)

        self.tabs.addTab(tab, "List")

    def _refresh_list(self) -> None:
        try:
            items = sorted(
                self.store.list_roadmap(self._course_id) if self._course_id else [],
                key=lambda r: (r.sort_order, r.start_date or date.max),
            )
        except Exception:
            log.exception("RoadmapPage: failed to load roadmap list for course_id=%s", self._course_id)
            self.statusMessage.emit("Could not load the roadmap list.")
            items = []
        self.list_model.set_rows(items)
        table_models.apply_delegates(self.list_view, self.list_model)

    def _on_list_cell_edited(self, obj, attr, value):
        try:
            self.store.update_roadmap_item(obj)
            return True
        except Exception:
            log.exception("RoadmapPage: failed to save roadmap edit (attr=%s)", attr)
            self.statusMessage.emit("Could not save that change.")
            return False

    def _selected_rows(self) -> list[models.RoadmapItem]:
        sel = self.list_view.selectionModel()
        if sel is None:
            return []
        rows = sorted({idx.row() for idx in sel.selectedRows()})
        all_rows = self.list_model.rows()
        return [all_rows[r] for r in rows if 0 <= r < len(all_rows)]

    # ------------------------------------------------------------------
    # refresh() - re-pulls everything from the store and rebuilds the page
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        try:
            self._week1_monday = self.store.setting_date("week1_monday") or self._week1_monday
            self._semester_end = self.store.setting_date("semester_end") or self._semester_end
            self._courses = self.store.list_courses()
        except Exception:
            log.exception("RoadmapPage: failed to load courses")
            self.statusMessage.emit("Roadmap page failed to load courses — see log.")
            self._courses = []

        self.course_combo.blockSignals(True)
        self.course_combo.clear()
        for c in self._courses:
            self.course_combo.addItem(f"{c.code} — {c.name}", c.course_id)
        if self._course_id is None or not any(c.course_id == self._course_id for c in self._courses):
            self._course_id = self._courses[0].course_id if self._courses else None
        idx = self.course_combo.findData(self._course_id) if self._course_id is not None else -1
        self.course_combo.setCurrentIndex(idx)
        self.course_combo.blockSignals(False)

        self._refresh_course_dependent()

    def _on_course_changed(self, index: int) -> None:
        self._course_id = self.course_combo.currentData()
        self._refresh_course_dependent()

    def _refresh_course_dependent(self) -> None:
        self._refresh_summary()
        self._refresh_list()
        self._refresh_timeline()

    def _refresh_summary(self) -> None:
        if not self._course_id:
            self.summary_label.setText("No course selected.")
            return
        try:
            topics = self.store.list_topics(self._course_id)
            today = date.today()
            total_wk = scheduler.term_week_number(self._semester_end, self._week1_monday)
            cur_wk = scheduler.term_week_number(today, self._week1_monday)
            weeks_left = max(0, total_wk - cur_wk + 1)
            topics_left = sum(1 for t in topics if t.status != "Mastered")
            current_pace, needed_pace = scheduler.roadmap_pace(topics, today, weeks_left)
            needed_txt = "∞" if needed_pace == float("inf") else f"{needed_pace:.1f}"
            self.summary_label.setText(
                f"Week {cur_wk} of {total_wk}  ·  {weeks_left} week(s) left  ·  "
                f"{topics_left} topic(s) left  ·  "
                f"Pace: {current_pace:.1f}/wk current vs {needed_txt}/wk needed"
            )
        except Exception:
            log.exception("RoadmapPage: failed to compute summary for course_id=%s", self._course_id)
            self.summary_label.setText("Summary unavailable — see log.")

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------
    def _on_add_milestone(self) -> None:
        if not self._course_id:
            self.statusMessage.emit("Select a course first.")
            return
        new_item = models.RoadmapItem(course_id=self._course_id, status="Planned")
        result = dialogs.edit_row(
            self, "Add milestone", table_models.ROADMAP_COLUMNS, new_item,
            exclude_attrs=_ADD_EDIT_EXCLUDE,
        )
        if result is None:
            return
        self.store.add_roadmap_item(result)
        self.refresh()

    def _on_edit_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            self.statusMessage.emit("Select a milestone in the List tab first.")
            return
        obj = rows[0]
        result = dialogs.edit_row(
            self, "Edit milestone", table_models.ROADMAP_COLUMNS, obj,
            exclude_attrs=_ADD_EDIT_EXCLUDE,
        )
        if result is None:
            return
        result.roadmap_id = obj.roadmap_id
        result.course_id = obj.course_id
        result.auto_generated = obj.auto_generated
        self.store.update_roadmap_item(result)
        self.refresh()

    def _on_delete_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            self.statusMessage.emit("Select one or more milestones in the List tab first.")
            return
        label = rows[0].milestone if len(rows) == 1 else f"{len(rows)} milestones"
        if not widgets.confirm(self, f"Delete {label}? This cannot be undone."):
            return
        for obj in rows:
            self.store.delete_roadmap_item(obj.roadmap_id)
        self.refresh()

    def _on_generate_from_syllabus(self) -> None:
        if not self._course_id:
            self.statusMessage.emit("Select a course first.")
            return
        course = self._current_course()
        self.store.delete_auto_roadmap(self._course_id)
        topics = self.store.list_topics(self._course_id)
        week1 = self.store.setting_date("week1_monday") or self._week1_monday
        semester_end = self.store.setting_date("semester_end") or self._semester_end
        items = scheduler.generate_roadmap(self._course_id, topics, week1, date.today(), semester_end)
        for it in items:
            self.store.add_roadmap_item(it)
        self.refresh()
        label = course.code if course else "course"
        self.statusMessage.emit(f"Generated {len(items)} roadmap milestone(s) for {label} from the syllabus.")

    def _on_mark_slipped(self) -> None:
        rows = self._selected_rows()
        if not rows:
            self.statusMessage.emit("Select one or more milestones in the List tab first.")
            return
        for obj in rows:
            obj.status = "Slipped"
            self.store.update_roadmap_item(obj)
        self.refresh()
        self.statusMessage.emit(f"Marked {len(rows)} milestone(s) slipped.")

    def _on_shift_remaining(self) -> None:
        if not self._course_id:
            self.statusMessage.emit("Select a course first.")
            return
        n, ok = QInputDialog.getInt(
            self, "Shift remaining milestones",
            "Shift start/end dates by how many days? (negative = earlier)",
            0, -365, 365, 1,
        )
        if not ok or n == 0:
            return
        today = date.today()
        try:
            items = self.store.list_roadmap(self._course_id)
        except Exception:
            log.exception("RoadmapPage: failed to load roadmap items to shift for course_id=%s", self._course_id)
            self.statusMessage.emit("Could not load milestones to shift.")
            return
        shifted = 0
        for it in items:
            if it.status != "Done" and it.start_date is not None and it.start_date >= today:
                it.start_date = it.start_date + timedelta(days=n)
                if it.end_date is not None:
                    it.end_date = it.end_date + timedelta(days=n)
                self.store.update_roadmap_item(it)
                shifted += 1
        self.refresh()
        self.statusMessage.emit(f"Shifted {shifted} milestone(s) by {n} day(s).")
