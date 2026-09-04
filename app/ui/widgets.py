"""
Small, generic, reusable widgets/helpers shared across pages: opening a
workbook-relative resource, status/urgency badges, a checklist editor, a
countdown card, and a dynamic button grid ("a new row = a new button" per
build spec §1).
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date, datetime, time as dtime
from typing import Callable, Optional

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app import config


def _logger():
    import logging
    return logging.getLogger("study_tracker")


# --------------------------------------------------------------------------
# Opening files / folders / URLs / PDF pages (Materials.path, Topics.link,
# PreLabs.link - a link may be a file, a folder, a URL, or file.pdf#page=N)
# --------------------------------------------------------------------------
def open_resource(store, link: Optional[str], page: Optional[int] = None, parent: Optional[QWidget] = None) -> bool:
    if not link:
        return False
    raw = link.strip()
    if raw.lower().startswith(("http://", "https://")):
        return QDesktopServices.openUrl(QUrl(raw))

    embedded_page = None
    path_part = raw
    if "#page=" in raw:
        path_part, _, page_str = raw.partition("#page=")
        try:
            embedded_page = int(page_str)
        except ValueError:
            embedded_page = None
    resolved = store.resolve_path(path_part)
    if resolved is None:
        return False
    effective_page = page if page is not None else embedded_page

    if not resolved.exists():
        if parent is not None:
            resp = QMessageBox.warning(
                parent, "Not found",
                f"{resolved}\n\nThis file wasn't found - browse for it?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return False
            chosen, _ = QFileDialog.getOpenFileName(parent, "Locate file", str(config.DATA_DIR))
            if not chosen:
                return False
            resolved = type(resolved)(chosen)
        else:
            _logger().warning("open_resource: missing file %s", resolved)
            return False

    opener_cmd = (store.get_setting("pdf_opener_cmd") or "").strip()
    if opener_cmd and effective_page is not None and resolved.suffix.lower() == ".pdf":
        cmd = opener_cmd.replace("{path}", str(resolved)).replace("{page}", str(effective_page))
        try:
            subprocess.Popen(cmd, shell=True)
            return True
        except OSError:
            _logger().exception("Custom PDF opener failed: %s", cmd)

    if resolved.is_dir():
        if sys.platform.startswith("win"):
            os.startfile(str(resolved))
            return True
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(resolved)))

    if effective_page is not None and resolved.suffix.lower() == ".pdf":
        url = QUrl.fromLocalFile(str(resolved))
        url.setFragment(f"page={effective_page}")
        return QDesktopServices.openUrl(url)

    if sys.platform.startswith("win"):
        try:
            os.startfile(str(resolved))
            return True
        except OSError:
            _logger().exception("os.startfile failed for %s", resolved)
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(resolved)))


# --------------------------------------------------------------------------
# Status / urgency
# --------------------------------------------------------------------------
def urgency_color(target_date: Optional[date], target_time: Optional[dtime] = None, now: Optional[datetime] = None) -> str:
    """overdue -> red, < 48h -> amber, < 7 days -> yellow, else grey."""
    if target_date is None:
        return config.URGENCY_NORMAL
    now = now or datetime.now()
    target_dt = datetime.combine(target_date, target_time or dtime(23, 59))
    delta_hours = (target_dt - now).total_seconds() / 3600
    if delta_hours < 0:
        return config.URGENCY_OVERDUE
    if delta_hours < 48:
        return config.URGENCY_SOON
    if delta_hours < 24 * 7:
        return config.URGENCY_UPCOMING
    return config.URGENCY_NORMAL


class Badge(QLabel):
    """A small colored pill label, e.g. a topic-status badge."""
    def __init__(self, text: str, color_hex: str, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_color(color_hex)

    def set_color(self, color_hex: str) -> None:
        self.setStyleSheet(
            f"background-color: {color_hex}; color: white; border-radius: 8px;"
            f"padding: 2px 8px; font-weight: 600;"
        )


def flag_badge(tooltip: str = "") -> QLabel:
    lbl = QLabel("⚠")
    lbl.setStyleSheet(f"color: {config.FLAG_BADGE_COLOR}; font-weight: 700; font-size: 13pt;")
    if tooltip:
        lbl.setToolTip(tooltip)
    return lbl


def colored_progress_bar(value_pct: float, color_hex: Optional[str] = None, fmt: str = "%p%") -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(max(0, min(100, round(value_pct))))
    bar.setFormat(fmt)
    if color_hex:
        bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color_hex}; }}")
    return bar


# --------------------------------------------------------------------------
# Checklist editor (PreLabs.checklist)
# --------------------------------------------------------------------------
class ChecklistWidget(QWidget):
    """A vertical stack of checkboxes bound to a list of ChecklistItem.
    Emits ``changed`` on every toggle; read ``.items`` back afterwards."""
    changed = Signal()

    def __init__(self, items, parent=None):
        super().__init__(parent)
        from app.models import ChecklistItem
        self.items = [ChecklistItem(text=i.text, checked=i.checked) for i in items]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._boxes = []
        for idx, item in enumerate(self.items):
            box = QCheckBox(item.text)
            box.setChecked(item.checked)
            box.toggled.connect(lambda checked, i=idx: self._on_toggle(i, checked))
            layout.addWidget(box)
            self._boxes.append(box)

    def _on_toggle(self, idx: int, checked: bool) -> None:
        self.items[idx].checked = checked
        self.changed.emit()

    def progress(self) -> tuple[int, int]:
        done = sum(1 for i in self.items if i.checked)
        return done, len(self.items)


# --------------------------------------------------------------------------
# Countdown card (dashboard upcoming exams, tracker exams tab)
# --------------------------------------------------------------------------
class CountdownCard(QFrame):
    def __init__(self, title: str, target_date: Optional[date], subtitle: str = "",
                 coverage_pct: Optional[float] = None, flagged: bool = False,
                 flag_tooltip: str = "", on_click: Optional[Callable[[], None]] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("CountdownCard")
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        head = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-weight: 700;")
        title_lbl.setWordWrap(True)
        head.addWidget(title_lbl, 1)
        if flagged:
            head.addWidget(flag_badge(flag_tooltip))
        layout.addLayout(head)

        if target_date is not None:
            days = (target_date - date.today()).days
            when = "today" if days == 0 else ("overdue" if days < 0 else f"in {days} days")
        else:
            when = "date TBC"
        sub_text = when + (f" · {subtitle}" if subtitle else "")
        sub = QLabel(sub_text)
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {urgency_color(target_date)};")
        layout.addWidget(sub)

        if coverage_pct is not None:
            layout.addWidget(colored_progress_bar(coverage_pct, fmt=f"Coverage %p%"))

        if on_click is not None:
            btn = QPushButton("Open")
            # NOTE: QPushButton.clicked emits clicked(bool checked=False). A
            # bare `btn.clicked.connect(on_click)` lets Qt pass that bool
            # into on_click's first parameter, silently clobbering whatever
            # the caller's own closure captured (e.g. a loop variable bound
            # via `lambda x=x: ...`) - wrap it so on_click is always called
            # with zero arguments, matching its documented Callable[[], None].
            btn.clicked.connect(lambda checked=False, _cb=on_click: _cb())
            layout.addWidget(btn)


# --------------------------------------------------------------------------
# Dynamic button grid ("a new row = a new button", build spec §1)
# --------------------------------------------------------------------------
def make_button_grid(items: list[tuple[str, Optional[str], Callable[[], None]]], columns: int = 3, parent: Optional[QWidget] = None) -> QWidget:
    """One QPushButton per (label, color_hex, on_click) tuple, arranged in
    a grid. Used for course buttons, material buttons, and any other
    "a new row = a new button" list in the build spec."""
    container = QWidget(parent)
    grid = QGridLayout(container)
    for idx, (label, color_hex, on_click) in enumerate(items):
        btn = QPushButton(label)
        btn.setMinimumHeight(36)
        if color_hex:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {color_hex}; color: white; "
                f"font-weight: 600; padding: 8px; border-radius: 6px; text-align: left; }}"
                f"QPushButton:hover {{ background-color: {color_hex}; }}"
            )
        # Same fix as CountdownCard above: absorb clicked's bool `checked`
        # argument so on_click always runs with zero arguments - otherwise
        # Qt clobbers a caller's `lambda x=x: fn(x)` loop-variable capture
        # with that bool (this was the root cause of "no course selected"
        # after clicking a course card: cid got overwritten with False/0).
        btn.clicked.connect(lambda checked=False, _cb=on_click: _cb())
        grid.addWidget(btn, idx // columns, idx % columns)
    if not items:
        grid.addWidget(QLabel("(none yet)"), 0, 0)
    return container


def confirm(parent: Optional[QWidget], text: str, title: str = "Confirm") -> bool:
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes


def clear_layout(layout) -> None:
    """Remove and delete every child widget/layout of a layout - handy when
    a page rebuilds a dynamic button grid or card list on refresh()."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())
