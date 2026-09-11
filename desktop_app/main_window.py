from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMainWindow, QTabWidget

from desktop_app.calendar_tab import CalendarTab
from desktop_app.dashboard_tab import DashboardTab
from desktop_app.history_tab import HistoryTab


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scheduler Agents")
        self.resize(900, 600)

        self.history_tab = HistoryTab(project_root)
        self.calendar_tab = CalendarTab(project_root)
        self.dashboard_tab = DashboardTab(project_root, on_run_finished=self._refresh_other_tabs)

        tabs = QTabWidget()
        tabs.addTab(self.dashboard_tab, "Dashboard")
        tabs.addTab(self.history_tab, "History")
        tabs.addTab(self.calendar_tab, "Calendar")
        self.setCentralWidget(tabs)

    def _refresh_other_tabs(self) -> None:
        self.history_tab.refresh()
        self.calendar_tab.refresh()
