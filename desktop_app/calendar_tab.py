from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import QCalendarWidget, QHBoxLayout, QListWidget, QPushButton, QVBoxLayout, QWidget

from scheduler_agents.tools.schedule_store import load_approved_schedule


class CalendarTab(QWidget):
    """A real calendar view of outputs/approved_schedule.json -- the
    project's own persistent source of truth for committed work (V1's
    approved monthly schedules plus every V2 coverage slot actually
    accepted), not the transient outputs/schedule.ics, which only ever
    holds whatever a single run's calendar_events happened to be.
    """

    def __init__(self, project_root: Path, parent=None):
        super().__init__(parent)
        self.project_root = project_root
        self._events_by_date: dict[date, list] = defaultdict(list)

        self.calendar = QCalendarWidget()
        self.calendar.clicked.connect(self._show_events_for_date)

        self.event_list = QListWidget()

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        side = QVBoxLayout()
        side.addWidget(refresh_button)
        side.addWidget(self.event_list)

        layout = QHBoxLayout(self)
        layout.addWidget(self.calendar, 2)
        layout.addLayout(side, 1)

        self.refresh()

    def refresh(self) -> None:
        approved_schedule_path = self.project_root / "outputs" / "approved_schedule.json"
        events = load_approved_schedule(approved_schedule_path)

        self._events_by_date = defaultdict(list)
        for event in events:
            self._events_by_date[event.date].append(event)

        highlight = QTextCharFormat()
        highlight.setBackground(QColor("#2f7d32"))
        highlight.setForeground(QColor("#ffffff"))
        default_format = QTextCharFormat()

        self.calendar.setDateTextFormat(QDate(), default_format)  # reset all
        for event_date in self._events_by_date:
            self.calendar.setDateTextFormat(
                QDate(event_date.year, event_date.month, event_date.day), highlight
            )

        self._show_events_for_date(self.calendar.selectedDate())

    def _show_events_for_date(self, qdate: QDate) -> None:
        picked = date(qdate.year(), qdate.month(), qdate.day())
        self.event_list.clear()
        for event in sorted(self._events_by_date.get(picked, []), key=lambda e: e.start_time):
            self.event_list.addItem(
                f"{event.start_time}-{event.end_time}  {event.title} ({event.language or 'n/a'})"
            )
        if not self._events_by_date.get(picked):
            self.event_list.addItem("(no events this day)")
