from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHeaderView, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

COLUMNS = ["Timestamp", "Type", "Subject", "Sender", "Source", "Summary"]
ATTENTION_BG = QColor("#fbe4e4")
OK_BG = QColor("#e7f5e8")


class HistoryTab(QWidget):
    """A table of every past run, newest first -- reads outputs/run_history.jsonl,
    the same structured record main.py/flow_worker.py append to after every
    run (scheduled, CLI, or from this app's own "Check email now" button)."""

    def __init__(self, project_root: Path, parent=None):
        super().__init__(parent)
        self.project_root = project_root

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        layout = QVBoxLayout(self)
        layout.addWidget(refresh_button)
        layout.addWidget(self.table)

        self.refresh()

    def refresh(self) -> None:
        history_path = self.project_root / "outputs" / "run_history.jsonl"
        records: list[dict] = []
        if history_path.exists():
            for line in history_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        records.reverse()

        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            needs_attention = bool(record.get("needs_attention"))
            summary = record.get("summary") or ""
            if needs_attention:
                summary = f"[NEEDS ATTENTION] {summary}"
            values = [
                record.get("timestamp", ""),
                record.get("email_type") or "",
                record.get("subject") or "",
                record.get("sender") or "",
                record.get("source") or "",
                summary,
            ]
            row_color = ATTENTION_BG if needs_attention else OK_BG
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setBackground(row_color)
                self.table.setItem(row, col, item)
