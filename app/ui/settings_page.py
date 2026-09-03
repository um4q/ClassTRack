"""
Settings page (build spec §5.11) - workbook path, semester dates, theme,
notifications, default study block, PDF opener command, and the app's
global action buttons: reload/save/backup, open data/coursepacks folders,
extract coursepack zips, export .ics, import syllabus PDF dates, and
trigger the weekly summary dialog (the shell owns showing that dialog;
this page only emits ``showWeeklySummaryRequested``).

No "reset to sample data" button - there is no sample data to reset to.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QDate, QSignalBlocker, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from app import config
from app.excel_store import ExcelStore
from app.models import Assessment, Course
from app.services import ics_export, importers, unzip

log = logging.getLogger("study_tracker")

_LIGHT_QSS_PATH = Path(__file__).resolve().parent / "styles_light.qss"
_DARK_QSS_PATH = config.STYLES_QSS_PATH


def _parse_iso_date(text: str) -> Optional[date]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Export .ics: pick which courses + whether to include classes
# --------------------------------------------------------------------------
class _IcsExportDialog(QDialog):
    def __init__(self, courses: list[Course], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export .ics")
        self.setMinimumWidth(360)
        outer = QVBoxLayout(self)

        outer.addWidget(QLabel("Include courses:"))
        self._course_checks: list[tuple[int, QCheckBox]] = []
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        for c in courses:
            cb = QCheckBox(f"{c.code} - {c.name}")
            cb.setChecked(bool(c.active))
            host_layout.addWidget(cb)
            self._course_checks.append((c.course_id, cb))
        host_layout.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

        self.include_classes_chk = QCheckBox("Include classes (from the weekly Schedule)")
        self.include_classes_chk.setChecked(False)
        outer.addWidget(self.include_classes_chk)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def selected_course_ids(self) -> set[int]:
        return {cid for cid, cb in self._course_checks if cb.isChecked()}

    def include_classes(self) -> bool:
        return self.include_classes_chk.isChecked()


# --------------------------------------------------------------------------
# Import syllabus PDF: preview scan results as a checkable, editable list
# --------------------------------------------------------------------------
class _SyllabusImportDialog(QDialog):
    _MAX_ROWS = 300

    def __init__(self, results: list[dict], courses: list[Course], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import syllabus PDF - preview")
        self.resize(760, 520)
        self._rows: list[dict] = []
        outer = QVBoxLayout(self)

        course_row = QHBoxLayout()
        course_row.addWidget(QLabel("Add checked items as assessments for course:"))
        self.course_combo = QComboBox()
        for c in courses:
            self.course_combo.addItem(f"{c.code} - {c.name}", c.course_id)
        course_row.addWidget(self.course_combo, 1)
        outer.addLayout(course_row)

        hint = QLabel(
            f"Found {len(results)} line(s) mentioning a date near an exam/assessment "
            "keyword. Rows with a recognized date are pre-checked; edit title, date "
            "(YYYY-MM-DD) or type before confirming."
        )
        hint.setWordWrap(True)
        outer.addWidget(hint)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(QLabel(""), 0)
        header_layout.addWidget(QLabel("Title"), 2)
        header_layout.addWidget(QLabel("Date"), 0)
        header_layout.addWidget(QLabel("Page"), 0)
        header_layout.addWidget(QLabel("Type"), 0)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        rows_layout = QVBoxLayout(host)
        rows_layout.setContentsMargins(0, 0, 0, 0)

        shown = results[: self._MAX_ROWS]
        for r in shown:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 2, 0, 2)

            chk = QCheckBox()
            chk.setChecked(r.get("date") is not None)
            row_layout.addWidget(chk, 0)

            title_edit = QLineEdit(r.get("title") or "")
            row_layout.addWidget(title_edit, 2)

            date_edit = QLineEdit(r.get("date") or "")
            date_edit.setPlaceholderText("YYYY-MM-DD")
            date_edit.setFixedWidth(100)
            row_layout.addWidget(date_edit, 0)

            page_lbl = QLabel(str(r.get("page") or ""))
            page_lbl.setFixedWidth(36)
            page_lbl.setAlignment(Qt.AlignCenter)
            row_layout.addWidget(page_lbl, 0)

            type_combo = QComboBox()
            type_combo.addItems(config.ASSESSMENT_TYPES)
            type_combo.setCurrentText("Assignment")
            row_layout.addWidget(type_combo, 0)

            rows_layout.addWidget(row_widget)
            self._rows.append({
                "checkbox": chk, "title_edit": title_edit, "date_edit": date_edit,
                "type_combo": type_combo, "page": r.get("page"),
            })
        rows_layout.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

        if len(results) > self._MAX_ROWS:
            trunc = QLabel(f"Showing the first {self._MAX_ROWS} of {len(results)} matches.")
            trunc.setStyleSheet("color: #94a3b8;")
            outer.addWidget(trunc)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Add checked as assessments")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def selected(self) -> tuple[Optional[int], list[dict]]:
        course_id = self.course_combo.currentData()
        picked: list[dict] = []
        for r in self._rows:
            if r["checkbox"].isChecked():
                picked.append({
                    "title": r["title_edit"].text().strip(),
                    "date_text": r["date_edit"].text().strip(),
                    "type": r["type_combo"].currentText(),
                    "page": r["page"],
                })
        return course_id, picked


# --------------------------------------------------------------------------
# Settings page
# --------------------------------------------------------------------------
class SettingsPage(QWidget):
    statusMessage = Signal(str)
    navigateTo = Signal(str)
    showWeeklySummaryRequested = Signal()

    _SEMESTER_DATE_KEYS = [
        ("semester_start", "Semester start"),
        ("semester_end", "Semester end"),
        ("week1_monday", "Week 1 Monday"),
        ("exam_week_start", "Exam week start"),
    ]

    def __init__(self, store: ExcelStore, parent=None):
        super().__init__(parent)
        self.store = store
        self._date_edits: dict[str, QDateEdit] = {}

        outer = QVBoxLayout(self)
        title = QLabel("Settings")
        title.setStyleSheet("font-size: 16pt; font-weight: 700;")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)

        content_layout.addWidget(self._build_workbook_group())
        content_layout.addWidget(self._build_semester_group())
        content_layout.addWidget(self._build_appearance_group())
        content_layout.addWidget(self._build_pdf_opener_group())
        content_layout.addWidget(self._build_actions_group())
        content_layout.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #94a3b8;")
        outer.addWidget(self._status_label)
        self.statusMessage.connect(self._status_label.setText)

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
                log.exception("SettingsPage: action failed")
                self.statusMessage.emit("That action failed - see data/app.log.")
        return wrapped

    def _qdate_for_setting(self, key: str) -> QDate:
        d = self.store.setting_date(key)
        if d is None:
            d = _parse_iso_date(config.SETTINGS_DEFAULTS.get(key, "")) or date.today()
        return QDate(d.year, d.month, d.day)

    # ------------------------------------------------------------------
    # Group builders
    # ------------------------------------------------------------------
    def _build_workbook_group(self) -> QGroupBox:
        box = QGroupBox("Workbook")
        form = QFormLayout(box)

        row = QHBoxLayout()
        self.workbook_path_edit = QLineEdit()
        self.workbook_path_edit.setReadOnly(True)
        row.addWidget(self.workbook_path_edit, 1)
        change_btn = QPushButton("Change...")
        change_btn.clicked.connect(self._guard(self._on_change_workbook_path))
        row.addWidget(change_btn)
        row_widget = QWidget()
        row_widget.setLayout(row)
        form.addRow("Workbook file", row_widget)
        return box

    def _build_semester_group(self) -> QGroupBox:
        box = QGroupBox("Semester dates")
        form = QFormLayout(box)
        for key, label in self._SEMESTER_DATE_KEYS:
            ed = QDateEdit()
            ed.setCalendarPopup(True)
            ed.setDisplayFormat("yyyy-MM-dd")
            ed.dateChanged.connect(self._guard(self._make_date_handler(key)))
            self._date_edits[key] = ed
            form.addRow(label, ed)
        return box

    def _build_appearance_group(self) -> QGroupBox:
        box = QGroupBox("Appearance & study")
        form = QFormLayout(box)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Dark", "Light"])
        self.theme_combo.currentTextChanged.connect(self._guard(self._on_theme_changed))
        form.addRow("Theme", self.theme_combo)

        self.notifications_chk = QCheckBox("Notify about pre-labs/assessments/exams due soon")
        self.notifications_chk.toggled.connect(self._guard(self._on_notifications_toggled))
        form.addRow("Notifications", self.notifications_chk)

        self.study_block_spin = QSpinBox()
        self.study_block_spin.setRange(5, 240)
        self.study_block_spin.setSuffix(" min")
        self.study_block_spin.valueChanged.connect(self._guard(self._on_study_block_changed))
        form.addRow("Default study block", self.study_block_spin)

        return box

    def _build_pdf_opener_group(self) -> QGroupBox:
        box = QGroupBox("PDF opener command")
        layout = QVBoxLayout(box)

        self.pdf_opener_edit = QLineEdit()
        self.pdf_opener_edit.setPlaceholderText('e.g. -page {page} "{path}"  (SumatraPDF)')
        self.pdf_opener_edit.editingFinished.connect(self._guard(self._on_pdf_opener_changed))
        layout.addWidget(self.pdf_opener_edit)

        help_lbl = QLabel(
            '{path} and {page} are substituted with the file path and page number. '
            'Example (SumatraPDF): -page {page} "{path}"\n'
            'Leave blank to use the default app via file:///...#page=N (works in Edge/Chrome).'
        )
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet("color: #94a3b8;")
        layout.addWidget(help_lbl)
        return box

    def _build_actions_group(self) -> QGroupBox:
        box = QGroupBox("Actions")
        layout = QVBoxLayout(box)

        def add_row(*buttons: QPushButton) -> None:
            row = QHBoxLayout()
            for b in buttons:
                row.addWidget(b)
            row.addStretch(1)
            layout.addLayout(row)

        reload_btn = QPushButton("Reload from Excel")
        reload_btn.clicked.connect(self._guard(self._on_reload))
        save_btn = QPushButton("Save now")
        save_btn.clicked.connect(self._guard(self._on_save_now))
        backup_btn = QPushButton("Backup now")
        backup_btn.clicked.connect(self._guard(self._on_backup_now))
        add_row(reload_btn, save_btn, backup_btn)

        data_folder_btn = QPushButton("Open data folder")
        data_folder_btn.clicked.connect(self._guard(self._on_open_data_folder))
        coursepacks_folder_btn = QPushButton("Open coursepacks folder")
        coursepacks_folder_btn.clicked.connect(self._guard(self._on_open_coursepacks_folder))
        extract_btn = QPushButton("Extract coursepack zips now")
        extract_btn.clicked.connect(self._guard(self._on_extract_coursepacks))
        add_row(data_folder_btn, coursepacks_folder_btn, extract_btn)

        ics_btn = QPushButton("Export .ics")
        ics_btn.clicked.connect(self._guard(self._on_export_ics))
        syllabus_btn = QPushButton("Import syllabus PDF")
        syllabus_btn.clicked.connect(self._guard(self._on_import_syllabus))
        summary_btn = QPushButton("Show weekly summary now")
        summary_btn.clicked.connect(self._guard(self._on_show_weekly_summary))
        add_row(ics_btn, syllabus_btn, summary_btn)

        return box

    # ------------------------------------------------------------------
    # refresh()
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        try:
            self.workbook_path_edit.setText(str(self.store.path))
        except Exception:
            log.exception("SettingsPage: failed to show workbook path")
            self.statusMessage.emit("Could not read the workbook path.")

        for key, ed in self._date_edits.items():
            try:
                with QSignalBlocker(ed):
                    ed.setDate(self._qdate_for_setting(key))
            except Exception:
                log.exception("SettingsPage: failed to load setting %s", key)

        try:
            with QSignalBlocker(self.theme_combo):
                theme_val = (self.store.get_setting("theme") or "dark").strip().lower()
                self.theme_combo.setCurrentText("Light" if theme_val == "light" else "Dark")
        except Exception:
            log.exception("SettingsPage: failed to load theme setting")

        try:
            with QSignalBlocker(self.notifications_chk):
                self.notifications_chk.setChecked(self.store.setting_bool("notifications", True))
        except Exception:
            log.exception("SettingsPage: failed to load notifications setting")

        try:
            with QSignalBlocker(self.study_block_spin):
                self.study_block_spin.setValue(self.store.setting_int("default_study_block_min", 50))
        except Exception:
            log.exception("SettingsPage: failed to load default_study_block_min")

        try:
            with QSignalBlocker(self.pdf_opener_edit):
                self.pdf_opener_edit.setText(self.store.get_setting("pdf_opener_cmd") or "")
        except Exception:
            log.exception("SettingsPage: failed to load pdf_opener_cmd")

    # ------------------------------------------------------------------
    # Workbook path
    # ------------------------------------------------------------------
    def _on_change_workbook_path(self) -> None:
        start_dir = str(self.store.path.parent) if self.store.path.parent.exists() else str(Path.home())
        QFileDialog.getOpenFileName(self, "Locate a workbook (view only)", start_dir, "Excel Workbook (*.xlsx)")
        QMessageBox.information(
            self, "Workbook location",
            "Study Tracker always reads and writes:\n\n"
            f"{self.store.path}\n\n"
            "To use a different workbook, close Study Tracker, move or copy your "
            "file to that exact path, then relaunch. Changing the workbook path "
            "while the app is running is not supported.",
        )

    # ------------------------------------------------------------------
    # Semester dates
    # ------------------------------------------------------------------
    def _make_date_handler(self, key: str):
        def handler(qdate: QDate) -> None:
            d = date(qdate.year(), qdate.month(), qdate.day())
            self.store.set_setting(key, d.isoformat())
            self.statusMessage.emit(f"{key} set to {d.isoformat()}.")
        return handler

    # ------------------------------------------------------------------
    # Appearance & study
    # ------------------------------------------------------------------
    def _on_theme_changed(self, text: str) -> None:
        theme_key = (text or "Dark").strip().lower()
        qss_path = _DARK_QSS_PATH if theme_key == "dark" else _LIGHT_QSS_PATH
        qss_text = qss_path.read_text(encoding="utf-8")
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(qss_text)
        self.store.set_setting("theme", theme_key)
        self.statusMessage.emit(f"Theme set to {text}.")

    def _on_notifications_toggled(self, checked: bool) -> None:
        self.store.set_setting("notifications", "TRUE" if checked else "FALSE")
        self.statusMessage.emit("Notifications " + ("enabled." if checked else "disabled."))

    def _on_study_block_changed(self, value: int) -> None:
        self.store.set_setting("default_study_block_min", str(value))

    # ------------------------------------------------------------------
    # PDF opener
    # ------------------------------------------------------------------
    def _on_pdf_opener_changed(self) -> None:
        self.store.set_setting("pdf_opener_cmd", self.pdf_opener_edit.text().strip())
        self.statusMessage.emit("PDF opener command saved.")

    # ------------------------------------------------------------------
    # Reload / save / backup
    # ------------------------------------------------------------------
    def _on_reload(self) -> None:
        self.store.reload()
        self.refresh()
        self.statusMessage.emit("Reloaded from Excel.")

    def _on_save_now(self) -> None:
        ok = self.store.save(force=True)
        if ok:
            self.statusMessage.emit("Saved.")
        else:
            QMessageBox.warning(
                self, "Save now",
                "Save failed - the workbook may be open in Excel. "
                "Close it and try again (autosave will also retry automatically).",
            )

    def _on_backup_now(self) -> None:
        dest = self.store.backup_now()
        if dest is not None:
            self.statusMessage.emit(f"Backup created: {dest.name}")
        else:
            QMessageBox.warning(self, "Backup now", "Backup failed - see data/app.log for details.")

    # ------------------------------------------------------------------
    # Folders / coursepack extraction
    # ------------------------------------------------------------------
    def _on_open_data_folder(self) -> None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.DATA_DIR)))
        self.statusMessage.emit("Opened data folder." if ok else "Could not open the data folder.")

    def _on_open_coursepacks_folder(self) -> None:
        config.COURSEPACKS_DIR.mkdir(parents=True, exist_ok=True)
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.COURSEPACKS_DIR)))
        self.statusMessage.emit("Opened coursepacks folder." if ok else "Could not open the coursepacks folder.")

    def _on_extract_coursepacks(self) -> None:
        log_lines = unzip.extract_coursepacks(config.COURSEPACKS_DIR)
        if not log_lines:
            QMessageBox.information(self, "Extract coursepack zips", "No zip files found in data/coursepacks/.")
        else:
            QMessageBox.information(self, "Extract coursepack zips", "\n".join(log_lines))
        self.statusMessage.emit(f"Coursepack extraction finished ({len(log_lines)} log line(s)).")

    # ------------------------------------------------------------------
    # Export .ics
    # ------------------------------------------------------------------
    def _on_export_ics(self) -> None:
        courses = self.store.list_courses()
        if not courses:
            QMessageBox.information(self, "Export .ics", "No courses to export yet.")
            return
        dlg = _IcsExportDialog(courses, self)
        if dlg.exec() != QDialog.Accepted:
            return
        selected_ids = dlg.selected_course_ids()
        if not selected_ids:
            self.statusMessage.emit("No courses selected - nothing exported.")
            return
        include_classes = dlg.include_classes()

        sel_courses = [c for c in courses if c.course_id in selected_ids]
        assessments = [a for a in self.store.list_assessments() if a.course_id in selected_ids]
        prelabs = [p for p in self.store.list_prelabs() if p.course_id in selected_ids]
        schedule = [s for s in self.store.list_schedule() if s.course_id in selected_ids] if include_classes else None
        holidays = self.store.list_holidays()
        semester_start = self.store.setting_date("semester_start")
        semester_end = self.store.setting_date("semester_end")

        text = ics_export.build_ics(
            courses=sel_courses, assessments=assessments, prelabs=prelabs,
            schedule=schedule, holidays=holidays, include_classes=include_classes,
            semester_start=semester_start, semester_end=semester_end,
        )

        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save .ics", str(Path.home() / "study_tracker.ics"), "iCalendar (*.ics)"
        )
        if not save_path:
            return
        with open(save_path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        self.statusMessage.emit(
            f"Exported {len(assessments)}+{len(prelabs)} event(s) to {Path(save_path).name}."
        )

    # ------------------------------------------------------------------
    # Import syllabus PDF
    # ------------------------------------------------------------------
    def _on_import_syllabus(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import syllabus PDF", str(config.DATA_DIR), "PDF files (*.pdf);;All files (*)"
        )
        if not path:
            return
        results = importers.scan_syllabus_pdf_dates(path)
        if not results:
            QMessageBox.information(
                self, "Import syllabus PDF",
                "No date-like lines were found in that PDF (or the pypdf package "
                "isn't installed).",
            )
            return
        courses = self.store.list_courses()
        if not courses:
            QMessageBox.warning(self, "Import syllabus PDF", "Add a course first - imported assessments need one.")
            return

        dlg = _SyllabusImportDialog(results, courses, self)
        if dlg.exec() != QDialog.Accepted:
            return
        course_id, rows = dlg.selected()
        if not course_id or not rows:
            self.statusMessage.emit("No rows selected - nothing imported.")
            return

        added = 0
        for row in rows:
            try:
                a = Assessment(
                    course_id=course_id,
                    type=row["type"] or "Assignment",
                    title=row["title"] or "Imported item",
                    category="",
                    due_date=_parse_iso_date(row["date_text"]),
                    status="Not Started",
                    max_score=100.0,
                    notes=f"Imported from syllabus PDF page {row['page']}",
                )
                self.store.add_assessment(a)
                added += 1
            except Exception:
                log.exception("SettingsPage: failed to add imported assessment row %r", row)
        self.statusMessage.emit(f"Imported {added} assessment(s) from the syllabus PDF.")

    # ------------------------------------------------------------------
    # Weekly summary
    # ------------------------------------------------------------------
    def _on_show_weekly_summary(self) -> None:
        self.showWeeklySummaryRequested.emit()
