"""
Dashboard page - the 10-card overview (build spec §5.2).

Every card pulls fresh data from ``ExcelStore`` at refresh() time; nothing is
hardcoded per course/topic/assessment so a new workbook row shows up with no
code change (build spec §1 "Dynamic buttons").
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from datetime import time as dtime
from typing import Optional

from PySide6.QtCharts import (
    QBarCategoryAxis, QChart, QChartView, QHorizontalBarSeries, QValueAxis,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from app import config
from app.excel_store import ExcelStore
from app.models import Course, PreLab, SyllabusFlag, Topic
from app.services import grades, progress, scheduler
from app.ui.widgets import (
    Badge, ChecklistWidget, CountdownCard, clear_layout, colored_progress_bar,
    flag_badge, open_resource, urgency_color,
)

logger = logging.getLogger("study_tracker")

_EXAM_TYPES = ("Midterm", "Final", "Quiz", "Practical Lab Assessment")


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    """A QFrame#Card (see styles.qss) with a bold title label already added."""
    frame = QFrame()
    frame.setObjectName("Card")
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    layout = QVBoxLayout(frame)
    title_lbl = QLabel(title)
    title_lbl.setProperty("role", "title")
    layout.addWidget(title_lbl)
    return frame, layout


class _ClickableCard(QFrame):
    """A QFrame#Card that emits ``clicked`` on left click - used for the
    course-progress cards (§5.2 card 6: 'clicking a course's card emits
    openCourse')."""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _pass_through(*widgets: QWidget) -> None:
    """Let mouse clicks fall through these child widgets to their parent
    (used so labels inside a _ClickableCard don't swallow the click)."""
    for w in widgets:
        w.setAttribute(Qt.WA_TransparentForMouseEvents, True)


class DashboardPage(QWidget):
    statusMessage = Signal(str)
    navigateTo = Signal(str)
    # Cross-page contract (build spec): CoursesPage/DashboardPage -> shell.
    openCourse = Signal(int)
    quickBrainDump = Signal()
    startTimerRequested = Signal(int, int)

    def __init__(self, store: ExcelStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._flags_expanded = False
        self._week1_monday = date(2026, 8, 31)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        self._container = QWidget()
        self._grid = QGridLayout(self._container)
        self._grid.setSpacing(12)
        self._grid.setContentsMargins(12, 12, 12, 12)
        for col in range(3):
            self._grid.setColumnStretch(col, 1)
        scroll.setWidget(self._container)

        self.store.dataChanged.connect(lambda _sheet: self.refresh())

        self.refresh()

    # ------------------------------------------------------------------
    # Error-safe wrapper for button-click / on-edit handlers (shared
    # contract: never let a slot raise).
    # ------------------------------------------------------------------
    def _guard(self, fn, message: str = "Action failed - see log"):
        def wrapped(*args, **kwargs):
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception(message)
                self.statusMessage.emit(message)
        return wrapped

    def _week_num(self, d: Optional[date]) -> Optional[int]:
        if d is None:
            return None
        try:
            return scheduler.term_week_number(d, self._week1_monday)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # refresh() - rebuild every card from the store
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        try:
            self._rebuild()
        except Exception:
            logger.exception("Dashboard failed to refresh")
            self.statusMessage.emit("Dashboard failed to refresh - see log")

    def _rebuild(self) -> None:
        clear_layout(self._grid)
        today = date.today()
        self._week1_monday = self.store.setting_date("week1_monday") or self._week1_monday

        all_courses = self.store.list_courses()
        courses = [c for c in all_courses if c.active]
        course_map = {c.course_id: c for c in all_courses}
        active_ids = {c.course_id for c in courses}

        card1 = self._build_prelab_card(today, course_map, active_ids)
        card2 = self._build_agenda_card(today, course_map, active_ids)
        card3 = self._build_exams_card(today, course_map, active_ids)
        card4 = self._build_deadlines_card(today, course_map, active_ids)
        card5 = self._build_week_card(today, courses)
        card6 = self._build_course_progress_card(today, courses)
        card7 = self._build_focus_card(today, courses, active_ids)
        card8 = self._build_next_action_card(today, course_map, active_ids)
        card9 = self._build_flags_card(course_map)
        card10 = self._build_braindump_card()

        # Card 1 (Tomorrow's Pre-Lab) is the largest: spans 2 cols x 2 rows.
        self._grid.addWidget(card1, 0, 0, 2, 2)
        self._grid.addWidget(card2, 0, 2, 1, 1)
        self._grid.addWidget(card3, 1, 2, 1, 1)
        self._grid.addWidget(card4, 2, 0, 1, 1)
        self._grid.addWidget(card5, 2, 1, 1, 1)
        self._grid.addWidget(card6, 2, 2, 1, 1)
        self._grid.addWidget(card7, 3, 0, 1, 1)
        self._grid.addWidget(card8, 3, 1, 1, 1)
        self._grid.addWidget(card9, 3, 2, 1, 1)
        self._grid.addWidget(card10, 4, 0, 1, 1)
        self._grid.setRowStretch(5, 1)

    # ==================================================================
    # Card 1 - Tomorrow's Pre-Lab
    # ==================================================================
    def _build_prelab_card(self, today: date, course_map: dict[int, Course], active_ids: set[int]) -> QFrame:
        frame, layout = _card("Tomorrow's Pre-Lab")
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        tomorrow = today + timedelta(days=1)
        try:
            prelabs = [
                p for p in self.store.list_prelabs()
                if p.course_id in active_ids and p.lab_type != "None"
            ]
        except Exception:
            logger.exception("Failed to load pre-labs for dashboard")
            prelabs = []

        matches = [
            p for p in prelabs
            if p.lab_date == tomorrow or (p.lab_date == today and not p.completed)
        ]
        matches.sort(key=lambda p: (p.lab_date, p.lab_time or dtime(23, 59)))

        if not matches:
            empty = QLabel("No lab tomorrow \U0001F389")
            empty.setStyleSheet("font-size: 12pt; padding: 10px 0;")
            layout.addWidget(empty)
        else:
            for p in matches:
                layout.addWidget(self._build_prelab_widget(p, course_map.get(p.course_id)))

        upcoming = sorted(
            (p for p in prelabs if p.lab_date and tomorrow < p.lab_date <= today + timedelta(days=7)),
            key=lambda p: p.lab_date,
        )
        if upcoming:
            sep = QLabel("Next 7 days")
            sep.setStyleSheet("font-weight: 600; color: #94a3b8; margin-top: 6px;")
            layout.addWidget(sep)
            for p in upcoming:
                course = course_map.get(p.course_id)
                code = course.code if course else "?"
                wk = self._week_num(p.lab_date)
                wk_s = f"Week {wk} · " if wk else ""
                row = QLabel(
                    f"{wk_s}{code} Lab {p.lab_number}: {p.title} — "
                    f"{p.lab_date.strftime('%a %b %d')}"
                )
                row.setWordWrap(True)
                layout.addWidget(row)

        layout.addStretch(1)
        return frame

    def _build_prelab_widget(self, p: PreLab, course: Optional[Course]) -> QFrame:
        box = QFrame()
        box.setStyleSheet(
            "QFrame { border: 1px solid #334155; border-radius: 8px; padding: 6px; }"
        )
        v = QVBoxLayout(box)

        header = QHBoxLayout()
        code = course.code if course else "?"
        color = course.color_hex if course else config.DEFAULT_COURSE_COLOR
        header.addWidget(Badge(code, color))
        title_lbl = QLabel(f"Lab {p.lab_number}: {p.title}")
        title_lbl.setStyleSheet("font-weight: 700; font-size: 11pt;")
        title_lbl.setWordWrap(True)
        header.addWidget(title_lbl, 1)
        v.addLayout(header)

        when = p.lab_date.strftime("%a %b %d") if p.lab_date else "date TBC"
        time_s = p.lab_time.strftime("%H:%M") if p.lab_time else "time TBC"
        meta = QLabel(f"{when} · {time_s} · {p.room or 'room TBC'}")
        meta.setStyleSheet(f"color: {urgency_color(p.lab_date, p.lab_time)};")
        v.addWidget(meta)

        if p.checklist:
            cl = ChecklistWidget(p.checklist)
            cl.changed.connect(self._guard(
                lambda p=p, cl=cl: self._save_checklist(p, cl),
                "Couldn't save checklist change",
            ))
            v.addWidget(cl)

        complete_box = QCheckBox("Pre-lab complete")
        complete_box.setChecked(p.completed)
        complete_box.toggled.connect(self._guard(
            lambda checked, p=p: self._set_prelab_complete(p, checked),
            "Couldn't update pre-lab completion",
        ))
        v.addWidget(complete_box)

        open_btn = QPushButton("Open lab PDF")
        open_btn.clicked.connect(self._guard(
            lambda p=p: self._open_prelab(p), "Couldn't open lab PDF"
        ))
        v.addWidget(open_btn)
        return box

    def _save_checklist(self, prelab: PreLab, widget: ChecklistWidget) -> None:
        prelab.checklist = list(widget.items)
        self.store.update_prelab(prelab)

    def _set_prelab_complete(self, prelab: PreLab, checked: bool) -> None:
        prelab.completed = bool(checked)
        prelab.completed_on = date.today() if checked else None
        self.store.update_prelab(prelab)

    def _open_prelab(self, prelab: PreLab) -> None:
        if not open_resource(self.store, prelab.link, parent=self):
            self.statusMessage.emit(f"No lab PDF link for Lab {prelab.lab_number}")

    # ==================================================================
    # Card 2 - Today / tomorrow agenda
    # ==================================================================
    def _build_agenda_card(self, today: date, course_map: dict[int, Course], active_ids: set[int]) -> QFrame:
        frame, layout = _card("Today / Tomorrow")
        try:
            schedule = [s for s in self.store.list_schedule() if s.course_id in active_ids]
            holidays = self.store.list_holidays()
            prelabs = [p for p in self.store.list_prelabs() if p.course_id in active_ids]
            assessments = [a for a in self.store.list_assessments() if a.course_id in active_ids]
        except Exception:
            logger.exception("Failed to load agenda data")
            schedule, holidays, prelabs, assessments = [], [], [], []

        for label, d in (("Today", today), ("Tomorrow", today + timedelta(days=1))):
            day_lbl = QLabel(f"{label} · {d.strftime('%a %b %d')}")
            day_lbl.setStyleSheet("font-weight: 600; color: #94a3b8; margin-top: 4px;")
            layout.addWidget(day_lbl)
            try:
                items = scheduler.agenda(d, schedule, holidays, prelabs, assessments)
            except Exception:
                logger.exception("scheduler.agenda failed")
                items = []
            if not items:
                if scheduler.is_holiday(d, holidays):
                    name = next((h.name for h in holidays if h.date == d), "Holiday")
                    layout.addWidget(QLabel(f"{name} — no classes"))
                else:
                    layout.addWidget(QLabel("Nothing scheduled"))
                continue
            for item in items:
                course = course_map.get(item.course_id)
                code = course.code if course else "?"
                color = course.color_hex if course else config.DEFAULT_COURSE_COLOR
                row = QHBoxLayout()
                row.addWidget(Badge(code, color))
                t_s = item.start_time.strftime("%H:%M") if item.start_time else "--:--"
                text = f"{t_s} {item.title}" + (f" · {item.room}" if item.room else "")
                text_lbl = QLabel(text)
                text_lbl.setWordWrap(True)
                row.addWidget(text_lbl, 1)
                if item.flagged:
                    row.addWidget(flag_badge())
                wrap = QWidget()
                wrap.setLayout(row)
                layout.addWidget(wrap)
        layout.addStretch(1)
        return frame

    # ==================================================================
    # Card 3 - Upcoming exams
    # ==================================================================
    def _build_exams_card(self, today: date, course_map: dict[int, Course], active_ids: set[int]) -> QFrame:
        frame, layout = _card("Upcoming Exams")
        try:
            assessments = [a for a in self.store.list_assessments() if a.course_id in active_ids]
            all_topics = {t.topic_id: t for t in self.store.list_topics()}
        except Exception:
            logger.exception("Failed to load exams for dashboard")
            assessments, all_topics = [], {}

        upcoming = []
        for a in assessments:
            if a.type not in _EXAM_TYPES or not a.due_date:
                continue
            days = (a.due_date - today).days
            if 0 <= days <= config.UPCOMING_EXAM_WINDOW_DAYS:
                upcoming.append(a)
        upcoming.sort(key=lambda a: a.due_date)

        if not upcoming:
            layout.addWidget(QLabel("No exams in the next 30 days"))
        for a in upcoming:
            course = course_map.get(a.course_id)
            code = course.code if course else "?"
            title = f"{code} {a.title}"
            wk = self._week_num(a.due_date)
            subtitle = a.due_date.strftime("%a %b %d")
            if wk:
                subtitle += f" · Week {wk}"
            total = len(a.topic_ids)
            mastered = sum(
                1 for tid in a.topic_ids
                if all_topics.get(tid) and all_topics[tid].status == "Mastered"
            )
            coverage = (mastered / total * 100) if total else 0.0
            card = CountdownCard(
                title, a.due_date, subtitle=subtitle, coverage_pct=coverage,
                flagged=a.is_flagged, flag_tooltip=a.notes,
            )
            layout.addWidget(card)
        layout.addStretch(1)
        return frame

    # ==================================================================
    # Card 4 - Deadlines (7 days)
    # ==================================================================
    def _build_deadlines_card(self, today: date, course_map: dict[int, Course], active_ids: set[int]) -> QFrame:
        frame, layout = _card("Deadlines (7 days)")
        try:
            assessments = [a for a in self.store.list_assessments() if a.course_id in active_ids]
        except Exception:
            logger.exception("Failed to load deadlines")
            assessments = []

        deadlines = [
            a for a in assessments
            if a.status not in ("Submitted", "Graded") and a.due_date
            and (a.due_date - today).days <= config.DEADLINE_WINDOW_DAYS
        ]
        deadlines.sort(key=lambda a: (a.due_date, a.due_time or dtime(23, 59)))

        if not deadlines:
            layout.addWidget(QLabel("Nothing due in the next 7 days"))
        for a in deadlines:
            course = course_map.get(a.course_id)
            code = course.code if course else "?"
            color = urgency_color(a.due_date, a.due_time)
            row = QHBoxLayout()
            code_lbl = QLabel(code)
            code_lbl.setStyleSheet("font-weight: 600;")
            row.addWidget(code_lbl)
            title_lbl = QLabel(a.title)
            title_lbl.setWordWrap(True)
            row.addWidget(title_lbl, 1)
            when = a.due_date.strftime("%a %b %d")
            if a.due_time:
                when += f" {a.due_time.strftime('%H:%M')}"
            when_lbl = QLabel(when)
            when_lbl.setStyleSheet(f"color: {color};")
            row.addWidget(when_lbl)
            if a.is_flagged:
                row.addWidget(flag_badge(a.notes))
            combo = QComboBox()
            combo.addItems(config.ASSESSMENT_STATUSES)
            if a.status in config.ASSESSMENT_STATUSES:
                combo.setCurrentText(a.status)
            combo.currentTextChanged.connect(self._guard(
                lambda text, a=a: self._set_assessment_status(a, text),
                "Couldn't update assessment status",
            ))
            row.addWidget(combo)
            wrap = QWidget()
            wrap.setLayout(row)
            layout.addWidget(wrap)
        layout.addStretch(1)
        return frame

    def _set_assessment_status(self, a, status: str) -> None:
        a.status = status
        self.store.update_assessment(a)

    # ==================================================================
    # Card 5 - This week (study hours vs weekly goal)
    # ==================================================================
    def _build_week_card(self, today: date, courses: list[Course]) -> QFrame:
        frame, layout = _card("This Week")
        active_ids = {c.course_id for c in courses}
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        try:
            all_log = self.store.list_study_log()
        except Exception:
            logger.exception("Failed to load study log")
            all_log = []

        week_log = [e for e in all_log if e.course_id in active_ids and e.date and monday <= e.date <= sunday]
        hours_by_course: dict[int, float] = defaultdict(float)
        for e in week_log:
            hours_by_course[e.course_id] += (e.minutes or 0) / 60.0
        total_hours = sum(hours_by_course.values())

        try:
            goals = self.store.list_goals()
        except Exception:
            logger.exception("Failed to load goals")
            goals = []
        weekly_goal = next(
            (g for g in goals if g.scope == "Weekly" and g.metric == "study_hours_per_week"), None
        )

        if weekly_goal and weekly_goal.target_value:
            pct = min(100, total_hours / weekly_goal.target_value * 100)
            goal_lbl = QLabel(f"{total_hours:.1f}h / {weekly_goal.target_value:g}h goal")
            layout.addWidget(goal_lbl)
            color = config.TOPIC_STATUS_COLORS["Mastered"] if pct >= 100 else config.DEFAULT_COURSE_COLOR
            layout.addWidget(colored_progress_bar(pct, color))
        else:
            layout.addWidget(QLabel(f"{total_hours:.1f}h studied this week"))

        if courses:
            chart = QChart()
            chart.legend().hide()
            chart.setBackgroundBrush(QColor("#1e293b"))
            chart.setBackgroundRoundness(0)
            bar_set = None
            try:
                from PySide6.QtCharts import QBarSet
                bar_set = QBarSet("Hours")
            except Exception:
                bar_set = None
            categories = [c.code for c in courses]
            if bar_set is not None:
                for c in courses:
                    bar_set.append(round(hours_by_course.get(c.course_id, 0.0), 2))
                series = QHorizontalBarSeries()
                series.append(bar_set)
                chart.addSeries(series)
                axis_y = QBarCategoryAxis()
                axis_y.append(categories)
                axis_y.setLabelsColor(QColor("#e5e7eb"))
                chart.addAxis(axis_y, Qt.AlignLeft)
                series.attachAxis(axis_y)
                axis_x = QValueAxis()
                axis_x.setLabelsColor(QColor("#e5e7eb"))
                max_val = max([hours_by_course.get(c.course_id, 0.0) for c in courses] or [1.0])
                axis_x.setRange(0, max(1.0, max_val * 1.2))
                chart.addAxis(axis_x, Qt.AlignBottom)
                series.attachAxis(axis_x)
                view = QChartView(chart)
                view.setRenderHint(QPainter.Antialiasing)
                view.setMinimumHeight(max(120, 24 * len(courses)))
                layout.addWidget(view)

        current, best = progress.study_streak(all_log, today)
        streak_lbl = QLabel(f"{current} day streak \U0001F525 (best {best})")
        streak_lbl.setStyleSheet("margin-top: 4px;")
        layout.addWidget(streak_lbl)
        layout.addStretch(1)
        return frame

    # ==================================================================
    # Card 6 - Course progress
    # ==================================================================
    def _build_course_progress_card(self, today: date, courses: list[Course]) -> QFrame:
        frame, layout = _card("Course Progress")
        if not courses:
            layout.addWidget(QLabel("No active courses"))
            layout.addStretch(1)
            return frame

        for course in courses:
            try:
                assessments = self.store.list_assessments(course.course_id)
                weights = self.store.list_grade_weights(course.course_id)
                prelabs = self.store.list_prelabs(course.course_id)
                topics = self.store.list_topics(course.course_id)
            except Exception:
                logger.exception("Failed to load course data for %s", course.code)
                continue

            overall = grades.compute_grade(assessments, weights)
            theory = grades.compute_grade(assessments, weights, component="Theory")
            lab = grades.compute_grade(assessments, weights, component="Lab")
            topic_pct = progress.topic_progress_pct(topics)
            done, _due, total_term = grades.labs_completed_counts(prelabs, today)

            card = _ClickableCard()
            card.clicked.connect(self._guard(
                lambda cid=course.course_id: self.openCourse.emit(cid),
                "Couldn't open course",
            ))
            v = QVBoxLayout(card)

            header = QHBoxLayout()
            badge = Badge(course.code, course.color_hex)
            header.addWidget(badge)
            name_lbl = QLabel(course.name)
            name_lbl.setStyleSheet("font-weight: 700;")
            name_lbl.setWordWrap(True)
            header.addWidget(name_lbl, 1)
            header_w = QWidget()
            header_w.setLayout(header)
            v.addWidget(header_w)

            current = overall.current
            target = course.target_grade
            if current is None:
                grade_color = "#94a3b8"
                grade_text = "Current grade: —"
            else:
                if target is not None:
                    grade_color = (
                        config.TOPIC_STATUS_COLORS["Mastered"] if current >= target
                        else config.URGENCY_OVERDUE
                    )
                else:
                    grade_color = "#e5e7eb"
                grade_text = f"Current grade: {current:.1f}%"
                if target is not None:
                    grade_text += f" (target {target:g}%)"
            grade_lbl = QLabel(grade_text)
            grade_lbl.setStyleSheet(f"color: {grade_color}; font-weight: 600;")
            v.addWidget(grade_lbl)

            theory_s = f"{theory.current:.0f}%" if theory.current is not None else "—"
            lab_s = f"{lab.current:.0f}%" if lab.current is not None else "—"
            comp_lbl = QLabel(f"Theory {theory_s} · Lab {lab_s}")
            v.addWidget(comp_lbl)

            v.addWidget(colored_progress_bar(topic_pct, course.color_hex, fmt="Topics %p%"))

            min_pct = course.min_lab_completion_pct
            min_s = f"{min_pct:g}%" if min_pct is not None else "—"
            labs_lbl = QLabel(f"Labs completed {done}/{total_term} (min {min_s})")
            v.addWidget(labs_lbl)

            _pass_through(badge, name_lbl, grade_lbl, comp_lbl, labs_lbl)
            layout.addWidget(card)

        layout.addStretch(1)
        return frame

    # ==================================================================
    # Card 7 - Focus next
    # ==================================================================
    def _build_focus_card(self, today: date, courses: list[Course], active_ids: set[int]) -> QFrame:
        frame, layout = _card("Focus Next")
        course_map = {c.course_id: c for c in courses}
        try:
            topics = [t for t in self.store.list_topics() if t.course_id in active_ids]
            assessments = [a for a in self.store.list_assessments() if a.course_id in active_ids]
        except Exception:
            logger.exception("Failed to load focus data")
            topics, assessments = [], []

        ranked = progress.rank_topics_by_focus(topics, assessments, today, limit=8)
        if not ranked:
            layout.addWidget(QLabel("Nothing urgent — nice work"))
        for t in ranked:
            course = course_map.get(t.course_id)
            code = course.code if course else "?"
            row = QHBoxLayout()
            lbl = QLabel(f"{code} · {t.section} {t.title}")
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)
            btn = QPushButton("Open")
            btn.clicked.connect(self._guard(lambda t=t: self._open_topic(t), "Couldn't open topic link"))
            row.addWidget(btn)
            wrap = QWidget()
            wrap.setLayout(row)
            layout.addWidget(wrap)
        layout.addStretch(1)
        return frame

    def _open_topic(self, topic: Topic) -> None:
        if not open_resource(self.store, topic.link, topic.page, parent=self):
            self.statusMessage.emit(f"No link for topic '{topic.title}'")

    # ==================================================================
    # Card 8 - Next best action
    # ==================================================================
    def _build_next_action_card(self, today: date, course_map: dict[int, Course], active_ids: set[int]) -> QFrame:
        frame, layout = _card("Next Best Action")
        try:
            message, course_id, topic_id = self._compute_next_action(today, course_map, active_ids)
        except Exception:
            logger.exception("Failed to compute next best action")
            message, course_id, topic_id = "Couldn't compute a suggestion right now.", 0, 0

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("font-size: 11pt;")
        layout.addWidget(msg_lbl)

        btn = QPushButton("Start 25-min timer")
        btn.clicked.connect(self._guard(
            lambda cid=course_id, tid=topic_id: self.startTimerRequested.emit(cid, tid),
            "Couldn't start timer",
        ))
        layout.addWidget(btn)
        layout.addStretch(1)
        return frame

    def _compute_next_action(self, today: date, course_map: dict[int, Course], active_ids: set[int]):
        assessments = [a for a in self.store.list_assessments() if a.course_id in active_ids]
        prelabs = [
            p for p in self.store.list_prelabs()
            if p.course_id in active_ids and p.lab_type != "None"
        ]
        topics = [t for t in self.store.list_topics() if t.course_id in active_ids]

        def code_of(cid: int) -> str:
            c = course_map.get(cid)
            return c.code if c else "?"

        # (a) overdue assessment or pre-lab
        overdue_a = [
            a for a in assessments
            if a.due_date and a.due_date < today and a.status not in ("Submitted", "Graded")
        ]
        overdue_p = [
            p for p in prelabs
            if p.prelab_due and p.prelab_due < today and not p.completed
        ]
        if overdue_a or overdue_p:
            best_a = min(overdue_a, key=lambda a: a.due_date) if overdue_a else None
            best_p = min(overdue_p, key=lambda p: p.prelab_due) if overdue_p else None
            if best_a is not None and (best_p is None or best_a.due_date <= best_p.prelab_due):
                msg = (
                    f"Overdue: {code_of(best_a.course_id)} {best_a.title} was due "
                    f"{best_a.due_date.strftime('%b %d')}."
                )
                tid = best_a.topic_ids[0] if best_a.topic_ids else 0
                return msg, best_a.course_id, tid
            msg = (
                f"Overdue: {code_of(best_p.course_id)} Lab {best_p.lab_number} pre-lab "
                f"was due {best_p.prelab_due.strftime('%b %d')}."
            )
            return msg, best_p.course_id, 0

        # (b) pre-lab due today/tomorrow, not completed
        tomorrow = today + timedelta(days=1)
        due_soon = [p for p in prelabs if p.lab_date in (today, tomorrow) and not p.completed]
        if due_soon:
            p = min(due_soon, key=lambda p: p.lab_date)
            when = "today" if p.lab_date == today else "tomorrow"
            msg = f"Pre-lab due {when}: {code_of(p.course_id)} Lab {p.lab_number} — {p.title}."
            return msg, p.course_id, 0

        # (c) exam within 7 days with coverage < 70%
        topic_map = {t.topic_id: t for t in topics}
        candidates = []
        for a in assessments:
            if a.type not in _EXAM_TYPES or not a.due_date:
                continue
            days = (a.due_date - today).days
            if not (0 <= days <= 7):
                continue
            total = len(a.topic_ids)
            mastered = sum(
                1 for tid in a.topic_ids if topic_map.get(tid) and topic_map[tid].status == "Mastered"
            )
            coverage = (mastered / total * 100) if total else 0.0
            if coverage < 70:
                candidates.append((days, coverage, a))
        if candidates:
            candidates.sort(key=lambda c: c[0])
            days, coverage, a = candidates[0]
            day_word = "day" if days == 1 else "days"
            msg = (
                f"{code_of(a.course_id)} {a.title} is in {days} {day_word} and coverage "
                f"is only {coverage:.0f}% — study now."
            )
            tid = 0
            if a.topic_ids:
                weak = [tid for tid in a.topic_ids if topic_map.get(tid)]
                if weak:
                    tid = min(weak, key=lambda tid: topic_map[tid].confidence)
            return msg, a.course_id, tid

        # (d) highest focus-score topic
        ranked = progress.rank_topics_by_focus(topics, assessments, today, limit=1)
        if ranked:
            t = ranked[0]
            return f"Focus on {t.title}.", t.course_id, t.topic_id

        # (e) fallback: lowest-confidence topic
        if topics:
            t = min(topics, key=lambda t: (t.confidence, t.title))
            return f"On track — 25 min on {t.title}.", t.course_id, t.topic_id

        return "All caught up — nothing urgent right now.", 0, 0

    # ==================================================================
    # Card 9 - Syllabus flags
    # ==================================================================
    def _build_flags_card(self, course_map: dict[int, Course]) -> QFrame:
        frame, layout = _card("Syllabus Flags")
        try:
            unresolved = self.store.list_syllabus_flags(unresolved_only=True)
        except Exception:
            logger.exception("Failed to load syllabus flags")
            unresolved = []

        toggle_btn = QPushButton(f"⚠ {len(unresolved)} unresolved")
        toggle_btn.setFlat(True)
        toggle_btn.setStyleSheet(f"text-align: left; color: {config.FLAG_BADGE_COLOR}; font-weight: 700;")
        layout.addWidget(toggle_btn)

        flag_list = QListWidget()
        flag_list.setVisible(self._flags_expanded)
        flag_list.setFrameShape(QFrame.NoFrame)
        for f in unresolved:
            course = course_map.get(f.course_id)
            code = course.code if course else "?"
            item = QListWidgetItem()
            row = QWidget()
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(2, 2, 2, 2)
            lbl = QLabel(f"{code} — {f.item}: {f.issue}")
            lbl.setWordWrap(True)
            lbl.setToolTip(f.source)
            row_l.addWidget(lbl, 1)
            resolved_box = QCheckBox("Resolved")
            resolved_box.toggled.connect(self._guard(
                lambda checked, f=f: self._set_flag_resolved(f, checked),
                "Couldn't update syllabus flag",
            ))
            row_l.addWidget(resolved_box)
            item.setSizeHint(row.sizeHint())
            flag_list.addItem(item)
            flag_list.setItemWidget(item, row)
        layout.addWidget(flag_list)

        def do_toggle():
            self._flags_expanded = not self._flags_expanded
            flag_list.setVisible(self._flags_expanded)

        toggle_btn.clicked.connect(self._guard(do_toggle, "Couldn't toggle flag list"))
        layout.addStretch(1)
        return frame

    def _set_flag_resolved(self, flag: SyllabusFlag, checked: bool) -> None:
        flag.resolved = bool(checked)
        self.store.update_syllabus_flag(flag)

    # ==================================================================
    # Card 10 - Quick brain dump
    # ==================================================================
    def _build_braindump_card(self) -> QFrame:
        frame, layout = _card("Quick Brain Dump")
        lbl = QLabel("Capture a thought before you lose it.")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)
        btn = QPushButton("+ Quick brain dump")
        btn.clicked.connect(self._guard(lambda: self.quickBrainDump.emit(), "Couldn't open brain dump"))
        layout.addWidget(btn)
        layout.addStretch(1)
        return frame
