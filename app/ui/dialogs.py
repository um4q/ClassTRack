"""
Generic add/edit dialogs driven by the same ColumnSpec list used by
table_models.DataclassTableModel - one dialog class serves every row type
(Course, Topic, Assessment, PreLab, GradeWeight, Goal, PracticeQuestion,
Material, Schedule, ...), so pages don't need a bespoke dialog per sheet.
"""
from __future__ import annotations

import copy
from datetime import date, time
from typing import Any, Optional

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QLineEdit, QPlainTextEdit, QSpinBox, QTimeEdit, QVBoxLayout, QWidget,
)

from app.ui.table_models import ColumnSpec
from app.ui.widgets import ChecklistWidget

_NO_DATE = QDate(2000, 1, 1)  # sentinel meaning "blank" for an Optional[date] field


class RowEditDialog(QDialog):
    """Add/Edit form auto-built from a ColumnSpec list.

    Known simplification: Optional[float] fields (score, target_grade, ...)
    default to 0.0 rather than round-tripping a true blank - clear them via
    the table's own cell editor if you need to reset to "ungraded"."""

    def __init__(self, title: str, columns: list[ColumnSpec], obj: Any, parent=None,
                 exclude_attrs: Optional[set[str]] = None, multiline_attrs: Optional[set[str]] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self.obj = copy.copy(obj)
        self._editors: dict[str, QWidget] = {}
        exclude_attrs = exclude_attrs or set()
        multiline_attrs = multiline_attrs or set()

        outer = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        outer.addLayout(form)

        for col in columns:
            if col.attr in exclude_attrs:
                continue
            value = getattr(obj, col.attr, None)
            editor = self._make_editor(col, value, multiline=col.attr in multiline_attrs)
            self._editors[col.attr] = editor
            form.addRow(col.header, editor)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _make_editor(self, col: ColumnSpec, value: Any, multiline: bool = False) -> QWidget:
        if col.attr == "checklist":
            return ChecklistWidget(value or [])
        if col.kind == "bool":
            box = QCheckBox()
            box.setChecked(bool(value))
            return box
        if col.kind == "combo" and col.choices:
            box = QComboBox()
            box.addItems(col.choices)
            if value is not None and str(value) in col.choices:
                box.setCurrentText(str(value))
            return box
        if col.kind == "date":
            ed = QDateEdit()
            ed.setCalendarPopup(True)
            ed.setDisplayFormat("yyyy-MM-dd")
            ed.setMinimumDate(_NO_DATE)
            ed.setSpecialValueText("(blank)")
            ed.setDate(QDate(value.year, value.month, value.day) if isinstance(value, date) else _NO_DATE)
            return ed
        if col.kind == "time":
            ed = QTimeEdit()
            ed.setDisplayFormat("HH:mm")
            ed.setTime(QTime(value.hour, value.minute) if isinstance(value, time) else QTime(0, 0))
            return ed
        if col.kind == "int":
            sp = QSpinBox()
            sp.setRange(-1_000_000, 1_000_000)
            sp.setValue(int(value) if value is not None else 0)
            return sp
        if col.kind == "float":
            sp = QDoubleSpinBox()
            sp.setRange(-1_000_000, 1_000_000)
            sp.setDecimals(2)
            sp.setValue(float(value) if value is not None else 0.0)
            return sp
        if multiline:
            ed = QPlainTextEdit()
            ed.setPlainText("" if value is None else str(value))
            return ed
        if isinstance(value, list):
            return QLineEdit(", ".join(str(v) for v in value))
        return QLineEdit("" if value is None else str(value))

    def result_object(self) -> Any:
        """Apply every editor's current value onto the (copied) object and
        return it - caller still passes this to the matching ExcelStore
        add_*/update_* method."""
        for attr, editor in self._editors.items():
            self._apply(attr, editor)
        return self.obj

    def _apply(self, attr: str, editor: QWidget) -> None:
        current = getattr(self.obj, attr, None)
        if isinstance(editor, ChecklistWidget):
            setattr(self.obj, attr, editor.items)
        elif isinstance(editor, QCheckBox):
            setattr(self.obj, attr, editor.isChecked())
        elif isinstance(editor, QComboBox):
            text = editor.currentText()
            setattr(self.obj, attr, int(text) if text.isdigit() else text)
        elif isinstance(editor, QDateEdit):
            qd = editor.date()
            setattr(self.obj, attr, None if qd == _NO_DATE else date(qd.year(), qd.month(), qd.day()))
        elif isinstance(editor, QTimeEdit):
            qt_ = editor.time()
            setattr(self.obj, attr, time(qt_.hour(), qt_.minute()))
        elif isinstance(editor, QSpinBox):
            setattr(self.obj, attr, editor.value())
        elif isinstance(editor, QDoubleSpinBox):
            setattr(self.obj, attr, editor.value())
        elif isinstance(editor, QPlainTextEdit):
            setattr(self.obj, attr, editor.toPlainText())
        elif isinstance(editor, QLineEdit):
            text = editor.text()
            if isinstance(current, list):
                elem_is_int = bool(current) and all(isinstance(v, int) for v in current)
                parts = [p.strip() for p in text.split(",") if p.strip()]
                setattr(self.obj, attr, [int(p) for p in parts] if elem_is_int else parts)
            else:
                setattr(self.obj, attr, text)


def edit_row(parent: Optional[QWidget], title: str, columns: list[ColumnSpec], obj: Any,
             exclude_attrs: Optional[set[str]] = None, multiline_attrs: Optional[set[str]] = None) -> Optional[Any]:
    """Open a RowEditDialog; returns the edited object on OK, else None."""
    dlg = RowEditDialog(title, columns, obj, parent, exclude_attrs, multiline_attrs)
    if dlg.exec() == QDialog.Accepted:
        return dlg.result_object()
    return None
