"""
Study Log page (build spec section 5.9): a cross-course ``TimerWidget``
(course/topic/activity combos, Start/Pause/Stop backed by a 1s QTimer, a
Pomodoro 25/5 toggle with an inline banner - no shell tray dependency), a
manual "+ Add entry" flow, a full editable table over every
``StudyLog`` row (course column prepended), three charts/visuals (hours per
course this week, hours per day for the last 30 days, and a GitHub-style
term heatmap), and a study streak readout.

Nothing here is hardcoded per course/topic - every combo and chart is
rebuilt from ``store.list_courses()`` / ``store.list_topics()`` /
``store.list_study_log()`` at ``refresh()`` time, so a new workbook row shows
up with no code change (build spec section 1 "Dynamic buttons").
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QPushButton, QScrollArea, QTableView,
    QVBoxLayout, QWidget,
)
from PySide6.QtCharts import (
    QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QValueAxis,
)

from app import config, models
from app.excel_store import ExcelStore
from app.services import progress
from app.ui import dialogs, table_models, widgets

log = logging.getLogger("study_tracker")


def _heatmap_color(minutes: int) -> str:
    """5-bucket single-hue (green) intensity scale for the term heatmap."""
    if minutes <= 0:
        return "#1f2937"
    if minutes < 30:
        return "#14532d"
    if minutes < 60:
        return "#166534"
    if minutes < 120:
        return "#16a34a"
    return "#22c55e"


class StudyLogPage(QWidget):
    """Cross-course study timer + log table + charts + heatmap + streak."""

    statusMessage = Signal(str)
    navigateTo = Signal(str)

    def __init__(self, store: ExcelStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._courses: list[models.Course] = []
        self._course_map: dict[int, str] = {}

        # -- timer state ---------------------------------------------------
        self._timer_seconds = 0
        self._timer_running = False
        self._pomodoro_phase = "Study"  # "Study" | "Break"
        self._pomodoro_phase_seconds = 0
        self._qtimer = QTimer(self)
        self._qtimer.setInterval(1000)
        self._qtimer.timeout.connect(self._guard(self._on_tick))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(10)

        outer.addWidget(self._build_timer_group())

        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Add entry")
        add_btn.clicked.connect(self._guard(self._on_add_entry))
        toolbar.addWidget(add_btn)
        self._edit_guarded = self._guard(self._on_edit_entry)
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self._edit_guarded)
        toolbar.addWidget(edit_btn)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._guard(self._on_delete_entry))
        toolbar.addWidget(delete_btn)
        toolbar.addStretch(1)
        outer.addLayout(toolbar)

        self.model = table_models.DataclassTableModel([])
        self.model.on_edit = self._on_inline_edit
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda _idx: self._edit_guarded())
        self.table.setMinimumHeight(200)
        outer.addWidget(self.table, 2)

        outer.addWidget(self._build_charts_area(), 3)

        self.store.dataChanged.connect(lambda _sheet: self.refresh())
        self.refresh()

    # ======================================================================
    # Shared helpers
    # ======================================================================
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

    # ======================================================================
    # Timer group
    # ======================================================================
    def _build_timer_group(self) -> QGroupBox:
        box = QGroupBox("Timer")
        v = QVBoxLayout(box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Course:"))
        self.course_combo = QComboBox()
        self.course_combo.setMinimumWidth(140)
        self.course_combo.currentIndexChanged.connect(self._guard(self._on_course_combo_changed))
        row1.addWidget(self.course_combo)
        row1.addWidget(QLabel("Topic:"))
        self.topic_combo = QComboBox()
        self.topic_combo.setMinimumWidth(220)
        row1.addWidget(self.topic_combo, 1)
        row1.addWidget(QLabel("Activity:"))
        self.activity_combo = QComboBox()
        self.activity_combo.addItems(config.STUDYLOG_ACTIVITIES)
        row1.addWidget(self.activity_combo)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        self.elapsed_label = QLabel("00:00")
        self.elapsed_label.setStyleSheet("font-weight: 700; font-size: 16pt; margin-right: 12px;")
        row2.addWidget(self.elapsed_label)
        self.pomodoro_cb = QCheckBox(
            f"Pomodoro {config.DEFAULT_POMODORO_STUDY_MIN}/{config.DEFAULT_POMODORO_BREAK_MIN}"
        )
        row2.addWidget(self.pomodoro_cb)
        row2.addStretch(1)
        start_btn = QPushButton("Start")
        start_btn.clicked.connect(self._guard(self._on_start))
        pause_btn = QPushButton("Pause")
        pause_btn.clicked.connect(self._guard(self._on_pause))
        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(self._guard(self._on_stop))
        row2.addWidget(start_btn)
        row2.addWidget(pause_btn)
        row2.addWidget(stop_btn)
        v.addLayout(row2)

        self.pomodoro_banner = QLabel("")
        self.pomodoro_banner.setObjectName("BannerInfo")
        self.pomodoro_banner.hide()
        v.addWidget(self.pomodoro_banner)

        self.streak_label = QLabel("0 day streak, best 0")
        self.streak_label.setStyleSheet("color: #94a3b8; margin-top: 2px;")
        v.addWidget(self.streak_label)
        return box

    def _on_course_combo_changed(self, _index: Optional[int] = None) -> None:
        course_id = self.course_combo.currentData() if self.course_combo.count() else None
        self._reload_topic_combo(course_id)

    def _reload_course_combo(self) -> None:
        current = self.course_combo.currentData() if self.course_combo.count() else None
        self.course_combo.blockSignals(True)
        self.course_combo.clear()
        for c in self._courses:
            self.course_combo.addItem(c.code or f"Course {c.course_id}", c.course_id)
        idx = self.course_combo.findData(current) if current else -1
        if idx < 0:
            idx = 0 if self.course_combo.count() else -1
        if idx >= 0:
            self.course_combo.setCurrentIndex(idx)
        self.course_combo.blockSignals(False)
        self._reload_topic_combo(self.course_combo.currentData() if self.course_combo.count() else None)

    def _reload_topic_combo(self, course_id: Optional[int]) -> None:
        current_topic = self.topic_combo.currentData() if self.topic_combo.count() else None
        self.topic_combo.blockSignals(True)
        self.topic_combo.clear()
        self.topic_combo.addItem("(no topic)", 0)
        if course_id:
            try:
                topics = self.store.list_topics(course_id)
            except Exception:
                log.exception("StudyLogPage: failed to load topics for course_id=%s", course_id)
                topics = []
            for t in topics:
                label = f"{t.section} {t.title}".strip() or f"Topic {t.topic_id}"
                self.topic_combo.addItem(label, t.topic_id)
        idx = self.topic_combo.findData(current_topic) if current_topic else 0
        self.topic_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.topic_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Timer mechanics
    # ------------------------------------------------------------------
    def _reset_timer_state(self) -> None:
        self._qtimer.stop()
        self._timer_running = False
        self._timer_seconds = 0
        self._pomodoro_phase = "Study"
        self._pomodoro_phase_seconds = 0
        self.elapsed_label.setText("00:00")
        self.pomodoro_banner.hide()

    def _on_start(self) -> None:
        if self._timer_running:
            return
        if not self._courses:
            self.statusMessage.emit("Add a course first (Courses page).")
            return
        self._timer_running = True
        if self._timer_seconds == 0:
            self._pomodoro_phase = "Study"
            self._pomodoro_phase_seconds = 0
            self.pomodoro_banner.hide()
        self._qtimer.start()

    def _on_pause(self) -> None:
        self._timer_running = False
        self._qtimer.stop()

    def _on_tick(self) -> None:
        self._timer_seconds += 1
        mm, ss = divmod(self._timer_seconds, 60)
        self.elapsed_label.setText(f"{mm:02d}:{ss:02d}")
        if self.pomodoro_cb.isChecked():
            self._pomodoro_phase_seconds += 1
            target_min = (
                config.DEFAULT_POMODORO_STUDY_MIN if self._pomodoro_phase == "Study"
                else config.DEFAULT_POMODORO_BREAK_MIN
            )
            remaining = target_min * 60 - self._pomodoro_phase_seconds
            if remaining <= 0:
                self._switch_pomodoro_phase()
            else:
                pm, ps = divmod(remaining, 60)
                icon = "📚" if self._pomodoro_phase == "Study" else "☕"
                self.pomodoro_banner.setText(f"{icon} {self._pomodoro_phase} — {pm:02d}:{ps:02d} left")
                self.pomodoro_banner.show()
        else:
            self.pomodoro_banner.hide()

    def _switch_pomodoro_phase(self) -> None:
        if self._pomodoro_phase == "Study":
            self._pomodoro_phase = "Break"
            msg = f"Study block done — take a {config.DEFAULT_POMODORO_BREAK_MIN} min break."
        else:
            self._pomodoro_phase = "Study"
            msg = "Break's over — back to studying!"
        self._pomodoro_phase_seconds = 0
        self.pomodoro_banner.setText(f"🍅 {msg}")
        self.pomodoro_banner.show()
        self.statusMessage.emit(msg)

    def _on_stop(self) -> None:
        self._qtimer.stop()
        seconds = self._timer_seconds
        self._timer_running = False
        self._timer_seconds = 0
        self._pomodoro_phase = "Study"
        self._pomodoro_phase_seconds = 0
        self.elapsed_label.setText("00:00")
        self.pomodoro_banner.hide()
        if seconds < 60:
            if seconds > 0:
                self.statusMessage.emit("Timer stopped — less than a minute, not logged.")
            return
        course_id = self.course_combo.currentData() if self.course_combo.count() else None
        if not course_id:
            self.statusMessage.emit("No course selected — nothing logged.")
            return
        minutes = round(seconds / 60)
        now = datetime.now()
        start_dt = now - timedelta(seconds=seconds)
        topic_id = self.topic_combo.currentData() or None
        entry = models.StudyLogEntry(
            course_id=course_id, topic_id=topic_id,
            date=date.today(), start_time=start_dt.time(), end_time=now.time(),
            minutes=minutes, activity=self.activity_combo.currentText(), notes="",
        )
        self.store.add_study_log_entry(entry)
        if topic_id:
            topic = self.store.get_topic(topic_id)
            if topic is not None:
                value, ok = QInputDialog.getInt(
                    self, "Confidence check", f"Confidence on '{topic.title}'? (1-5)",
                    topic.confidence, 1, 5, 1,
                )
                if ok:
                    topic.confidence = value
                    self.store.update_topic(topic)
        self.refresh()
        self.statusMessage.emit(f"Logged {minutes} minute(s) of study.")

    # ======================================================================
    # Cross-page contract: Dashboard's "Start 25-min timer" button
    # ======================================================================
    def start_timer_for(self, course_id: int, topic_id: Optional[int] = None) -> None:
        try:
            self._reset_timer_state()
            idx = self.course_combo.findData(course_id)
            if idx < 0 and self.course_combo.count():
                idx = 0
            if idx >= 0:
                self.course_combo.blockSignals(True)
                self.course_combo.setCurrentIndex(idx)
                self.course_combo.blockSignals(False)
            self._reload_topic_combo(self.course_combo.currentData() if self.course_combo.count() else None)
            if topic_id:
                tidx = self.topic_combo.findData(topic_id)
                if tidx >= 0:
                    self.topic_combo.setCurrentIndex(tidx)
            self.pomodoro_cb.setChecked(True)
            self._on_start()
        except Exception:
            log.exception("StudyLogPage: start_timer_for failed (course_id=%s, topic_id=%s)", course_id, topic_id)
            self.statusMessage.emit("Could not start the timer.")

    # ======================================================================
    # Manual add / edit / delete
    # ======================================================================
    def _on_add_entry(self) -> None:
        if not self._courses:
            self.statusMessage.emit("Add a course first (Courses page).")
            return
        codes = [c.code for c in self._courses]
        default_course_id = self.course_combo.currentData() if self.course_combo.count() else None
        default_idx = 0
        for i, c in enumerate(self._courses):
            if c.course_id == default_course_id:
                default_idx = i
                break
        code, ok = QInputDialog.getItem(self, "Select course", "Course:", codes, default_idx, False)
        if not ok or not code:
            return
        course = next((c for c in self._courses if c.code == code), None)
        if course is None:
            return
        new_entry = models.StudyLogEntry(course_id=course.course_id, date=date.today())
        result = dialogs.edit_row(
            self, f"Add study log entry — {course.code}", table_models.STUDYLOG_COLUMNS, new_entry,
            multiline_attrs={"notes"},
        )
        if result is None:
            return
        self.store.add_study_log_entry(result)
        self.refresh()
        self.statusMessage.emit("Study log entry added.")

    def _on_edit_entry(self) -> None:
        obj = self._row_from_view(self.table, self.model)
        if obj is None:
            self.statusMessage.emit("Select a study log entry first.")
            return
        result = dialogs.edit_row(
            self, "Edit study log entry", table_models.STUDYLOG_COLUMNS, obj,
            multiline_attrs={"notes"},
        )
        if result is None:
            return
        self.store.update_study_log_entry(result)
        self.refresh()
        self.statusMessage.emit("Study log entry updated.")

    def _on_delete_entry(self) -> None:
        obj = self._row_from_view(self.table, self.model)
        if obj is None:
            self.statusMessage.emit("Select a study log entry first.")
            return
        if not widgets.confirm(self, "Delete this study log entry?"):
            return
        self.store.delete_study_log_entry(obj.log_id)
        self.refresh()
        self.statusMessage.emit("Study log entry deleted.")

    def _on_inline_edit(self, obj, attr, value):
        try:
            self.store.update_study_log_entry(obj)
            return True
        except Exception:
            log.exception("StudyLogPage: inline edit failed for log_id=%s", getattr(obj, "log_id", "?"))
            self.statusMessage.emit("Could not save that change.")
            return False

    # ======================================================================
    # Charts / heatmap area
    # ======================================================================
    def _build_charts_area(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(12)

        self._week_chart_view = QChartView()
        self._week_chart_view.setRenderHint(QPainter.Antialiasing)
        self._week_chart_view.setMinimumHeight(220)
        layout.addWidget(self._week_chart_view)

        self._daily_chart_view = QChartView()
        self._daily_chart_view.setRenderHint(QPainter.Antialiasing)
        self._daily_chart_view.setMinimumHeight(220)
        layout.addWidget(self._daily_chart_view)

        heatmap_group = QGroupBox("Term heatmap")
        heatmap_v = QVBoxLayout(heatmap_group)
        heatmap_scroll = QScrollArea()
        heatmap_scroll.setWidgetResizable(True)
        heatmap_scroll.setFixedHeight(150)
        heatmap_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        heatmap_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        heatmap_inner = QWidget()
        self._heatmap_grid = QGridLayout(heatmap_inner)
        self._heatmap_grid.setSpacing(2)
        self._heatmap_grid.setContentsMargins(4, 4, 4, 4)
        self._heatmap_grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        heatmap_scroll.setWidget(heatmap_inner)
        heatmap_v.addWidget(heatmap_scroll)
        layout.addWidget(heatmap_group)

        layout.addStretch(1)
        scroll.setWidget(container)
        return scroll

    def _build_week_course_chart(self, entries: list[models.StudyLogEntry]) -> None:
        chart = QChart()
        chart.setTitle("Hours per course — this week")
        chart.legend().hide()
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        hours_by_course: dict[int, float] = defaultdict(float)
        for e in entries:
            if e.date and monday <= e.date <= sunday:
                hours_by_course[e.course_id] += (e.minutes or 0) / 60.0

        bar_set = QBarSet("Hours")
        categories: list[str] = []
        for c in self._courses:
            bar_set.append(round(hours_by_course.get(c.course_id, 0.0), 2))
            categories.append(c.code or f"#{c.course_id}")
        series = QBarSeries()
        series.append(bar_set)
        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append(categories)
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setTitleText("Hours")
        max_val = max(hours_by_course.values()) if hours_by_course else 0.0
        axis_y.setRange(0, max(1.0, max_val * 1.2))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        self._week_chart_view.setChart(chart)

    def _build_daily_chart(self, entries: list[models.StudyLogEntry]) -> None:
        chart = QChart()
        chart.setTitle("Hours per day — last 30 days")
        chart.legend().hide()
        today = date.today()
        days = [today - timedelta(days=i) for i in range(29, -1, -1)]

        minutes_by_day: dict[date, int] = defaultdict(int)
        for e in entries:
            if e.date:
                minutes_by_day[e.date] += e.minutes or 0

        bar_set = QBarSet("Hours")
        categories: list[str] = []
        values: list[float] = []
        for d in days:
            hrs = round(minutes_by_day.get(d, 0) / 60.0, 2)
            bar_set.append(hrs)
            values.append(hrs)
            categories.append(d.strftime("%m-%d"))
        series = QBarSeries()
        series.append(bar_set)
        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append(categories)
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setTitleText("Hours")
        max_val = max(values) if values else 0.0
        axis_y.setRange(0, max(1.0, max_val * 1.2))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        self._daily_chart_view.setChart(chart)

    def _build_heatmap(self, entries: list[models.StudyLogEntry]) -> None:
        widgets.clear_layout(self._heatmap_grid)

        start = self.store.setting_date("semester_start") \
            or date.fromisoformat(config.SETTINGS_DEFAULTS["semester_start"])
        end = self.store.setting_date("semester_end") \
            or date.fromisoformat(config.SETTINGS_DEFAULTS["semester_end"])
        if start > end:
            start, end = end, start

        minutes_by_day: dict[date, int] = defaultdict(int)
        for e in entries:
            if e.date:
                minutes_by_day[e.date] += e.minutes or 0

        grid_start = start - timedelta(days=start.weekday())  # back to that week's Monday
        total_days = (end - grid_start).days + 1
        for offset in range(total_days):
            d = grid_start + timedelta(days=offset)
            col = offset // 7
            row = d.weekday()
            cell = QFrame()
            cell.setFixedSize(13, 13)
            if d < start or d > end:
                cell.setStyleSheet("background: transparent; border: none;")
            else:
                minutes = minutes_by_day.get(d, 0)
                color = _heatmap_color(minutes)
                cell.setStyleSheet(f"background-color: {color}; border-radius: 3px; border: 1px solid #33415580;")
                cell.setToolTip(f"{d.isoformat()} — {minutes} min")
            self._heatmap_grid.addWidget(cell, row, col)

    def _refresh_charts(self, entries: list[models.StudyLogEntry]) -> None:
        try:
            self._build_week_course_chart(entries)
        except Exception:
            log.exception("StudyLogPage: failed to build the weekly-hours-per-course chart")
        try:
            self._build_daily_chart(entries)
        except Exception:
            log.exception("StudyLogPage: failed to build the daily-hours chart")
        try:
            self._build_heatmap(entries)
        except Exception:
            log.exception("StudyLogPage: failed to build the term heatmap")

    # ======================================================================
    # Table + streak
    # ======================================================================
    def _refresh_table(self, entries: list[models.StudyLogEntry]) -> None:
        rows = sorted(entries, key=lambda e: (e.date or date.min, e.start_time or dtime.min), reverse=True)
        self.model.columns = [table_models.course_column(self._course_map)] + list(table_models.STUDYLOG_COLUMNS)
        self.model.set_rows(rows)
        table_models.apply_delegates(self.table, self.model)

    def _refresh_streak(self, entries: list[models.StudyLogEntry]) -> None:
        current, best = progress.study_streak(entries, date.today())
        self.streak_label.setText(f"{current} day streak, best {best}")

    # ======================================================================
    # refresh()
    # ======================================================================
    def refresh(self) -> None:
        try:
            self._courses = self.store.list_courses()
            self._course_map = {c.course_id: c.code for c in self._courses}
            self._reload_course_combo()
        except Exception:
            log.exception("StudyLogPage: failed to load courses")
            self.statusMessage.emit("Study Log page failed to load courses — see log.")

        try:
            entries = self.store.list_study_log()
        except Exception:
            log.exception("StudyLogPage: failed to load the study log")
            self.statusMessage.emit("Could not load the study log.")
            entries = []

        for fn, label in (
            (lambda: self._refresh_table(entries), "table"),
            (lambda: self._refresh_charts(entries), "charts"),
            (lambda: self._refresh_streak(entries), "streak"),
        ):
            try:
                fn()
            except Exception:
                log.exception("StudyLogPage: failed to refresh %s", label)
                self.statusMessage.emit(f"Could not refresh the {label}.")
