"""
Study Tracker entry point.

Run via start.bat (or `python main.py` from the project root with the venv
active). Bootstraps QApplication, extracts any new coursepack zips, loads
the workbook, applies the saved theme, draws a placeholder app icon if one
isn't already in assets/, and shows the main window.
"""
from __future__ import annotations

import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from app import config
from app.excel_store import ExcelStore, logger
from app.services.unzip import extract_coursepacks


def _ensure_icon() -> None:
    """Draw a simple app icon with QPainter if assets/icon.png is missing (spec §2)."""
    if config.ICON_PATH.exists():
        return
    try:
        config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        size = 256
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(config.DEFAULT_COURSE_COLOR))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(8, 8, size - 16, size - 16, 48, 48)
        painter.setPen(QColor("white"))
        font = QFont("Segoe UI", 120, QFont.Bold)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignCenter, "S")
        painter.end()
        pix.save(str(config.ICON_PATH), "PNG")
    except Exception:
        logger.exception("Failed to draw a placeholder icon - continuing without one")


def _install_excepthook() -> None:
    """Slots already catch their own exceptions (status-bar message + log);
    this is the last-resort net for anything that still escapes."""
    def handle(exc_type, exc_value, exc_tb):
        logger.error(
            "Unhandled exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        try:
            QMessageBox.critical(
                None, config.APP_NAME,
                f"An unexpected error occurred:\n\n{exc_value}\n\nSee data/app.log for details.",
            )
        except Exception:
            pass
    sys.excepthook = handle


def _load_stylesheet(theme: str) -> str:
    path = config.STYLES_QSS_PATH if theme != "light" else config.STYLES_QSS_PATH.with_name("styles_light.qss")
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("Could not read stylesheet %s", path)
        return ""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setOrganizationName(config.APP_ORG)
    _install_excepthook()
    _ensure_icon()
    if config.ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(config.ICON_PATH)))

    try:
        for line in extract_coursepacks(config.COURSEPACKS_DIR):
            logger.info("coursepacks: %s", line)
    except Exception:
        logger.exception("Coursepack extraction failed - continuing without it")

    store = ExcelStore()
    store.load()

    app.setStyleSheet(_load_stylesheet(store.get_setting("theme", "dark")))

    from app.ui.main_window import MainWindow
    window = MainWindow(store)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
