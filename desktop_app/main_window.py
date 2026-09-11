from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from desktop_app.calendar_tab import CalendarTab
from desktop_app.dashboard_tab import DashboardTab
from desktop_app.history_tab import HistoryTab
from scheduler_agents.memory.user_memory import UserMemory


class MainWindow(QWidget):
    """QMainWindow adds nothing here over a plain QWidget (no toolbar/menu/
    status bar), and a plain top-level QWidget is the simpler base for a
    window that's really just "a header plus some tabs"."""

    def __init__(self, project_root: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scheduler Agents")
        self.resize(900, 640)

        memory = UserMemory()
        header = QWidget()
        header.setObjectName("HeaderBar")
        header_layout = QVBoxLayout(header)
        title = QLabel("Scheduler Agents")
        title.setObjectName("HeaderTitle")
        subtitle_text = memory.full_name
        if memory.email:
            subtitle_text += f"  ·  {memory.email}"
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("HeaderSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)

        self.history_tab = HistoryTab(project_root)
        self.calendar_tab = CalendarTab(project_root)
        self.dashboard_tab = DashboardTab(project_root, on_run_finished=self._refresh_other_tabs)

        tabs = QTabWidget()
        tabs.addTab(self.dashboard_tab, "Dashboard")
        tabs.addTab(self.history_tab, "History")
        tabs.addTab(self.calendar_tab, "Calendar")

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(tabs)

    def _refresh_other_tabs(self) -> None:
        self.history_tab.refresh()
        self.calendar_tab.refresh()
