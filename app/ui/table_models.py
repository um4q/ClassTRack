"""
Generic Qt table infrastructure driven by column specs, plus predefined
column-spec lists for every sheet shown in more than one place (Assessments,
PreLabs, ...) so every page renders/edits them consistently. No hand-filled
QTableWidget anywhere - every table is a QTableView over a
DataclassTableModel (see build spec §1 "Tables:").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any, Callable, Optional

from PySide6.QtCore import QAbstractTableModel, QDate, QModelIndex, QTime, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QDateEdit, QStyledItemDelegate, QTimeEdit

from app import config
from app.ui.widgets import urgency_color


# --------------------------------------------------------------------------
# Column spec + generic model
# --------------------------------------------------------------------------
@dataclass
class ColumnSpec:
    attr: str
    header: str
    kind: str = "text"  # text|int|float|date|time|bool|combo
    editable: bool = False
    choices: Optional[list[str]] = None
    formatter: Optional[Callable[[Any, Any], str]] = None       # (value, row_obj) -> display text
    color_fn: Optional[Callable[[Any, Any], Optional[str]]] = None  # (value, row_obj) -> hex color or None
    width: Optional[int] = None


class DataclassTableModel(QAbstractTableModel):
    """A QAbstractTableModel over a list of dataclass row instances, driven
    entirely by a ColumnSpec list - no per-page model subclass needed.
    Set ``on_edit(row_obj, attr, new_value)`` to push edits back through
    ExcelStore; return False from it to reject/roll back an edit."""

    def __init__(self, columns: list[ColumnSpec], rows: Optional[list[Any]] = None, parent=None):
        super().__init__(parent)
        self.columns = columns
        self._rows: list[Any] = list(rows or [])
        self.on_edit: Optional[Callable[[Any, str, Any], Optional[bool]]] = None

    def set_rows(self, rows: list[Any]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rows(self) -> list[Any]:
        return self._rows

    def row_object(self, row: int) -> Any:
        return self._rows[row]

    def index_of(self, row_obj: Any) -> int:
        try:
            return self._rows.index(row_obj)
        except ValueError:
            return -1

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return self.columns[section].header

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        col = self.columns[index.column()]
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if col.editable and col.kind == "bool":
            f |= Qt.ItemIsUserCheckable
        elif col.editable:
            f |= Qt.ItemIsEditable
        return f

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        col = self.columns[index.column()]
        obj = self._rows[index.row()]
        value = getattr(obj, col.attr, None)
        if role in (Qt.DisplayRole, Qt.EditRole):
            if col.kind == "bool":
                return None if role == Qt.DisplayRole else value
            if col.formatter:
                return col.formatter(value, obj)
            if value is None:
                return ""
            if isinstance(value, date):
                return value.strftime("%Y-%m-%d")
            if isinstance(value, time):
                return value.strftime("%H:%M")
            if isinstance(value, list):
                return ", ".join(str(v) for v in value)
            return value
        if role == Qt.CheckStateRole and col.kind == "bool":
            return Qt.Checked if value else Qt.Unchecked
        if role == Qt.ForegroundRole and col.color_fn:
            hexcolor = col.color_fn(value, obj)
            if hexcolor:
                return QColor(hexcolor)
        if role == Qt.ToolTipRole and col.formatter is None and isinstance(value, str) and len(value) > 60:
            return value
        return None

    def setData(self, index: QModelIndex, value: Any, role=Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        col = self.columns[index.column()]
        obj = self._rows[index.row()]
        if role == Qt.CheckStateRole and col.kind == "bool":
            try:
                new_value = int(value) != 0
            except (TypeError, ValueError):
                new_value = bool(value)
        elif role == Qt.EditRole:
            new_value = self._coerce(col, value)
        else:
            return False
        setattr(obj, col.attr, new_value)
        if self.on_edit and self.on_edit(obj, col.attr, new_value) is False:
            return False
        self.dataChanged.emit(index, index, [role])
        return True

    def _coerce(self, col: ColumnSpec, value: Any) -> Any:
        if col.kind == "int":
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
        if col.kind == "float":
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        if col.kind == "date" and isinstance(value, QDate):
            return date(value.year(), value.month(), value.day())
        if col.kind == "time" and isinstance(value, QTime):
            return time(value.hour(), value.minute())
        if col.kind == "combo" and isinstance(value, str) and value.isdigit():
            return int(value)
        return value


# --------------------------------------------------------------------------
# Delegates
# --------------------------------------------------------------------------
class ComboBoxDelegate(QStyledItemDelegate):
    def __init__(self, choices: list[str], parent=None):
        super().__init__(parent)
        self.choices = choices

    def createEditor(self, parent, option, index):
        box = QComboBox(parent)
        box.addItems(self.choices)
        return box

    def setEditorData(self, editor, index):
        text = index.data(Qt.EditRole)
        i = editor.findText(str(text)) if text is not None else -1
        editor.setCurrentIndex(max(i, 0))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)


class DateColumnDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        ed = QDateEdit(parent)
        ed.setCalendarPopup(True)
        ed.setDisplayFormat("yyyy-MM-dd")
        return ed

    def setEditorData(self, editor, index):
        model = index.model()
        col = model.columns[index.column()]
        raw = getattr(model.row_object(index.row()), col.attr, None)
        editor.setDate(QDate(raw.year, raw.month, raw.day) if isinstance(raw, date) else QDate.currentDate())

    def setModelData(self, editor, model, index):
        qd = editor.date()
        model.setData(index, date(qd.year(), qd.month(), qd.day()), Qt.EditRole)


class TimeColumnDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        ed = QTimeEdit(parent)
        ed.setDisplayFormat("HH:mm")
        return ed

    def setEditorData(self, editor, index):
        model = index.model()
        col = model.columns[index.column()]
        raw = getattr(model.row_object(index.row()), col.attr, None)
        editor.setTime(QTime(raw.hour, raw.minute) if isinstance(raw, time) else QTime(0, 0))

    def setModelData(self, editor, model, index):
        qt_ = editor.time()
        model.setData(index, time(qt_.hour(), qt_.minute()), Qt.EditRole)


def apply_delegates(table_view, model: DataclassTableModel) -> None:
    """Attach the right delegate to every combo/date/time column of ``model``."""
    for col_idx, col in enumerate(model.columns):
        if col.kind == "combo" and col.choices:
            table_view.setItemDelegateForColumn(col_idx, ComboBoxDelegate(col.choices, table_view))
        elif col.kind == "date":
            table_view.setItemDelegateForColumn(col_idx, DateColumnDelegate(table_view))
        elif col.kind == "time":
            table_view.setItemDelegateForColumn(col_idx, TimeColumnDelegate(table_view))


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def course_column(course_map: dict[int, str], attr: str = "course_id", header: str = "Course") -> ColumnSpec:
    """A read-only 'Course' column resolving course_id -> code, for tables
    that span multiple courses (Tracker, Dashboard)."""
    return ColumnSpec(attr, header, formatter=lambda v, row: course_map.get(v, "?"))


def _status_color(value, row, mapping=config.STATUS_BADGE_COLORS):
    return mapping.get(value)


# --------------------------------------------------------------------------
# Predefined column specs (reused by every page that shows this sheet)
# --------------------------------------------------------------------------
TOPIC_COLUMNS = [
    ColumnSpec("section", "Section", width=70),
    ColumnSpec("title", "Title", editable=True),
    ColumnSpec("status", "Status", kind="combo", editable=True, choices=config.TOPIC_STATUSES,
               color_fn=lambda v, row: config.TOPIC_STATUS_COLORS.get(v)),
    ColumnSpec("confidence", "Conf.", kind="combo", editable=True, choices=["1", "2", "3", "4", "5"], width=50),
    ColumnSpec("priority", "Priority", kind="combo", editable=True, choices=["1", "2", "3", "4", "5"], width=60),
    ColumnSpec("last_reviewed", "Last reviewed", kind="date", editable=True),
    ColumnSpec("estimated_hours", "Est. hrs", kind="float", editable=True, width=60),
]

ASSESSMENT_COLUMNS = [
    ColumnSpec("type", "Type", kind="combo", editable=True, choices=config.ASSESSMENT_TYPES),
    ColumnSpec("title", "Title", editable=True),
    ColumnSpec("due_date", "Due date", kind="date", editable=True,
               color_fn=lambda v, row: urgency_color(v, row.due_time)),
    ColumnSpec("due_time", "Time", kind="time", editable=True),
    ColumnSpec("location", "Location", editable=True),
    ColumnSpec("status", "Status", kind="combo", editable=True, choices=config.ASSESSMENT_STATUSES),
    ColumnSpec("score", "Score", kind="float", editable=True, width=60,
               formatter=lambda v, row: "" if v is None else f"{v:g}"),
    ColumnSpec("max_score", "Max", kind="float", editable=True, width=60,
               formatter=lambda v, row: "" if v is None else f"{v:g}"),
    ColumnSpec("notes", "⚠", width=30, formatter=lambda v, row: "⚠" if row.is_flagged else ""),
]

PRELAB_COLUMNS = [
    ColumnSpec("lab_number", "Lab #", width=50),
    ColumnSpec("title", "Title", editable=True),
    ColumnSpec("lab_type", "Type", kind="combo", editable=True, choices=config.PRELAB_TYPES),
    ColumnSpec("lab_date", "Date", kind="date", editable=True),
    ColumnSpec("lab_time", "Time", kind="time", editable=True),
    ColumnSpec("room", "Room", editable=True, width=60),
    ColumnSpec("prelab_due", "Pre-lab due", kind="date", editable=True,
               color_fn=lambda v, row: (None if row.completed else urgency_color(v))),
    ColumnSpec("checklist", "Checklist", formatter=lambda v, row: f"{row.checklist_progress[0]}/{row.checklist_progress[1]}"),
    ColumnSpec("completed", "Done", kind="bool", editable=True, width=45),
]

GRADEWEIGHT_COLUMNS = [
    ColumnSpec("component", "Component", kind="combo", editable=True, choices=config.GRADEWEIGHT_COMPONENTS, width=70),
    ColumnSpec("category", "Category", editable=True,
               formatter=lambda v, row: ("⚠ " + v) if row.is_placeholder else v),
    ColumnSpec("weight_pct", "Weight %", kind="float", editable=True, width=70),
    ColumnSpec("drop_lowest", "Drop lowest", kind="int", editable=True, width=80),
    ColumnSpec("notes", "Notes", editable=True),
]

GOAL_COLUMNS = [
    ColumnSpec("scope", "Scope", kind="combo", editable=True, choices=config.GOAL_SCOPES, width=70),
    ColumnSpec("description", "Description", editable=True),
    ColumnSpec("metric", "Metric", kind="combo", editable=True, choices=config.GOAL_METRICS),
    ColumnSpec("target_value", "Target", kind="float", editable=True, width=60),
    ColumnSpec("current_value", "Current", kind="float", width=60,
               formatter=lambda v, row: "" if v is None else f"{v:g}"),
    ColumnSpec("status", "Status", kind="combo", editable=True, choices=config.GOAL_STATUSES,
               color_fn=lambda v, row: config.STATUS_BADGE_COLORS.get(v)),
]

PRACTICE_QUESTION_COLUMNS = [
    ColumnSpec("question_type", "Type", kind="combo", editable=True, choices=config.PRACTICE_QUESTION_TYPES, width=80),
    ColumnSpec("question_text", "Question", editable=True),
    ColumnSpec("difficulty", "Diff.", kind="combo", editable=True, choices=["1", "2", "3", "4", "5"], width=50),
    ColumnSpec("box", "Box", kind="int", width=40),
    ColumnSpec("times_correct", "Correct", kind="int", width=55,
               formatter=lambda v, row: f"{v}/{row.times_attempted}"),
    ColumnSpec("next_due", "Next due", kind="date", editable=True),
]

STUDYLOG_COLUMNS = [
    ColumnSpec("date", "Date", kind="date", editable=True),
    ColumnSpec("start_time", "Start", kind="time", editable=True),
    ColumnSpec("end_time", "End", kind="time", editable=True),
    ColumnSpec("minutes", "Minutes", kind="int", editable=True, width=70),
    ColumnSpec("activity", "Activity", kind="combo", editable=True, choices=config.STUDYLOG_ACTIVITIES),
    ColumnSpec("notes", "Notes", editable=True),
]

ATTENDANCE_COLUMNS = [
    ColumnSpec("date", "Date", kind="date", editable=True),
    ColumnSpec("status", "Status", kind="combo", editable=True, choices=config.ATTENDANCE_STATUSES,
               color_fn=lambda v, row: {"Present": config.TOPIC_STATUS_COLORS["Mastered"],
                                         "Absent": config.URGENCY_OVERDUE,
                                         "Late": config.URGENCY_SOON,
                                         "Excused": config.URGENCY_NORMAL}.get(v)),
]

SYLLABUS_FLAG_COLUMNS = [
    ColumnSpec("item", "Item", editable=True),
    ColumnSpec("issue", "Issue", editable=True),
    ColumnSpec("source", "Source", editable=True),
    ColumnSpec("resolved", "Resolved", kind="bool", editable=True, width=60),
]

ROADMAP_COLUMNS = [
    ColumnSpec("sort_order", "#", kind="int", editable=True, width=30),
    ColumnSpec("milestone", "Milestone", editable=True),
    ColumnSpec("start_date", "Start", kind="date", editable=True),
    ColumnSpec("end_date", "End", kind="date", editable=True),
    ColumnSpec("status", "Status", kind="combo", editable=True, choices=config.ROADMAP_STATUSES,
               color_fn=lambda v, row: config.STATUS_BADGE_COLORS.get(v)),
    ColumnSpec("auto_generated", "Auto", kind="bool", width=40),
]

MOCKEXAM_COLUMNS = [
    ColumnSpec("name", "Name"),
    ColumnSpec("date_taken", "Date", kind="date"),
    ColumnSpec("score", "Score", formatter=lambda v, row: "" if v is None else f"{v:g}/{row.max_score:g}" if row.max_score else f"{v:g}"),
    ColumnSpec("duration_min", "Duration (min)"),
]

SCHEDULE_COLUMNS = [
    ColumnSpec("kind", "Kind", kind="combo", editable=True, choices=config.SCHEDULE_KINDS, width=70),
    ColumnSpec("section", "Section", editable=True, width=60),
    ColumnSpec("day_of_week", "Day", kind="combo", editable=True, choices=config.DAYS_OF_WEEK),
    ColumnSpec("start_time", "Start", kind="time", editable=True),
    ColumnSpec("end_time", "End", kind="time", editable=True),
    ColumnSpec("room", "Room", editable=True, width=60),
    ColumnSpec("dates", "Only on", formatter=lambda v, row: ", ".join(d.isoformat() for d in v) if v else "every week"),
]

MATERIAL_COLUMNS = [
    ColumnSpec("label", "Label", editable=True),
    ColumnSpec("kind", "Kind", kind="combo", editable=True, choices=config.MATERIAL_KINDS),
    ColumnSpec("path", "Path", editable=True),
    ColumnSpec("sort_order", "Order", kind="int", editable=True, width=50),
]
