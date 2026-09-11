from __future__ import annotations

import html
import json
from pathlib import Path

from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QPushButton, QTextBrowser, QVBoxLayout, QWidget


class ConversationsTab(QWidget):
    """Every V2/V3 back-and-forth (what the agent asked, what she answered)
    plus the resulting email draft, rendered like an actual conversation +
    email preview rather than raw JSON. Reads the same outputs/run_history.jsonl
    as the History tab, just the runs that have a "conversation" -- schedule
    and timesheet runs never do, and neither does a needs_attention run that
    never got an answer.
    """

    def __init__(self, project_root: Path, parent=None):
        super().__init__(parent)
        self.project_root = project_root
        self._records: list[dict] = []

        self.conversation_list = QListWidget()
        self.conversation_list.currentRowChanged.connect(self._show_conversation)

        self.transcript_view = QTextBrowser()
        self.transcript_view.setOpenExternalLinks(False)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        left = QVBoxLayout()
        left.addWidget(refresh_button)
        left.addWidget(self.conversation_list)

        layout = QHBoxLayout(self)
        left_widget = QWidget()
        left_widget.setLayout(left)
        layout.addWidget(left_widget, 1)
        layout.addWidget(self.transcript_view, 2)

        self.refresh()

    def refresh(self) -> None:
        history_path = self.project_root / "outputs" / "run_history.jsonl"
        records: list[dict] = []
        if history_path.exists():
            for line in history_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if record.get("conversation"):
                    records.append(record)
        records.reverse()
        self._records = records

        self.conversation_list.clear()
        for record in records:
            label = f"{record.get('timestamp', '?')[:16]}  {record.get('subject') or '(no subject)'}"
            self.conversation_list.addItem(QListWidgetItem(label))

        if records:
            self.conversation_list.setCurrentRow(0)
        else:
            self.transcript_view.setHtml(
                "<p style='color:#8a8399;'>No completed conversations yet -- V2/V3 runs that were "
                "actually answered will show up here, with the resulting draft.</p>"
            )

    def _show_conversation(self, row: int) -> None:
        if row < 0 or row >= len(self._records):
            return
        record = self._records[row]
        conversation = record.get("conversation") or {}

        bubbles = []
        for exchange in conversation.get("exchanges", []):
            bubbles.append(
                f"""
                <div style="margin:8px 0;">
                  <div style="background:#ece7f6; color:#26232e; padding:8px 12px;
                              border-radius:10px; max-width:80%; display:inline-block;">
                    <b>Agent:</b> {html.escape(exchange.get('question', ''))}
                  </div>
                </div>
                <div style="margin:8px 0; text-align:right;">
                  <div style="background:#6f42c1; color:white; padding:8px 12px;
                              border-radius:10px; max-width:80%; display:inline-block;">
                    <b>You:</b> {html.escape(exchange.get('answer', ''))}
                  </div>
                </div>
                """
            )

        draft = conversation.get("draft") or "(no draft)"
        preview = f"""
        <h3>Conversation</h3>
        {''.join(bubbles) or '<p><i>No exchanges recorded.</i></p>'}
        <h3 style="margin-top:24px;">Email preview</h3>
        <div style="border:1px solid #d9d3ea; border-radius:6px; padding:12px; background:white;">
          <p><b>To:</b> {html.escape(record.get('sender') or '')}</p>
          <p><b>Subject:</b> {html.escape(record.get('subject') or '')}</p>
          <hr style="border:none; border-top:1px solid #d9d3ea;">
          <pre style="white-space:pre-wrap; font-family:inherit; margin:0;">{html.escape(draft)}</pre>
        </div>
        """
        if record.get("gmail_draft_id"):
            preview += (
                "<p style='color:#2f7d32; margin-top:8px;'>"
                "&#10003; Also saved as a real Gmail draft -- open Gmail to review and send it.</p>"
            )
        else:
            preview += (
                "<p style='color:#8a8399; margin-top:8px;'>"
                "Not filed as a Gmail draft (sample-file run, or live Gmail wasn't enabled/threaded).</p>"
            )

        self.transcript_view.setHtml(preview)
