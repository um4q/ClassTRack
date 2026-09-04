"""
Course Detail page (build spec section 5.3). Starts with no course selected;
call ``show_course(course_id)`` to load one (also used to switch courses
without recreating the widget). Header = code/name/instructor(mailto)/
office/sections/current grade/target/projected/Theory & Lab component
pass-checks/lab completion/unresolved-flags count. Below the header: dynamic
material buttons grouped by kind. Below that: a 9-tab QTabWidget covering
Syllabus/Topics, Labs, Assessments, Grades, Roadmap, Study, Attendance,
Practice and Flags for this one course.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from PySide6.QtCore import QDate, QDateTime, QModelIndex, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QTabWidget, QTableView, QTreeView, QVBoxLayout, QWidget,
)
from PySide6.QtCharts import (
    QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView,
    QDateTimeAxis, QLineSeries, QValueAxis,
)

from app import config, models
from app.excel_store import ExcelStore
from app.services import grades, progress, scheduler
from app.ui import dialogs, table_models, widgets
from app.ui.table_models import ColumnSpec

log = logging.getLogger("study_tracker")

# --------------------------------------------------------------------------
# Local constants
# --------------------------------------------------------------------------
TAB_TOPICS = 0
TAB_LABS = 1
TAB_ASSESSMENTS = 2
TAB_GRADES = 3
TAB_ROADMAP = 4
TAB_STUDY = 5
TAB_ATTENDANCE = 6
TAB_PRACTICE = 7
TAB_FLAGS = 8

TOPIC_ID_ROLE = Qt.UserRole + 1001

# Small local column spec for the "Add topic" dialog - not the predefined
# TOPIC_COLUMNS (that one uses combo boxes for confidence/priority, whose
# choices RowEditDialog would write back as *strings*; we want real ints).
_TOPIC_ADD_COLUMNS: list[ColumnSpec] = [
    ColumnSpec("unit", "Unit", editable=True),
    ColumnSpec("section", "Section", editable=True),
    ColumnSpec("title", "Title", editable=True),
    ColumnSpec("status", "Status", kind="combo", editable=True, choices=config.TOPIC_STATUSES),
    ColumnSpec("confidence", "Confidence (1-5)", kind="int", editable=True),
    ColumnSpec("priority", "Priority (1-5)", kind="int", editable=True),
    ColumnSpec("link", "Link", editable=True),
    ColumnSpec("page", "PDF page", kind="int", editable=True),
    ColumnSpec("estimated_hours", "Est. hours", kind="float", editable=True),
    ColumnSpec("notes", "Notes", editable=True),
]


def _check_mark(ok: bool) -> str:
    return "✔" if ok else "✘"


class CourseDetailPage(QWidget):
    """The full detail view for one course. Empty until ``show_course`` is
    called; ``refresh()`` re-pulls everything from the store for the
    currently loaded course_id."""

    statusMessage = Signal(str)
    navigateTo = Signal(str)
    backRequested = Signal()

    def __init__(self, store: ExcelStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.course_id: Optional[int] = None
        self.course: Optional[models.Course] = None
        self._pending_unit: str = ""

        # cached, course-scoped rows refreshed by _refresh_header()
        self._course_assessments: list[models.Assessment] = []
        self._course_weights: list[models.GradeWeight] = []
        self._course_prelabs: list[models.PreLab] = []
        self._overall_grade_result: Optional[grades.GradeResult] = None
        self._nait_result: Optional[grades.NaitPassResult] = None

        # timer state (Study tab)
        self._timer_seconds = 0
        self._timer_running = False
        self._timer_qtimer = QTimer(self)
        self._timer_qtimer.setInterval(1000)
        self._timer_qtimer.timeout.connect(self._on_timer_tick)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self._build_header(outer)
        self._build_materials_section(outer)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_topics_tab(), "Syllabus / Topics")
        self._tabs.addTab(self._build_labs_tab(), "Labs")
        self._tabs.addTab(self._build_assessments_tab(), "Assessments")
        grades_scroll = QScrollArea()
        grades_scroll.setWidgetResizable(True)
        grades_scroll.setWidget(self._build_grades_tab())
        self._tabs.addTab(grades_scroll, "Grades")
        self._tabs.addTab(self._build_roadmap_tab(), "Roadmap")
        self._tabs.addTab(self._build_study_tab(), "Study")
        self._tabs.addTab(self._build_attendance_tab(), "Attendance")
        self._tabs.addTab(self._build_practice_tab(), "Practice")
        self._tabs.addTab(self._build_flags_tab(), "Flags")
        outer.addWidget(self._tabs, 1)

        self.refresh()

    # ======================================================================
    # Cross-page contract
    # ======================================================================
    def show_course(self, course_id: int) -> None:
        self.course_id = course_id
        self.refresh()

    def _on_back_clicked(self) -> None:
        try:
            self.backRequested.emit()
        except Exception:
            log.exception("CourseDetailPage: back navigation failed")
            self.statusMessage.emit("Could not go back.")

    def _defer_refresh(self) -> None:
        QTimer.singleShot(0, self.refresh)

    # ======================================================================
    # Header
    # ======================================================================
    def _build_header(self, outer: QVBoxLayout) -> None:
        header_frame = QFrame()
        header_frame.setObjectName("CourseHeader")
        header_frame.setFrameShape(QFrame.StyledPanel)
        header_layout = QVBoxLayout(header_frame)

        top_row = QHBoxLayout()
        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(self._on_back_clicked)
        top_row.addWidget(back_btn)
        self._header_title = QLabel("No course selected")
        self._header_title.setStyleSheet("font-size: 18pt; font-weight: 700;")
        top_row.addWidget(self._header_title, 1)
        self._header_flags_btn = QPushButton("")
        self._header_flags_btn.setFlat(True)
        self._header_flags_btn.setCursor(Qt.PointingHandCursor)
        self._header_flags_btn.setStyleSheet(f"color: {config.FLAG_BADGE_COLOR}; font-weight: 700;")
        self._header_flags_btn.clicked.connect(lambda: self._tabs.setCurrentIndex(TAB_FLAGS))
        top_row.addWidget(self._header_flags_btn)
        header_layout.addLayout(top_row)

        info_row = QHBoxLayout()
        self._header_instructor = QLabel("—")
        self._header_instructor.setOpenExternalLinks(False)
        self._header_instructor.linkActivated.connect(self._on_instructor_link)
        self._header_office = QLabel("")
        self._header_sections = QLabel("")
        for lbl in (self._header_instructor, self._header_office, self._header_sections):
            lbl.setStyleSheet("color: palette(text); margin-right: 16px;")
            info_row.addWidget(lbl)
        info_row.addStretch(1)
        header_layout.addLayout(info_row)

        grade_row = QHBoxLayout()
        self._header_grade = QLabel("—")
        self._header_grade.setStyleSheet("font-size: 20pt; font-weight: 700; margin-right: 12px;")
        self._header_target = QLabel("")
        self._header_projected = QLabel("")
        self._header_theory = QLabel("")
        self._header_lab = QLabel("")
        self._header_labcompletion = QLabel("")
        for lbl in (self._header_target, self._header_projected, self._header_theory,
                    self._header_lab, self._header_labcompletion):
            lbl.setStyleSheet("margin-right: 14px;")
        grade_row.addWidget(self._header_grade)
        grade_row.addWidget(self._header_target)
        grade_row.addWidget(self._header_projected)
        grade_row.addWidget(self._header_theory)
        grade_row.addWidget(self._header_lab)
        grade_row.addWidget(self._header_labcompletion)
        grade_row.addStretch(1)
        header_layout.addLayout(grade_row)

        outer.addWidget(header_frame)

    def _on_instructor_link(self, link: str) -> None:
        try:
            QDesktopServices.openUrl(QUrl(link))
        except Exception:
            log.exception("CourseDetailPage: could not open instructor link %s", link)
            self.statusMessage.emit("Could not open the email link.")

    def _refresh_header(self) -> None:
        c = self.course
        self._header_title.setText(f"{c.code or '?'} — {c.name or '?'}")
        if c.email:
            self._header_instructor.setText(
                f'{c.instructor or "Instructor TBC"} · <a href="mailto:{c.email}">{c.email}</a>'
            )
        else:
            self._header_instructor.setText(c.instructor or "Instructor TBC")
        self._header_office.setText(f"Office: {c.office or '—'}")
        self._header_sections.setText(f"Sections: {c.lecture_section or '?'} / {c.lab_section or '?'}")

        assessments = self.store.list_assessments(self.course_id)
        weights = self.store.list_grade_weights(self.course_id)
        prelabs = self.store.list_prelabs(self.course_id)
        self._course_assessments = assessments
        self._course_weights = weights
        self._course_prelabs = prelabs

        overall = grades.compute_grade(assessments, weights)
        self._overall_grade_result = overall
        self._header_grade.setText(f"{overall.current:.1f}%" if overall.current is not None else "—")
        self._header_target.setText(
            f"Target: {c.target_grade:.1f}%" if c.target_grade is not None else "Target: —"
        )
        self._header_projected.setText(
            f"Projected: {overall.projected:.1f}%" if overall.projected is not None else "Projected: —"
        )

        today = date.today()
        nait = grades.nait_pass_check(c, assessments, weights, prelabs, today)
        self._nait_result = nait
        theory_extra = f" ({nait.theory.current:.0f}%)" if nait.theory.current is not None else ""
        lab_extra = f" ({nait.lab.current:.0f}%)" if nait.lab.current is not None else ""
        self._header_theory.setText(f"Theory {_check_mark(nait.theory_pass)}{theory_extra}")
        self._header_lab.setText(f"Lab {_check_mark(nait.lab_pass)}{lab_extra}")
        min_pct = c.min_lab_completion_pct if c.min_lab_completion_pct is not None else 0.0
        self._header_labcompletion.setText(
            f"Labs: {nait.labs_done}/{nait.labs_due} to date (need {min_pct:g}%)"
        )

        unresolved = len(self.store.list_syllabus_flags(self.course_id, unresolved_only=True))
        self._header_flags_btn.setText(
            f"⚠ {unresolved} unresolved flag(s)" if unresolved else "No unresolved flags"
        )

    # ======================================================================
    # Materials
    # ======================================================================
    def _build_materials_section(self, outer: QVBoxLayout) -> None:
        materials_frame = QFrame()
        materials_frame.setFrameShape(QFrame.StyledPanel)
        materials_outer = QVBoxLayout(materials_frame)

        toolbar = QHBoxLayout()
        title = QLabel("Materials")
        title.setStyleSheet("font-weight: 700;")
        toolbar.addWidget(title)
        toolbar.addStretch(1)
        add_btn = QPushButton("+ Add material")
        add_btn.clicked.connect(self._on_add_material)
        toolbar.addWidget(add_btn)
        materials_outer.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(230)
        content = QWidget()
        self._materials_layout = QHBoxLayout(content)
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        scroll.setWidget(content)
        materials_outer.addWidget(scroll)

        outer.addWidget(materials_frame)

    def _refresh_materials(self) -> None:
        widgets.clear_layout(self._materials_layout)
        materials = self.store.list_materials(self.course_id)
        color = (self.course.color_hex if self.course else None) or config.DEFAULT_COURSE_COLOR
        for group_name, kinds in config.MATERIAL_KIND_GROUPS.items():
            group_materials = [m for m in materials if m.kind in kinds]
            box = QGroupBox(f"{group_name} ({len(group_materials)})")
            box_layout = QVBoxLayout(box)
            items = [
                (m.label or m.kind, color, (lambda mat=m: self._on_open_material(mat)))
                for m in group_materials
            ]
            grid = widgets.make_button_grid(items, columns=1)
            box_layout.addWidget(grid)
            box_layout.addStretch(1)
            self._materials_layout.addWidget(box, 1)

    def _on_open_material(self, material: models.Material) -> None:
        try:
            ok = widgets.open_resource(self.store, material.path, None, self)
            if not ok:
                self.statusMessage.emit(f"Could not open '{material.label}'.")
        except Exception:
            log.exception("CourseDetailPage: open material failed for material_id=%s", material.material_id)
            self.statusMessage.emit("Could not open that material.")

    def _on_add_material(self) -> None:
        try:
            if self.course_id is None:
                self.statusMessage.emit("Open a course first.")
                return
            result = dialogs.edit_row(
                self, "Add material", table_models.MATERIAL_COLUMNS,
                models.Material(course_id=self.course_id, kind="Other"),
                exclude_attrs={"material_id", "course_id"},
            )
            if result is None:
                return
            result.course_id = self.course_id
            self.store.add_material(result)
            self.refresh()
            self.statusMessage.emit(f"Added material '{result.label}'.")
        except Exception:
            log.exception("CourseDetailPage: add material failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not add the material.")

    # ======================================================================
    # Tab 1: Syllabus / Topics
    # ======================================================================
    def _build_topics_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        row1 = QHBoxLayout()
        add_topic_btn = QPushButton("+ Add topic")
        add_topic_btn.clicked.connect(self._on_add_topic)
        add_unit_btn = QPushButton("+ Add unit")
        add_unit_btn.clicked.connect(self._on_add_unit)
        paste_btn = QPushButton("Paste-import")
        paste_btn.clicked.connect(self._on_paste_import)
        row1.addWidget(add_topic_btn)
        row1.addWidget(add_unit_btn)
        row1.addWidget(paste_btn)
        row1.addStretch(1)
        row1.addWidget(QLabel("Filter status:"))
        self._topics_filter_combo = QComboBox()
        self._topics_filter_combo.addItem("All statuses")
        self._topics_filter_combo.addItems(config.TOPIC_STATUSES)
        self._topics_filter_combo.currentIndexChanged.connect(lambda _i: self._refresh_topics_tab())
        row1.addWidget(self._topics_filter_combo)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Set status for selected:"))
        self._topics_bulk_status_combo = QComboBox()
        self._topics_bulk_status_combo.addItems(config.TOPIC_STATUSES)
        row2.addWidget(self._topics_bulk_status_combo)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._on_bulk_set_status)
        row2.addWidget(apply_btn)
        reviewed_btn = QPushButton("Mark reviewed today")
        reviewed_btn.clicked.connect(self._on_mark_reviewed)
        row2.addWidget(reviewed_btn)
        row2.addStretch(1)
        layout.addLayout(row2)

        self._topics_tree = QTreeView()
        self._topics_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._topics_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._topics_tree.setAlternatingRowColors(True)
        self._topics_tree.setUniformRowHeights(False)
        self._topics_model = QStandardItemModel()
        self._topics_model.setHorizontalHeaderLabels(
            ["Unit / Section", "Title", "Status", "Conf.", "Priority", "Last reviewed", "Est. hrs", "Open"]
        )
        self._topics_tree.setModel(self._topics_model)
        layout.addWidget(self._topics_tree, 1)
        return w

    def _refresh_topics_tab(self) -> None:
        topics = self.store.list_topics(self.course_id)
        status_filter = self._topics_filter_combo.currentText()
        if status_filter and status_filter != "All statuses":
            topics = [t for t in topics if t.status == status_filter]
        topics_by_id = {t.topic_id: t for t in topics}

        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(
            ["Unit / Section", "Title", "Status", "Conf.", "Priority", "Last reviewed", "Est. hrs", "Open"]
        )
        seen_units: list[str] = []
        grouped: dict[str, list[models.Topic]] = {}
        for t in topics:
            u = t.unit or "(No unit)"
            if u not in grouped:
                grouped[u] = []
                seen_units.append(u)
            grouped[u].append(t)

        for u in seen_units:
            unit_item = QStandardItem(u)
            unit_item.setEditable(False)
            font = unit_item.font()
            font.setBold(True)
            unit_item.setFont(font)
            model.appendRow(unit_item)
            for t in grouped[u]:
                unit_item.appendRow(self._topic_row_items(t))

        self._topics_model = model
        self._topics_tree.setModel(model)
        self._topics_tree.setColumnWidth(0, 90)
        self._topics_tree.setColumnWidth(1, 280)

        for row in range(model.rowCount()):
            unit_item = model.item(row, 0)
            self._topics_tree.setFirstColumnSpanned(row, QModelIndex(), True)
            self._topics_tree.expand(model.indexFromItem(unit_item))
            for child_row in range(unit_item.rowCount()):
                sec_item = unit_item.child(child_row, 0)
                topic_id = sec_item.data(TOPIC_ID_ROLE)
                t = topics_by_id.get(topic_id)
                if t is None:
                    continue
                status_index = model.indexFromItem(unit_item.child(child_row, 2))
                combo = QComboBox()
                combo.addItems(config.TOPIC_STATUSES)
                combo.setCurrentText(t.status)
                combo.setStyleSheet(
                    f"QComboBox {{ background-color: {config.TOPIC_STATUS_COLORS.get(t.status, '#666')}; "
                    f"color: white; }}"
                )
                combo.currentTextChanged.connect(
                    lambda new_status, tid=topic_id: self._on_topic_status_changed(tid, new_status)
                )
                self._topics_tree.setIndexWidget(status_index, combo)

                open_index = model.indexFromItem(unit_item.child(child_row, 7))
                open_btn = QPushButton("Open")
                open_btn.setMaximumWidth(56)
                open_btn.clicked.connect(lambda checked=False, tid=topic_id: self._on_open_topic(tid))
                self._topics_tree.setIndexWidget(open_index, open_btn)

    def _topic_row_items(self, t: models.Topic) -> list[QStandardItem]:
        sec_item = QStandardItem(t.section or "")
        sec_item.setEditable(False)
        sec_item.setData(t.topic_id, TOPIC_ID_ROLE)
        title_item = QStandardItem(t.title or "")
        title_item.setEditable(False)
        if t.notes:
            title_item.setToolTip(t.notes)
        status_item = QStandardItem("")
        status_item.setEditable(False)
        conf_item = QStandardItem(str(t.confidence))
        conf_item.setEditable(False)
        pri_item = QStandardItem(str(t.priority))
        pri_item.setEditable(False)
        lr_item = QStandardItem(t.last_reviewed.strftime("%Y-%m-%d") if t.last_reviewed else "")
        lr_item.setEditable(False)
        hrs_item = QStandardItem(f"{t.estimated_hours:g}" if t.estimated_hours is not None else "")
        hrs_item.setEditable(False)
        open_item = QStandardItem("")
        open_item.setEditable(False)
        return [sec_item, title_item, status_item, conf_item, pri_item, lr_item, hrs_item, open_item]

    def _selected_topic_ids(self) -> list[int]:
        sel = self._topics_tree.selectionModel()
        if sel is None:
            return []
        ids: set[int] = set()
        for idx in sel.selectedIndexes():
            if not idx.parent().isValid():
                continue  # unit header row - not a topic
            item = self._topics_model.itemFromIndex(idx.siblingAtColumn(0))
            if item is None:
                continue
            tid = item.data(TOPIC_ID_ROLE)
            if tid:
                ids.add(tid)
        return list(ids)

    def _selected_unit_group(self) -> Optional[str]:
        sel = self._topics_tree.selectionModel()
        if sel is None:
            return None
        for idx in sel.selectedIndexes():
            if not idx.parent().isValid() and idx.column() == 0:
                return idx.data()
        return None

    def _on_open_topic(self, topic_id: int) -> None:
        try:
            topic = self.store.get_topic(topic_id)
            if topic is None:
                self.statusMessage.emit("That topic no longer exists.")
                return
            ok = widgets.open_resource(self.store, topic.link, topic.page, self)
            if not ok:
                self.statusMessage.emit(f"Could not open '{topic.title}'.")
        except Exception:
            log.exception("CourseDetailPage: open topic failed for topic_id=%s", topic_id)
            self.statusMessage.emit("Could not open that topic.")

    def _on_topic_status_changed(self, topic_id: int, new_status: str) -> None:
        try:
            topic = self.store.get_topic(topic_id)
            if topic is None:
                return
            progress.topic_status_changed(topic, new_status, date.today())
            self.store.update_topic(topic)
            self._defer_refresh()
        except Exception:
            log.exception("CourseDetailPage: topic status change failed for topic_id=%s", topic_id)
            self.statusMessage.emit("Could not update the topic status.")

    def _on_add_topic(self) -> None:
        try:
            if self.course_id is None:
                self.statusMessage.emit("Open a course first.")
                return
            default = models.Topic(course_id=self.course_id, unit=self._pending_unit, status="Not Started")
            result = dialogs.edit_row(
                self, "Add topic", _TOPIC_ADD_COLUMNS, default,
                exclude_attrs={"topic_id", "course_id", "last_reviewed", "next_review"},
            )
            if result is None:
                return
            result.course_id = self.course_id
            self.store.add_topic(result)
            self._refresh_topics_tab()
            self.statusMessage.emit(f"Added topic '{result.title}'.")
        except Exception:
            log.exception("CourseDetailPage: add topic failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not add the topic.")

    def _on_add_unit(self) -> None:
        try:
            text, ok = QInputDialog.getText(self, "Add unit", "Unit name (used for topics you add next):")
            if not ok or not text.strip():
                return
            self._pending_unit = text.strip()
            self.statusMessage.emit(f"New topics will default to unit '{self._pending_unit}'.")
        except Exception:
            log.exception("CourseDetailPage: add unit failed")
            self.statusMessage.emit("Could not set the unit.")

    def _on_bulk_set_status(self) -> None:
        try:
            ids = self._selected_topic_ids()
            if not ids:
                self.statusMessage.emit("Select one or more topics first.")
                return
            new_status = self._topics_bulk_status_combo.currentText()
            today = date.today()
            for tid in ids:
                t = self.store.get_topic(tid)
                if t is None:
                    continue
                progress.topic_status_changed(t, new_status, today)
                self.store.update_topic(t)
            self._refresh_topics_tab()
            self.statusMessage.emit(f"Set {len(ids)} topic(s) to {new_status}.")
        except Exception:
            log.exception("CourseDetailPage: bulk status update failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not update the selected topics.")

    def _on_mark_reviewed(self) -> None:
        try:
            ids = self._selected_topic_ids()
            if not ids:
                self.statusMessage.emit("Select one or more topics first.")
                return
            today = date.today()
            for tid in ids:
                t = self.store.get_topic(tid)
                if t is None:
                    continue
                progress.topic_mark_reviewed(t, today)
                self.store.update_topic(t)
            self._refresh_topics_tab()
            self.statusMessage.emit(f"Marked {len(ids)} topic(s) reviewed today.")
        except Exception:
            log.exception("CourseDetailPage: mark reviewed failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not mark the selected topics reviewed.")

    def _on_paste_import(self) -> None:
        try:
            if self.course_id is None:
                self.statusMessage.emit("Open a course first.")
                return
            selected_unit = self._selected_unit_group()
            dlg = QDialog(self)
            dlg.setWindowTitle("Paste-import topics")
            dlg.setMinimumSize(480, 360)
            layout = QVBoxLayout(dlg)
            layout.addWidget(QLabel("One topic per line, e.g. \"1.2 Feedback control loop components\":"))
            text_edit = QPlainTextEdit()
            layout.addWidget(text_edit, 1)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dlg.accept)
            buttons.rejected.connect(dlg.reject)
            layout.addWidget(buttons)
            if dlg.exec() != QDialog.Accepted:
                return
            raw_text = text_edit.toPlainText()

            unit_value = selected_unit
            if not unit_value:
                unit_value, ok = QInputDialog.getText(self, "Unit", "Unit for these topics:")
                if not ok:
                    return

            count = 0
            for line in raw_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                section = parts[0] if parts else ""
                title = parts[1] if len(parts) > 1 else ""
                self.store.add_topic(models.Topic(
                    course_id=self.course_id, unit=unit_value or "", section=section,
                    title=title, status="Not Started",
                ))
                count += 1
            self._refresh_topics_tab()
            self.statusMessage.emit(f"Imported {count} topic(s).")
        except Exception:
            log.exception("CourseDetailPage: paste-import topics failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not import topics.")

    # ======================================================================
    # Tab 2: Labs
    # ======================================================================
    def _build_labs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Add lab")
        add_btn.clicked.connect(self._on_add_prelab)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(QLabel("(right-click a row for Edit / Rename / Open / Delete)"))
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self._labs_model = table_models.DataclassTableModel(table_models.PRELAB_COLUMNS)
        self._labs_model.on_edit = self._on_prelab_edit
        self._labs_table = QTableView()
        self._labs_table.setModel(self._labs_model)
        table_models.apply_delegates(self._labs_table, self._labs_model)
        self._labs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._labs_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._labs_table.customContextMenuRequested.connect(self._on_labs_context_menu)
        self._labs_table.doubleClicked.connect(self._on_edit_prelab_row)
        layout.addWidget(self._labs_table, 1)
        return w

    def _refresh_labs_tab(self) -> None:
        self._labs_model.set_rows(self._course_prelabs)

    def _on_prelab_edit(self, obj, attr, value):
        try:
            self.store.update_prelab(obj)
            self._defer_refresh()
            return True
        except Exception:
            log.exception("CourseDetailPage: prelab inline edit failed for prelab_id=%s", getattr(obj, "prelab_id", "?"))
            self.statusMessage.emit("Could not save the lab change.")
            return False

    def _on_add_prelab(self) -> None:
        try:
            if self.course_id is None:
                self.statusMessage.emit("Open a course first.")
                return
            result = dialogs.edit_row(
                self, "Add lab", table_models.PRELAB_COLUMNS,
                models.PreLab(course_id=self.course_id, lab_type="Common"),
                exclude_attrs={"prelab_id", "course_id"},
            )
            if result is None:
                return
            result.course_id = self.course_id
            self.store.add_prelab(result)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: add lab failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not add the lab.")

    def _on_edit_prelab_row(self, index: QModelIndex) -> None:
        try:
            obj = self._labs_model.row_object(index.row())
            result = dialogs.edit_row(
                self, f"Edit lab {obj.lab_number}", table_models.PRELAB_COLUMNS, obj,
                exclude_attrs={"prelab_id", "course_id"},
            )
            if result is None:
                return
            result.prelab_id = obj.prelab_id
            result.course_id = self.course_id
            self.store.update_prelab(result)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: edit lab failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not save the lab.")

    def _on_labs_context_menu(self, pos) -> None:
        try:
            index = self._labs_table.indexAt(pos)
            if not index.isValid():
                return
            obj = self._labs_model.row_object(index.row())
            menu = QMenu(self._labs_table)
            edit_action = menu.addAction("Edit")
            rename_action = menu.addAction("Rename to assigned lab") if obj.lab_type == "Rotational" else None
            open_action = menu.addAction("Open")
            delete_action = menu.addAction("Delete")
            chosen = menu.exec(self._labs_table.viewport().mapToGlobal(pos))
            if chosen is None:
                return
            if chosen is edit_action:
                self._on_edit_prelab_row(index)
            elif rename_action is not None and chosen is rename_action:
                self._on_rename_rotational_lab(obj)
            elif chosen is open_action:
                ok = widgets.open_resource(self.store, obj.link, None, self)
                if not ok:
                    self.statusMessage.emit(f"Could not open lab {obj.lab_number}.")
            elif chosen is delete_action:
                if widgets.confirm(self, f"Delete lab {obj.lab_number} — {obj.title}?"):
                    self.store.delete_prelab(obj.prelab_id)
                    self.refresh()
        except Exception:
            log.exception("CourseDetailPage: labs context menu failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not complete that lab action.")

    def _on_rename_rotational_lab(self, obj: models.PreLab) -> None:
        try:
            materials = [m for m in self.store.list_materials(self.course_id) if m.kind == "Lab Procedure"]
            if not materials:
                self.statusMessage.emit("No 'Lab Procedure' materials found for this course.")
                return
            labels = [m.label for m in materials]
            choice, ok = QInputDialog.getItem(self, "Rename to assigned lab", "Assigned lab procedure:", labels, 0, False)
            if not ok or not choice:
                return
            material = next((m for m in materials if m.label == choice), None)
            if material is None:
                return
            obj.title = material.label
            obj.link = material.path
            self.store.update_prelab(obj)
            self.refresh()
            self.statusMessage.emit(f"Lab {obj.lab_number} renamed to '{material.label}'.")
        except Exception:
            log.exception("CourseDetailPage: rename rotational lab failed for prelab_id=%s", obj.prelab_id)
            self.statusMessage.emit("Could not rename the lab.")

    # ======================================================================
    # Tab 3: Assessments
    # ======================================================================
    def _build_assessments_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Add assessment")
        add_btn.clicked.connect(self._on_add_assessment)
        grade_btn = QPushButton("Enter grade")
        grade_btn.clicked.connect(self._on_enter_grade)
        del_btn = QPushButton("Delete selected")
        del_btn.clicked.connect(self._on_delete_assessment)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(grade_btn)
        toolbar.addWidget(del_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self._assessments_model = table_models.DataclassTableModel(table_models.ASSESSMENT_COLUMNS)
        self._assessments_model.on_edit = self._on_assessment_edit
        self._assessments_table = QTableView()
        self._assessments_table.setModel(self._assessments_model)
        table_models.apply_delegates(self._assessments_table, self._assessments_model)
        self._assessments_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._assessments_table.doubleClicked.connect(self._on_edit_assessment_row)
        layout.addWidget(self._assessments_table, 1)
        return w

    def _refresh_assessments_tab(self) -> None:
        self._assessments_model.set_rows(self._course_assessments)

    def _on_assessment_edit(self, obj, attr, value):
        try:
            self.store.update_assessment(obj)
            self._defer_refresh()
            return True
        except Exception:
            log.exception("CourseDetailPage: assessment inline edit failed for assessment_id=%s", getattr(obj, "assessment_id", "?"))
            self.statusMessage.emit("Could not save the assessment change.")
            return False

    def _on_add_assessment(self) -> None:
        try:
            if self.course_id is None:
                self.statusMessage.emit("Open a course first.")
                return
            result = dialogs.edit_row(
                self, "Add assessment", table_models.ASSESSMENT_COLUMNS,
                models.Assessment(course_id=self.course_id),
                exclude_attrs={"assessment_id", "course_id"}, multiline_attrs={"notes"},
            )
            if result is None:
                return
            result.course_id = self.course_id
            self.store.add_assessment(result)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: add assessment failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not add the assessment.")

    def _on_edit_assessment_row(self, index: QModelIndex) -> None:
        try:
            obj = self._assessments_model.row_object(index.row())
            result = dialogs.edit_row(
                self, f"Edit assessment — {obj.title}", table_models.ASSESSMENT_COLUMNS, obj,
                exclude_attrs={"assessment_id", "course_id"}, multiline_attrs={"notes"},
            )
            if result is None:
                return
            result.assessment_id = obj.assessment_id
            result.course_id = self.course_id
            self.store.update_assessment(result)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: edit assessment failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not save the assessment.")

    def _on_delete_assessment(self) -> None:
        try:
            obj = self._selected_row_object(self._assessments_table, self._assessments_model)
            if obj is None:
                self.statusMessage.emit("Select an assessment first.")
                return
            if not widgets.confirm(self, f"Delete assessment '{obj.title}'?"):
                return
            self.store.delete_assessment(obj.assessment_id)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: delete assessment failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not delete the assessment.")

    def _on_enter_grade(self) -> None:
        try:
            obj = self._selected_row_object(self._assessments_table, self._assessments_model)
            if obj is None:
                self.statusMessage.emit("Select an assessment first.")
                return
            max_score = obj.max_score if obj.max_score else 100.0
            score, ok = QInputDialog.getDouble(
                self, "Enter grade", f"Score for '{obj.title}' (out of {max_score:g}):",
                obj.score if obj.score is not None else 0.0, 0.0, max(max_score * 2, 1000.0), 2,
            )
            if not ok:
                return
            obj.score = score
            obj.status = "Graded"
            self.store.update_assessment(obj)
            self.refresh()
            self.statusMessage.emit(f"Recorded {score:g}/{max_score:g} for '{obj.title}'.")
        except Exception:
            log.exception("CourseDetailPage: enter grade failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not record the grade.")

    # ======================================================================
    # Tab 4: Grades
    # ======================================================================
    def _build_grades_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)

        weights_box = QGroupBox("Grade weights")
        weights_layout = QVBoxLayout(weights_box)
        wt_toolbar = QHBoxLayout()
        add_w = QPushButton("+ Add weight")
        add_w.clicked.connect(self._on_add_weight)
        del_w = QPushButton("Delete selected")
        del_w.clicked.connect(self._on_delete_weight)
        wt_toolbar.addWidget(add_w)
        wt_toolbar.addWidget(del_w)
        wt_toolbar.addStretch(1)
        weights_layout.addLayout(wt_toolbar)
        self._weights_model = table_models.DataclassTableModel(table_models.GRADEWEIGHT_COLUMNS)
        self._weights_model.on_edit = self._on_weight_edit
        self._weights_table = QTableView()
        self._weights_table.setModel(self._weights_model)
        table_models.apply_delegates(self._weights_table, self._weights_model)
        self._weights_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._weights_table.doubleClicked.connect(self._on_edit_weight_row)
        self._weights_table.setMaximumHeight(170)
        weights_layout.addWidget(self._weights_table)
        outer.addWidget(weights_box)

        avg_box = QGroupBox("Category averages")
        self._category_avg_layout = QGridLayout(avg_box)
        outer.addWidget(avg_box)

        summary_box = QGroupBox("Grade summary")
        summary_layout = QHBoxLayout(summary_box)
        self._grade_overall_label = QLabel("Overall current: —")
        self._grade_overall_label.setStyleSheet("font-weight: 700; font-size: 12pt; margin-right: 16px;")
        self._grade_projected_label = QLabel("Projected final: —")
        self._grade_theory_label = QLabel("Theory: —")
        self._grade_lab_label = QLabel("Lab: —")
        for lbl in (self._grade_overall_label, self._grade_projected_label,
                    self._grade_theory_label, self._grade_lab_label):
            summary_layout.addWidget(lbl)
        summary_layout.addStretch(1)
        outer.addWidget(summary_box)

        nait_box = QGroupBox("NAIT pass check")
        nait_layout = QVBoxLayout(nait_box)
        self._nait_theory_label = QLabel("")
        self._nait_lab_label = QLabel("")
        self._nait_labs_label = QLabel("")
        self._nait_capped_label = QLabel("")
        self._nait_capped_label.setStyleSheet(f"color: {config.URGENCY_OVERDUE}; font-weight: 700;")
        for lbl in (self._nait_theory_label, self._nait_lab_label, self._nait_labs_label, self._nait_capped_label):
            nait_layout.addWidget(lbl)
        outer.addWidget(nait_box)

        whatif_box = QGroupBox("What-if")
        whatif_layout = QFormLayout(whatif_box)
        self._whatif_target_spin = QDoubleSpinBox()
        self._whatif_target_spin.setRange(0, 100)
        self._whatif_target_spin.setValue(80)
        self._whatif_target_spin.valueChanged.connect(self._on_whatif_target_changed)
        self._whatif_label = QLabel("—")
        whatif_layout.addRow("Target grade %:", self._whatif_target_spin)
        whatif_layout.addRow("Needed average on remaining:", self._whatif_label)

        self._whatif_item_combo = QComboBox()
        self._whatif_item_combo.currentIndexChanged.connect(lambda _i: self._update_whatif_item_label())
        self._whatif_assumed_spin = QDoubleSpinBox()
        self._whatif_assumed_spin.setRange(0, 100)
        self._whatif_assumed_spin.setValue(70)
        self._whatif_assumed_spin.valueChanged.connect(lambda _v: self._update_whatif_item_label())
        self._whatif_item_label = QLabel("—")
        whatif_layout.addRow("Single item:", self._whatif_item_combo)
        whatif_layout.addRow("Assumed avg on the rest %:", self._whatif_assumed_spin)
        whatif_layout.addRow("Needed on this item:", self._whatif_item_label)
        outer.addWidget(whatif_box)

        trend_box = QGroupBox("Grade trend")
        trend_layout = QVBoxLayout(trend_box)
        self._trend_chart_view = QChartView()
        self._trend_chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._trend_chart_view.setMinimumHeight(220)
        trend_layout.addWidget(self._trend_chart_view)
        outer.addWidget(trend_box)

        outer.addStretch(1)
        return w

    def _refresh_grades_tab(self) -> None:
        assessments = self._course_assessments
        weights = self._course_weights
        self._weights_model.set_rows(weights)

        widgets.clear_layout(self._category_avg_layout)
        header_row = ["Category", "Component", "Weight %", "Earned / Max", "Pct"]
        for col, text in enumerate(header_row):
            lbl = QLabel(text)
            lbl.setStyleSheet("font-weight: 700;")
            self._category_avg_layout.addWidget(lbl, 0, col)
        for row_idx, wgt in enumerate(weights, start=1):
            cat_items = [a for a in assessments if a.category == wgt.category]
            graded = [a for a in cat_items if a.score is not None and a.max_score]
            earned = sum(a.score for a in graded)
            maxv = sum(a.max_score for a in graded)
            pct = (earned / maxv * 100) if maxv else None
            cat_label = ("⚠ " if wgt.is_placeholder else "") + (wgt.category or "—")
            self._category_avg_layout.addWidget(QLabel(cat_label), row_idx, 0)
            self._category_avg_layout.addWidget(QLabel(wgt.component), row_idx, 1)
            self._category_avg_layout.addWidget(QLabel(f"{wgt.weight_pct:g}%"), row_idx, 2)
            self._category_avg_layout.addWidget(QLabel(f"{earned:g}/{maxv:g}" if maxv else "—"), row_idx, 3)
            self._category_avg_layout.addWidget(
                QLabel(f"{pct:.1f}%" if pct is not None else "not graded yet"), row_idx, 4
            )

        theory_gr = grades.compute_grade(assessments, weights, component="Theory")
        lab_gr = grades.compute_grade(assessments, weights, component="Lab")
        overall_gr = self._overall_grade_result
        self._grade_theory_label.setText(
            f"Theory: {theory_gr.current:.1f}%" if theory_gr.current is not None else "Theory: —"
        )
        self._grade_lab_label.setText(
            f"Lab: {lab_gr.current:.1f}%" if lab_gr.current is not None else "Lab: —"
        )
        self._grade_overall_label.setText(
            f"Overall current: {overall_gr.current:.1f}%" if overall_gr and overall_gr.current is not None else "Overall current: —"
        )
        self._grade_projected_label.setText(
            f"Projected final: {overall_gr.projected:.1f}%" if overall_gr and overall_gr.projected is not None else "Projected final: —"
        )

        nait = self._nait_result
        self._nait_theory_label.setText(
            f"Theory ≥ 50%: {_check_mark(nait.theory_pass)}"
            + (f" ({nait.theory.current:.1f}%)" if nait.theory.current is not None else "")
        )
        self._nait_lab_label.setText(
            f"Lab ≥ 50%: {_check_mark(nait.lab_pass)}"
            + (f" ({nait.lab.current:.1f}%)" if nait.lab.current is not None else "")
        )
        min_pct = self.course.min_lab_completion_pct if self.course.min_lab_completion_pct is not None else 0.0
        self._nait_labs_label.setText(
            f"Labs completed {nait.labs_done}/{nait.labs_due} to date "
            f"({nait.labs_total_term} total this term) — need {min_pct:g}%: {_check_mark(nait.lab_completion_pass)}"
        )
        if not nait.overall_pass and nait.capped_grade is not None:
            self._nait_capped_label.setText(f"⚠ Course grade would be capped at {nait.capped_grade:.1f}%")
            self._nait_capped_label.setVisible(True)
        else:
            self._nait_capped_label.setVisible(False)

        if self._whatif_target_spin.value() == 0 and self.course.target_grade:
            self._whatif_target_spin.blockSignals(True)
            self._whatif_target_spin.setValue(self.course.target_grade)
            self._whatif_target_spin.blockSignals(False)
        self._update_whatif_label()

        ungraded = [a for a in assessments if a.score is None]
        current_item = self._whatif_item_combo.currentData()
        self._whatif_item_combo.blockSignals(True)
        self._whatif_item_combo.clear()
        for a in ungraded:
            self._whatif_item_combo.addItem(f"{a.type}: {a.title}", a.assessment_id)
        idx = self._whatif_item_combo.findData(current_item)
        self._whatif_item_combo.setCurrentIndex(idx if idx >= 0 else (0 if ungraded else -1))
        self._whatif_item_combo.blockSignals(False)
        self._update_whatif_item_label()

        self._build_trend_chart(assessments, weights, self.course.target_grade)

    @staticmethod
    def _format_whatif(wr: grades.WhatIfResult) -> str:
        if wr.achieved:
            return "Target already achieved."
        if wr.impossible:
            return "Impossible — exceeds 100%."
        if wr.needed_avg is None:
            return "No remaining weight to earn."
        return f"Need {wr.needed_avg:.1f}% average on the remaining items."

    def _on_whatif_target_changed(self, _value: float) -> None:
        self._update_whatif_label()
        self._update_whatif_item_label()

    def _update_whatif_label(self) -> None:
        try:
            if self._overall_grade_result is None:
                self._whatif_label.setText("—")
                return
            target = self._whatif_target_spin.value()
            wr = grades.what_if(self._overall_grade_result, target)
            self._whatif_label.setText(self._format_whatif(wr))
        except Exception:
            log.exception("CourseDetailPage: what-if update failed")
            self._whatif_label.setText("—")

    def _assessment_item_weight(self, assessment: models.Assessment) -> float:
        weight = next((w for w in self._course_weights if w.category == assessment.category), None)
        if weight is None:
            return assessment.weight_override or 0.0
        cat_items = [a for a in self._course_assessments if a.category == assessment.category]
        drop_n = max(0, weight.drop_lowest)
        survivors = max(1, len(cat_items) - drop_n) if drop_n else len(cat_items)
        return grades.item_weight(assessment, weight.weight_pct, survivors)

    def _update_whatif_item_label(self) -> None:
        try:
            aid = self._whatif_item_combo.currentData()
            if not aid or self._overall_grade_result is None:
                self._whatif_item_label.setText("—")
                return
            assessment = next((a for a in self._course_assessments if a.assessment_id == aid), None)
            if assessment is None:
                self._whatif_item_label.setText("—")
                return
            item_w = self._assessment_item_weight(assessment)
            if item_w <= 0:
                self._whatif_item_label.setText("This item has no weight yet.")
                return
            target = self._whatif_target_spin.value()
            assumed = self._whatif_assumed_spin.value()
            wr = grades.what_if_single_item(self._overall_grade_result, target, item_w, assumed)
            self._whatif_item_label.setText(self._format_whatif(wr))
        except Exception:
            log.exception("CourseDetailPage: single-item what-if update failed")
            self._whatif_item_label.setText("—")

    def _build_trend_chart(self, assessments, weights, target: Optional[float]) -> None:
        chart = QChart()
        chart.legend().hide()
        trend = grades.grade_trend(assessments, weights)
        if not trend:
            chart.setTitle("Grade trend (no graded items yet)")
            self._trend_chart_view.setChart(chart)
            return
        chart.setTitle("Grade trend")

        series = QLineSeries()
        series.setName("Current grade")
        for d, pct in trend:
            qdt = QDateTime(QDate(d.year, d.month, d.day))
            series.append(float(qdt.toMSecsSinceEpoch()), pct)
        chart.addSeries(series)

        axis_x = QDateTimeAxis()
        axis_x.setFormat("MMM d")
        axis_x.setTitleText("Date")
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setRange(0, 100)
        axis_y.setTitleText("Grade %")
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        if target is not None:
            target_series = QLineSeries()
            target_series.setName("Target")
            pen = QPen(QColor(config.URGENCY_SOON))
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidth(2)
            target_series.setPen(pen)
            first_d, last_d = trend[0][0], trend[-1][0]
            for d in (first_d, last_d):
                qdt = QDateTime(QDate(d.year, d.month, d.day))
                target_series.append(float(qdt.toMSecsSinceEpoch()), target)
            chart.addSeries(target_series)
            target_series.attachAxis(axis_x)
            target_series.attachAxis(axis_y)

        self._trend_chart_view.setChart(chart)

    def _on_weight_edit(self, obj, attr, value):
        try:
            self.store.update_grade_weight(obj)
            self._defer_refresh()
            return True
        except Exception:
            log.exception("CourseDetailPage: weight inline edit failed for weight_id=%s", getattr(obj, "weight_id", "?"))
            self.statusMessage.emit("Could not save the grade weight.")
            return False

    def _on_add_weight(self) -> None:
        try:
            if self.course_id is None:
                self.statusMessage.emit("Open a course first.")
                return
            result = dialogs.edit_row(
                self, "Add grade weight", table_models.GRADEWEIGHT_COLUMNS,
                models.GradeWeight(course_id=self.course_id),
                exclude_attrs={"weight_id", "course_id"}, multiline_attrs={"notes"},
            )
            if result is None:
                return
            result.course_id = self.course_id
            self.store.add_grade_weight(result)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: add grade weight failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not add the grade weight.")

    def _on_edit_weight_row(self, index: QModelIndex) -> None:
        try:
            obj = self._weights_model.row_object(index.row())
            result = dialogs.edit_row(
                self, f"Edit weight — {obj.category}", table_models.GRADEWEIGHT_COLUMNS, obj,
                exclude_attrs={"weight_id", "course_id"}, multiline_attrs={"notes"},
            )
            if result is None:
                return
            result.weight_id = obj.weight_id
            result.course_id = self.course_id
            self.store.update_grade_weight(result)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: edit grade weight failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not save the grade weight.")

    def _on_delete_weight(self) -> None:
        try:
            obj = self._selected_row_object(self._weights_table, self._weights_model)
            if obj is None:
                self.statusMessage.emit("Select a grade weight first.")
                return
            if not widgets.confirm(self, f"Delete grade weight '{obj.category}'?"):
                return
            self.store.delete_grade_weight(obj.weight_id)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: delete grade weight failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not delete the grade weight.")

    # ======================================================================
    # Tab 5: Roadmap
    # ======================================================================
    def _build_roadmap_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Add milestone")
        add_btn.clicked.connect(self._on_add_roadmap)
        gen_btn = QPushButton("Generate roadmap")
        gen_btn.clicked.connect(self._on_generate_roadmap)
        del_btn = QPushButton("Delete selected")
        del_btn.clicked.connect(self._on_delete_roadmap)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(gen_btn)
        toolbar.addWidget(del_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self._roadmap_model = table_models.DataclassTableModel(table_models.ROADMAP_COLUMNS)
        self._roadmap_model.on_edit = self._on_roadmap_edit
        self._roadmap_table = QTableView()
        self._roadmap_table.setModel(self._roadmap_model)
        table_models.apply_delegates(self._roadmap_table, self._roadmap_model)
        self._roadmap_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._roadmap_table.doubleClicked.connect(self._on_edit_roadmap_row)
        layout.addWidget(self._roadmap_table, 1)
        return w

    def _refresh_roadmap_tab(self) -> None:
        items = sorted(self.store.list_roadmap(self.course_id), key=lambda r: r.sort_order)
        self._roadmap_model.set_rows(items)

    def _on_roadmap_edit(self, obj, attr, value):
        try:
            self.store.update_roadmap_item(obj)
            self._defer_refresh()
            return True
        except Exception:
            log.exception("CourseDetailPage: roadmap inline edit failed for roadmap_id=%s", getattr(obj, "roadmap_id", "?"))
            self.statusMessage.emit("Could not save the roadmap change.")
            return False

    def _on_add_roadmap(self) -> None:
        try:
            if self.course_id is None:
                self.statusMessage.emit("Open a course first.")
                return
            result = dialogs.edit_row(
                self, "Add milestone", table_models.ROADMAP_COLUMNS,
                models.RoadmapItem(course_id=self.course_id, status="Planned"),
                exclude_attrs={"roadmap_id", "course_id"}, multiline_attrs={"notes"},
            )
            if result is None:
                return
            result.course_id = self.course_id
            self.store.add_roadmap_item(result)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: add roadmap milestone failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not add the milestone.")

    def _on_edit_roadmap_row(self, index: QModelIndex) -> None:
        try:
            obj = self._roadmap_model.row_object(index.row())
            result = dialogs.edit_row(
                self, f"Edit milestone — {obj.milestone}", table_models.ROADMAP_COLUMNS, obj,
                exclude_attrs={"roadmap_id", "course_id"}, multiline_attrs={"notes"},
            )
            if result is None:
                return
            result.roadmap_id = obj.roadmap_id
            result.course_id = self.course_id
            self.store.update_roadmap_item(result)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: edit roadmap milestone failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not save the milestone.")

    def _on_delete_roadmap(self) -> None:
        try:
            obj = self._selected_row_object(self._roadmap_table, self._roadmap_model)
            if obj is None:
                self.statusMessage.emit("Select a milestone first.")
                return
            if not widgets.confirm(self, f"Delete milestone '{obj.milestone}'?"):
                return
            self.store.delete_roadmap_item(obj.roadmap_id)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: delete roadmap milestone failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not delete the milestone.")

    def _on_generate_roadmap(self) -> None:
        try:
            if self.course_id is None:
                self.statusMessage.emit("Open a course first.")
                return
            week1 = self.store.setting_date("week1_monday")
            semester_end = self.store.setting_date("semester_end")
            if week1 is None or semester_end is None:
                self.statusMessage.emit("Set week1_monday and semester_end in Settings first.")
                return
            self.store.delete_auto_roadmap(self.course_id)
            topics = self.store.list_topics(self.course_id)
            items = scheduler.generate_roadmap(self.course_id, topics, week1, date.today(), semester_end)
            for it in items:
                self.store.add_roadmap_item(it)
            self.refresh()
            self.statusMessage.emit(f"Generated {len(items)} roadmap milestone(s).")
        except Exception:
            log.exception("CourseDetailPage: generate roadmap failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not generate the roadmap.")

    # ======================================================================
    # Tab 6: Study
    # ======================================================================
    def _build_study_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        timer_box = QGroupBox("Timer")
        timer_layout = QHBoxLayout(timer_box)
        timer_layout.addWidget(QLabel("Topic:"))
        self._study_topic_combo = QComboBox()
        self._study_topic_combo.setMinimumWidth(220)
        timer_layout.addWidget(self._study_topic_combo, 1)
        timer_layout.addWidget(QLabel("Activity:"))
        self._study_activity_combo = QComboBox()
        self._study_activity_combo.addItems(config.STUDYLOG_ACTIVITIES)
        timer_layout.addWidget(self._study_activity_combo)
        self._study_elapsed_label = QLabel("00:00")
        self._study_elapsed_label.setStyleSheet("font-weight: 700; font-size: 14pt; margin: 0 10px;")
        timer_layout.addWidget(self._study_elapsed_label)
        start_btn = QPushButton("Start")
        start_btn.clicked.connect(self._on_timer_start)
        pause_btn = QPushButton("Pause")
        pause_btn.clicked.connect(self._on_timer_pause)
        stop_btn = QPushButton("Stop")
        stop_btn.clicked.connect(self._on_timer_stop)
        timer_layout.addWidget(start_btn)
        timer_layout.addWidget(pause_btn)
        timer_layout.addWidget(stop_btn)
        layout.addWidget(timer_box)

        self._studylog_model = table_models.DataclassTableModel(table_models.STUDYLOG_COLUMNS)
        self._studylog_model.on_edit = self._on_studylog_edit
        self._studylog_table = QTableView()
        self._studylog_table.setModel(self._studylog_model)
        table_models.apply_delegates(self._studylog_table, self._studylog_model)
        self._studylog_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self._studylog_table, 1)

        self._study_chart_view = QChartView()
        self._study_chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._study_chart_view.setMinimumHeight(200)
        layout.addWidget(self._study_chart_view)
        return w

    def _refresh_study_tab(self) -> None:
        current_topic = self._study_topic_combo.currentData()
        self._study_topic_combo.blockSignals(True)
        self._study_topic_combo.clear()
        self._study_topic_combo.addItem("(no topic)", 0)
        for t in self.store.list_topics(self.course_id):
            label = f"{t.section} {t.title}".strip() or f"Topic {t.topic_id}"
            self._study_topic_combo.addItem(label, t.topic_id)
        idx = self._study_topic_combo.findData(current_topic) if current_topic else 0
        self._study_topic_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._study_topic_combo.blockSignals(False)

        entries = sorted(
            self.store.list_study_log(self.course_id),
            key=lambda e: (e.date is None, -(e.date.toordinal() if e.date is not None else 0)),
        )
        self._studylog_model.set_rows(entries)
        self._build_study_chart(entries)

    def _on_studylog_edit(self, obj, attr, value):
        try:
            self.store.update_study_log_entry(obj)
            self._defer_refresh()
            return True
        except Exception:
            log.exception("CourseDetailPage: study log inline edit failed for log_id=%s", getattr(obj, "log_id", "?"))
            self.statusMessage.emit("Could not save the study log change.")
            return False

    def _build_study_chart(self, entries: list[models.StudyLogEntry]) -> None:
        chart = QChart()
        chart.setTitle("Hours per week")
        chart.legend().hide()
        week1 = self.store.setting_date("week1_monday")
        semester_end = self.store.setting_date("semester_end")
        max_week = 16
        if week1 and semester_end:
            max_week = max(1, scheduler.term_week_number(semester_end, week1))
        hours_by_week = {wk: 0.0 for wk in range(1, max_week + 1)}
        if week1:
            for e in entries:
                if e.date:
                    wk = scheduler.term_week_number(e.date, week1)
                    if wk in hours_by_week:
                        hours_by_week[wk] += (e.minutes or 0) / 60.0

        bar_set = QBarSet("Hours")
        categories = []
        for wk in range(1, max_week + 1):
            bar_set.append(round(hours_by_week[wk], 2))
            categories.append(f"Wk{wk}")
        series = QBarSeries()
        series.append(bar_set)
        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append(categories)
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setTitleText("Hours")
        max_val = max(hours_by_week.values()) if hours_by_week else 0.0
        axis_y.setRange(0, max(5.0, max_val * 1.2))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        self._study_chart_view.setChart(chart)

    def _on_timer_start(self) -> None:
        try:
            if not self._timer_running:
                self._timer_running = True
                self._timer_qtimer.start()
        except Exception:
            log.exception("CourseDetailPage: timer start failed")
            self.statusMessage.emit("Could not start the timer.")

    def _on_timer_pause(self) -> None:
        try:
            self._timer_running = False
            self._timer_qtimer.stop()
        except Exception:
            log.exception("CourseDetailPage: timer pause failed")
            self.statusMessage.emit("Could not pause the timer.")

    def _on_timer_tick(self) -> None:
        self._timer_seconds += 1
        mm, ss = divmod(self._timer_seconds, 60)
        self._study_elapsed_label.setText(f"{mm:02d}:{ss:02d}")

    def _on_timer_stop(self) -> None:
        try:
            self._timer_qtimer.stop()
            seconds = self._timer_seconds
            self._timer_running = False
            self._timer_seconds = 0
            self._study_elapsed_label.setText("00:00")
            if seconds < 60:
                if seconds > 0:
                    self.statusMessage.emit("Timer stopped — less than a minute, not logged.")
                return
            minutes = round(seconds / 60)
            now = datetime.now()
            start_dt = now - timedelta(seconds=seconds)
            topic_id = self._study_topic_combo.currentData()
            entry = models.StudyLogEntry(
                course_id=self.course_id, topic_id=topic_id if topic_id else None,
                date=date.today(), start_time=start_dt.time(), end_time=now.time(),
                minutes=minutes, activity=self._study_activity_combo.currentText(), notes="",
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
        except Exception:
            log.exception("CourseDetailPage: timer stop failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not save the study log entry.")

    # ======================================================================
    # Tab 7: Attendance
    # ======================================================================
    def _build_attendance_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Mark today:"))
        for status in config.ATTENDANCE_STATUSES:
            btn = QPushButton(status)
            btn.clicked.connect(lambda checked=False, s=status: self._on_mark_attendance(s))
            toolbar.addWidget(btn)
        toolbar.addStretch(1)
        self._attendance_pct_label = QLabel("Attendance: —")
        self._attendance_pct_label.setStyleSheet("font-weight: 700;")
        toolbar.addWidget(self._attendance_pct_label)
        layout.addLayout(toolbar)

        self._attendance_model = table_models.DataclassTableModel(table_models.ATTENDANCE_COLUMNS)
        self._attendance_model.on_edit = self._on_attendance_edit
        self._attendance_table = QTableView()
        self._attendance_table.setModel(self._attendance_model)
        table_models.apply_delegates(self._attendance_table, self._attendance_model)
        self._attendance_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self._attendance_table, 1)
        return w

    def _refresh_attendance_tab(self) -> None:
        entries = sorted(
            self.store.list_attendance(self.course_id),
            key=lambda a: (a.date is None, -(a.date.toordinal() if a.date is not None else 0)),
        )
        self._attendance_model.set_rows(entries)
        total = len(entries)
        present = sum(1 for a in entries if a.status == "Present")
        pct = (present / total * 100) if total else None
        self._attendance_pct_label.setText(
            f"Attendance: {pct:.0f}% ({present}/{total})" if pct is not None else "Attendance: —"
        )

    def _on_attendance_edit(self, obj, attr, value):
        try:
            self.store.update_attendance_entry(obj)
            self._defer_refresh()
            return True
        except Exception:
            log.exception("CourseDetailPage: attendance inline edit failed for attendance_id=%s", getattr(obj, "attendance_id", "?"))
            self.statusMessage.emit("Could not save the attendance change.")
            return False

    def _on_mark_attendance(self, status: str) -> None:
        try:
            if self.course_id is None:
                self.statusMessage.emit("Open a course first.")
                return
            today = date.today()
            existing = next((a for a in self.store.list_attendance(self.course_id) if a.date == today), None)
            if existing is not None:
                existing.status = status
                self.store.update_attendance_entry(existing)
            else:
                self.store.add_attendance_entry(
                    models.AttendanceEntry(course_id=self.course_id, date=today, status=status)
                )
            self.refresh()
            self.statusMessage.emit(f"Marked today as {status}.")
        except Exception:
            log.exception("CourseDetailPage: mark attendance failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not mark attendance.")

    # ======================================================================
    # Tab 8: Practice
    # ======================================================================
    def _build_practice_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Add question")
        add_btn.clicked.connect(self._on_add_practice)
        del_btn = QPushButton("Delete selected")
        del_btn.clicked.connect(self._on_delete_practice)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(del_btn)
        toolbar.addWidget(QLabel("(full practice mode: see the Practice page)"))
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self._practice_model = table_models.DataclassTableModel(table_models.PRACTICE_QUESTION_COLUMNS)
        self._practice_model.on_edit = self._on_practice_edit
        self._practice_table = QTableView()
        self._practice_table.setModel(self._practice_model)
        table_models.apply_delegates(self._practice_table, self._practice_model)
        self._practice_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._practice_table.doubleClicked.connect(self._on_edit_practice_row)
        layout.addWidget(self._practice_table, 1)
        return w

    def _refresh_practice_tab(self) -> None:
        self._practice_model.set_rows(self.store.list_practice_questions(self.course_id))

    def _on_practice_edit(self, obj, attr, value):
        try:
            self.store.update_practice_question(obj)
            self._defer_refresh()
            return True
        except Exception:
            log.exception("CourseDetailPage: practice question inline edit failed for question_id=%s", getattr(obj, "question_id", "?"))
            self.statusMessage.emit("Could not save the question change.")
            return False

    def _on_add_practice(self) -> None:
        try:
            if self.course_id is None:
                self.statusMessage.emit("Open a course first.")
                return
            result = dialogs.edit_row(
                self, "Add practice question", table_models.PRACTICE_QUESTION_COLUMNS,
                models.PracticeQuestion(course_id=self.course_id),
                exclude_attrs={"question_id", "course_id"}, multiline_attrs={"question_text"},
            )
            if result is None:
                return
            result.course_id = self.course_id
            self.store.add_practice_question(result)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: add practice question failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not add the question.")

    def _on_edit_practice_row(self, index: QModelIndex) -> None:
        try:
            obj = self._practice_model.row_object(index.row())
            result = dialogs.edit_row(
                self, "Edit practice question", table_models.PRACTICE_QUESTION_COLUMNS, obj,
                exclude_attrs={"question_id", "course_id"}, multiline_attrs={"question_text"},
            )
            if result is None:
                return
            result.question_id = obj.question_id
            result.course_id = self.course_id
            self.store.update_practice_question(result)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: edit practice question failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not save the question.")

    def _on_delete_practice(self) -> None:
        try:
            obj = self._selected_row_object(self._practice_table, self._practice_model)
            if obj is None:
                self.statusMessage.emit("Select a question first.")
                return
            if not widgets.confirm(self, "Delete this practice question?"):
                return
            self.store.delete_practice_question(obj.question_id)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: delete practice question failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not delete the question.")

    # ======================================================================
    # Tab 9: Flags
    # ======================================================================
    def _build_flags_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Add flag")
        add_btn.clicked.connect(self._on_add_flag)
        del_btn = QPushButton("Delete selected")
        del_btn.clicked.connect(self._on_delete_flag)
        toolbar.addWidget(add_btn)
        toolbar.addWidget(del_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self._flags_model = table_models.DataclassTableModel(table_models.SYLLABUS_FLAG_COLUMNS)
        self._flags_model.on_edit = self._on_flag_edit
        self._flags_table = QTableView()
        self._flags_table.setModel(self._flags_model)
        table_models.apply_delegates(self._flags_table, self._flags_model)
        self._flags_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self._flags_table, 1)
        return w

    def _refresh_flags_tab(self) -> None:
        self._flags_model.set_rows(self.store.list_syllabus_flags(self.course_id))

    def _on_flag_edit(self, obj, attr, value):
        try:
            self.store.update_syllabus_flag(obj)
            self._defer_refresh()
            return True
        except Exception:
            log.exception("CourseDetailPage: flag inline edit failed for flag_id=%s", getattr(obj, "flag_id", "?"))
            self.statusMessage.emit("Could not save the flag change.")
            return False

    def _on_add_flag(self) -> None:
        try:
            if self.course_id is None:
                self.statusMessage.emit("Open a course first.")
                return
            result = dialogs.edit_row(
                self, "Add syllabus flag", table_models.SYLLABUS_FLAG_COLUMNS,
                models.SyllabusFlag(course_id=self.course_id, resolved=False),
                exclude_attrs={"flag_id", "course_id"},
            )
            if result is None:
                return
            result.course_id = self.course_id
            self.store.add_syllabus_flag(result)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: add flag failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not add the flag.")

    def _on_delete_flag(self) -> None:
        try:
            obj = self._selected_row_object(self._flags_table, self._flags_model)
            if obj is None:
                self.statusMessage.emit("Select a flag first.")
                return
            if not widgets.confirm(self, f"Delete flag '{obj.item}'?"):
                return
            self.store.delete_syllabus_flag(obj.flag_id)
            self.refresh()
        except Exception:
            log.exception("CourseDetailPage: delete flag failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not delete the flag.")

    # ======================================================================
    # Shared helpers
    # ======================================================================
    @staticmethod
    def _selected_row_object(table: QTableView, model: table_models.DataclassTableModel):
        sel = table.selectionModel()
        if sel is None:
            return None
        idxs = sel.selectedRows()
        if not idxs:
            return None
        return model.row_object(idxs[0].row())

    # ======================================================================
    # refresh()
    # ======================================================================
    def refresh(self) -> None:
        try:
            self.course = self.store.get_course(self.course_id) if self.course_id is not None else None
            if self.course is None:
                self._show_empty_state()
                return
            self._refresh_header()
            self._refresh_materials()
            self._refresh_topics_tab()
            self._refresh_labs_tab()
            self._refresh_assessments_tab()
            self._refresh_grades_tab()
            self._refresh_roadmap_tab()
            self._refresh_study_tab()
            self._refresh_attendance_tab()
            self._refresh_practice_tab()
            self._refresh_flags_tab()
        except Exception:
            log.exception("CourseDetailPage.refresh failed for course_id=%s", self.course_id)
            self.statusMessage.emit("Could not load this course's details.")

    def _show_empty_state(self) -> None:
        self._header_title.setText("No course selected")
        self._header_instructor.setText("—")
        self._header_office.setText("")
        self._header_sections.setText("")
        self._header_grade.setText("—")
        self._header_target.setText("")
        self._header_projected.setText("")
        self._header_theory.setText("")
        self._header_lab.setText("")
        self._header_labcompletion.setText("")
        self._header_flags_btn.setText("")

        widgets.clear_layout(self._materials_layout)

        self._topics_model = QStandardItemModel()
        self._topics_model.setHorizontalHeaderLabels(
            ["Unit / Section", "Title", "Status", "Conf.", "Priority", "Last reviewed", "Est. hrs", "Open"]
        )
        self._topics_tree.setModel(self._topics_model)

        for model in (
            self._labs_model, self._assessments_model, self._weights_model,
            self._roadmap_model, self._studylog_model, self._attendance_model,
            self._practice_model, self._flags_model,
        ):
            model.set_rows([])

        widgets.clear_layout(self._category_avg_layout)
        self._grade_theory_label.setText("Theory: —")
        self._grade_lab_label.setText("Lab: —")
        self._grade_overall_label.setText("Overall current: —")
        self._grade_projected_label.setText("Projected final: —")
        self._nait_theory_label.setText("")
        self._nait_lab_label.setText("")
        self._nait_labs_label.setText("")
        self._nait_capped_label.setVisible(False)
        self._whatif_label.setText("—")
        self._whatif_item_label.setText("—")
        self._whatif_item_combo.clear()
        self._overall_grade_result = None
        self._nait_result = None
        self._trend_chart_view.setChart(QChart())

        self._study_topic_combo.clear()
        self._study_chart_view.setChart(QChart())
        self._attendance_pct_label.setText("Attendance: —")
