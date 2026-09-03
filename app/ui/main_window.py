"""
The application shell: sidebar navigation, the page stack, the save/lock
banner, the status bar, system-tray notifications, global shortcuts, and the
weekly-summary dialog. Wires the small cross-page signal contract described
in every page module's docstring (openCourse, quickBrainDump,
startTimerRequested, backRequested/show_course, new_note_today,
start_timer_for, showWeeklySummaryRequested) - see app/ui/*_page.py.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QStackedWidget,
    QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget,
)

from app import config
from app.excel_store import ExcelStore
from app.services import scheduler, summary
from app.ui.courses_page import CoursesPage
from app.ui.course_detail_page import CourseDetailPage
from app.ui.dashboard_page import DashboardPage
from app.ui.goals_page import GoalsPage
from app.ui.mock_exam_page import MockExamPage
from app.ui.practice_page import PracticePage
from app.ui.review_page import ReviewPage
from app.ui.roadmap_page import RoadmapPage
from app.ui.settings_page import SettingsPage
from app.ui.study_log_page import StudyLogPage
from app.ui.tracker_page import TrackerPage

logger = logging.getLogger("study_tracker")

# Sidebar order == Ctrl+1..Ctrl+9 order (spec caps the shortcut at 9 keys;
# Goals/Settings are still reachable via the sidebar, just not a Ctrl+N key).
PAGE_ORDER = [
    "Dashboard", "Courses", "Tracker", "Roadmap", "Practice",
    "Mock Exam", "Review", "Study Log", "Goals", "Settings",
]


class MainWindow(QMainWindow):
    def __init__(self, store: ExcelStore):
        super().__init__()
        self.store = store
        self.setWindowTitle(config.APP_NAME)
        self.resize(1280, 800)
        self.setMinimumSize(1200, 760)
        if config.ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(config.ICON_PATH)))

        self._pages: dict[str, QWidget] = {}
        self._build_pages()
        self._build_shell()
        self._wire_signals()
        self._build_shortcuts()
        self._build_tray()

        self.store.dataChanged.connect(self._on_data_changed)
        self.store.saved.connect(self._on_saved)
        self.store.saveError.connect(self._on_save_error)
        self.store.lockWarning.connect(self._on_lock_warning)

        if self.store.created_empty:
            self._show_banner(
                "Empty workbook created - restore the supplied study_tracker.xlsx for your semester data.",
                warning=True,
            )

        self.go_to_page("Dashboard")
        self._update_status()

        self._tray_timer = QTimer(self)
        self._tray_timer.timeout.connect(self._check_notifications)
        self._tray_timer.start(config.TRAY_POLL_MS)
        QTimer.singleShot(500, self._check_notifications)
        QTimer.singleShot(800, self._maybe_show_weekly_summary)

    # ------------------------------------------------------------ build --
    def _build_pages(self) -> None:
        self.dashboard_page = DashboardPage(self.store)
        self.courses_page = CoursesPage(self.store)
        self.course_detail_page = CourseDetailPage(self.store)
        self.tracker_page = TrackerPage(self.store)
        self.roadmap_page = RoadmapPage(self.store)
        self.practice_page = PracticePage(self.store)
        self.mock_exam_page = MockExamPage(self.store)
        self.review_page = ReviewPage(self.store)
        self.study_log_page = StudyLogPage(self.store)
        self.goals_page = GoalsPage(self.store)
        self.settings_page = SettingsPage(self.store)

        self._pages = {
            "Dashboard": self.dashboard_page,
            "Courses": self.courses_page,
            "Tracker": self.tracker_page,
            "Roadmap": self.roadmap_page,
            "Practice": self.practice_page,
            "Mock Exam": self.mock_exam_page,
            "Review": self.review_page,
            "Study Log": self.study_log_page,
            "Goals": self.goals_page,
            "Settings": self.settings_page,
        }

    def _build_shell(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.banner = QLabel("")
        self.banner.setObjectName("Banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        outer.addWidget(self.banner)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        outer.addWidget(body, 1)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(180)
        for name in PAGE_ORDER:
            QListWidgetItem(name, self.sidebar)
        self.sidebar.currentTextChanged.connect(self._on_sidebar_clicked)
        body_layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        for name in PAGE_ORDER:
            self.stack.addWidget(self._pages[name])
        self.stack.addWidget(self.course_detail_page)
        body_layout.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready")

    def _wire_signals(self) -> None:
        for page in list(self._pages.values()) + [self.course_detail_page]:
            if hasattr(page, "statusMessage"):
                page.statusMessage.connect(self.show_status_message)
            if hasattr(page, "navigateTo"):
                page.navigateTo.connect(self.go_to_page)

        self.courses_page.openCourse.connect(self.show_course_detail)
        self.dashboard_page.openCourse.connect(self.show_course_detail)
        self.dashboard_page.quickBrainDump.connect(self.open_quick_brain_dump)
        self.dashboard_page.startTimerRequested.connect(self.start_timer_for)
        self.course_detail_page.backRequested.connect(lambda: self.go_to_page("Courses"))
        self.settings_page.showWeeklySummaryRequested.connect(
            lambda: self._show_weekly_summary_dialog(mark_shown=False)
        )

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+S"), self, activated=lambda: self.store.save(force=True))
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.open_quick_brain_dump)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._reload_from_excel)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self._toggle_timer_shortcut)
        for i, name in enumerate(PAGE_ORDER[:9], start=1):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, activated=lambda n=name: self.go_to_page(n))

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        icon = QIcon(str(config.ICON_PATH)) if config.ICON_PATH.exists() else self.windowIcon()
        if not icon.isNull():
            self.tray.setIcon(icon)
        self.tray.setToolTip(config.APP_NAME)
        menu = QMenu()
        show_action = menu.addAction("Show Study Tracker")
        show_action.triggered.connect(self._restore_from_tray)
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(QApplication.instance().quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._restore_from_tray()
            if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # --------------------------------------------------------- navigation --
    def go_to_page(self, name: str) -> None:
        page = self._pages.get(name)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        for i in range(self.sidebar.count()):
            if self.sidebar.item(i).text() == name:
                self.sidebar.blockSignals(True)
                self.sidebar.setCurrentRow(i)
                self.sidebar.blockSignals(False)
                break
        try:
            page.refresh()
        except Exception:
            logger.exception("refresh() failed for page %s", name)

    def _on_sidebar_clicked(self, name: str) -> None:
        if name:
            self.go_to_page(name)

    def show_course_detail(self, course_id: int) -> None:
        try:
            self.course_detail_page.show_course(course_id)
        except Exception:
            logger.exception("show_course(%s) failed", course_id)
        self.stack.setCurrentWidget(self.course_detail_page)
        self.sidebar.blockSignals(True)
        self.sidebar.setCurrentRow(-1)
        self.sidebar.blockSignals(False)

    def open_quick_brain_dump(self) -> None:
        self.go_to_page("Review")
        try:
            self.review_page.new_note_today()
        except Exception:
            logger.exception("new_note_today failed")

    def start_timer_for(self, course_id: int, topic_id: int) -> None:
        self.go_to_page("Study Log")
        try:
            self.study_log_page.start_timer_for(course_id, topic_id or None)
        except Exception:
            logger.exception("start_timer_for failed")

    def _toggle_timer_shortcut(self) -> None:
        self.go_to_page("Study Log")
        page = self.study_log_page
        for method_name in ("toggle_timer", "start_stop_timer"):
            if hasattr(page, method_name):
                try:
                    getattr(page, method_name)()
                except Exception:
                    logger.exception("Ctrl+T timer toggle failed")
                return

    def _reload_from_excel(self) -> None:
        try:
            self.store.reload()
            self.show_status_message("Reloaded from Excel")
        except Exception:
            logger.exception("reload failed")
            self.show_status_message("Reload failed - see data/app.log")

    # -------------------------------------------------------------- store --
    def _on_data_changed(self, sheet: str) -> None:
        current = self.stack.currentWidget()
        if current is not None and hasattr(current, "refresh"):
            try:
                current.refresh()
            except Exception:
                logger.exception("refresh() failed after dataChanged(%s)", sheet)
        self._update_status()

    def _on_saved(self) -> None:
        self._update_status(saved_now=True)

    def _on_save_error(self, message: str) -> None:
        self.show_status_message(f"Save problem: {message}")

    def _on_lock_warning(self, locked: bool) -> None:
        if locked:
            self._show_banner("Workbook is open in Excel - close it to save. Retrying every 10s; no edits are lost.", warning=True)
        else:
            self._hide_banner()

    def _update_status(self, saved_now: bool = False) -> None:
        try:
            week1 = self.store.setting_date("week1_monday")
            week_txt = f"Week {scheduler.term_week_number(date.today(), week1)}" if week1 else ""
        except Exception:
            week_txt = ""
        prefix = f"Saved {datetime.now().strftime('%H:%M')}" if saved_now else "Ready"
        self.statusBar().showMessage(f"{prefix}" + (f" · {week_txt}" if week_txt else ""))

    def show_status_message(self, message: str) -> None:
        self.statusBar().showMessage(message, 5000)

    def _show_banner(self, text: str, warning: bool = True) -> None:
        self.banner.setText(text)
        self.banner.setObjectName("Banner" if warning else "BannerInfo")
        self.banner.setStyleSheet(self.banner.styleSheet())  # force re-polish
        self.banner.show()

    def _hide_banner(self) -> None:
        if not self.store.created_empty:
            self.banner.hide()

    # ---------------------------------------------------------- tray poll --
    def _check_notifications(self) -> None:
        try:
            today = date.today()
            tomorrow = today + timedelta(days=1)
            messages: list[str] = []
            for p in self.store.list_prelabs():
                if p.lab_type == "None" or p.completed or p.lab_date not in (today, tomorrow):
                    continue
                when = "today" if p.lab_date == today else "tomorrow"
                messages.append(f"Pre-lab due {when}: Lab {p.lab_number} - {p.title}")
            for a in self.store.list_assessments():
                if a.status in ("Submitted", "Graded") or not a.due_date:
                    continue
                days = (a.due_date - today).days
                if days < 0:
                    continue
                if a.type in ("Midterm", "Final") and days <= 3:
                    messages.append(f"Exam in {days}d: {a.title}")
                elif days <= 1:
                    messages.append(f"Due {'today' if days == 0 else 'tomorrow'}: {a.title}")
            if messages and hasattr(self, "tray") and QSystemTrayIcon.isSystemTrayAvailable():
                self.tray.showMessage(
                    config.APP_NAME, "\n".join(messages[:6]),
                    QSystemTrayIcon.MessageIcon.Information, 8000,
                )
        except Exception:
            logger.exception("Notification check failed")

    # ----------------------------------------------------- weekly summary --
    def _maybe_show_weekly_summary(self) -> None:
        try:
            current_week = summary.iso_week_str(date.today())
            if self.store.get_setting("last_summary_week") == current_week:
                return
            self._show_weekly_summary_dialog(mark_shown=True)
        except Exception:
            logger.exception("Weekly summary check failed")

    def _show_weekly_summary_dialog(self, mark_shown: bool) -> None:
        try:
            today = date.today()
            week_start = today - timedelta(days=today.weekday())
            week_end = week_start + timedelta(days=6)
            ctx = summary.WeeklySummaryContext(
                today=today, week_start=week_start, week_end=week_end,
                courses=self.store.list_courses(), assessments=self.store.list_assessments(),
                topics=self.store.list_topics(), prelabs=self.store.list_prelabs(),
                study_log=self.store.list_study_log(), weights=self.store.list_grade_weights(),
                mock_exams=self.store.list_mock_exams(), roadmap_items=self.store.list_roadmap(),
                goals=self.store.list_goals(),
            )
            result = summary.compute_weekly_summary(ctx)
        except Exception:
            logger.exception("Failed to compute weekly summary")
            if mark_shown:
                self.store.set_setting("last_summary_week", summary.iso_week_str(date.today()))
            return

        html = self._render_weekly_summary_html(result, week_start, week_end)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Weekly summary - week of {week_start.strftime('%b %d')}")
        dlg.setMinimumSize(480, 520)
        layout = QVBoxLayout(dlg)
        view = QTextEdit()
        view.setReadOnly(True)
        view.setHtml(html)
        layout.addWidget(view)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        save_btn = QPushButton("Save as note (#weekly)")
        buttons.addButton(save_btn, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(dlg.accept)

        def save_note():
            try:
                from app.models import BrainDumpNote
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                note = BrainDumpNote(
                    date=today, title=f"Weekly summary - {week_start.strftime('%b %d')}",
                    body_html=html, tags=["weekly"], created_at=now, updated_at=now,
                )
                self.store.add_brain_dump_note(note)
                self.show_status_message("Weekly summary saved as a note")
            except Exception:
                logger.exception("Failed to save weekly summary note")

        save_btn.clicked.connect(save_note)
        layout.addWidget(buttons)
        if mark_shown:
            self.store.set_setting("last_summary_week", summary.iso_week_str(today))
        dlg.exec()

    def _render_weekly_summary_html(self, r, week_start: date, week_end: date) -> str:
        def rows(items):
            return "".join(f"<li>{i}</li>" for i in items) or "<li><i>none</i></li>"

        hours_by_course = "".join(f"<li>course {cid}: {h:.1f}h</li>" for cid, h in r.hours_by_course.items())
        goal_txt = f" / goal {r.hours_goal:.0f}h" if r.hours_goal else ""
        return f"""
        <h2>Week of {week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}</h2>
        <p><b>Study hours:</b> {r.hours_total:.1f}h{goal_txt}</p>
        <ul>{hours_by_course}</ul>
        <p><b>Assessments:</b> {r.assessments_submitted} submitted, {r.assessments_graded} graded</p>
        <p><b>Topics mastered this week:</b> {r.topics_mastered_this_week}</p>
        <p><b>Pre-labs:</b> {r.prelabs_on_time} on time, {r.prelabs_late} late</p>
        <p><b>Overdue items:</b></p><ul>{rows(r.overdue_items)}</ul>
        <p><b>Courses below target:</b></p><ul>{rows(r.courses_below_target)}</ul>
        <p><b>Courses failing NAIT pass rule:</b></p><ul>{rows(r.courses_failing_nait)}</ul>
        <p><b>Mock exam results:</b></p><ul>{rows(r.mock_results)}</ul>
        <p><b>Slipped milestones:</b></p><ul>{rows(r.slipped_milestones)}</ul>
        """

    # --------------------------------------------------------------- close --
    def closeEvent(self, event) -> None:
        self._tray_timer.stop()
        if self.store.close():
            event.accept()
            return
        resp = QMessageBox.question(
            self, "Workbook is locked",
            "The workbook could not be saved (it looks like it's open in Excel).\n\n"
            "Close Excel and press Retry, or Discard to quit without saving.",
            QMessageBox.Retry | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if resp == QMessageBox.Retry:
            event.ignore()
            QTimer.singleShot(300, self.close)
        elif resp == QMessageBox.Discard:
            event.accept()
        else:
            event.ignore()
