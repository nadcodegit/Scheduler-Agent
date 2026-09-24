from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop_app.flow_worker import FlowWorker


def _set_status_style(label: QLabel, status: str) -> None:
    """Drives the QLabel[status="..."] rules in style.qss -- Qt only
    re-evaluates a dynamic property's stylesheet rule after an explicit
    unpolish/polish, a plain setProperty() alone has no visible effect."""

    label.setProperty("status", status)
    label.style().unpolish(label)
    label.style().polish(label)


class DashboardTab(QWidget):
    """Status at a glance, a button to check email right now, and -- when a
    V2/V3 email needs a real answer -- the actual dialog to answer it in,
    instead of sending the human back to a terminal.
    """

    def __init__(self, project_root: Path, on_run_finished, parent=None):
        super().__init__(parent)
        self.project_root = project_root
        self._on_run_finished = on_run_finished
        self._worker: FlowWorker | None = None

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        self.check_button = QPushButton("Check email now")
        self.check_button.clicked.connect(self.run_check)

        self.open_invoice_button = QPushButton("Open invoice")
        self.open_invoice_button.hide()
        self.open_invoice_button.clicked.connect(self._open_last_invoice)
        self._last_invoice_path: str | None = None

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)

        button_row = QHBoxLayout()
        button_row.addWidget(self.check_button)
        button_row.addWidget(self.open_invoice_button)
        button_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(button_row)
        layout.addWidget(QLabel("Last result:"))
        layout.addWidget(self.log_view)

        self.refresh_status()

    def refresh_status(self) -> None:
        history_path = self.project_root / "outputs" / "run_history.jsonl"
        if not history_path.exists():
            self.status_label.setText("No runs yet -- click \"Check email now\" to run the first one.")
            _set_status_style(self.status_label, "idle")
            self.open_invoice_button.hide()
            return

        lines = history_path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            self.status_label.setText("No runs yet -- click \"Check email now\" to run the first one.")
            _set_status_style(self.status_label, "idle")
            self.open_invoice_button.hide()
            return

        last = json.loads(lines[-1])
        needs_attention = bool(last.get("needs_attention"))
        attention = " -- NEEDS YOUR ATTENTION" if needs_attention else " -- all clear"
        status_text = (
            f"Last real activity: {last.get('timestamp', '?')}{attention}\n"
            f"Email: {last.get('subject') or '(none)'} ({last.get('email_type') or 'n/a'}) "
            f"from {last.get('sender') or 'n/a'}"
        )
        invoice_path = last.get("invoice_output_path")
        if invoice_path:
            status_text += f"\n✓ Invoice generated: {Path(invoice_path).name}"
        self.status_label.setText(status_text)
        _set_status_style(self.status_label, "attention" if needs_attention else "ok")
        self.log_view.setPlainText(json.dumps(last, indent=2, ensure_ascii=False))

        self._last_invoice_path = invoice_path
        self.open_invoice_button.setVisible(bool(invoice_path))

    def _open_last_invoice(self) -> None:
        if self._last_invoice_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_invoice_path))

    def run_check(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        self.check_button.setEnabled(False)
        self.status_label.setText("Checking email...")
        _set_status_style(self.status_label, "idle")

        self._worker = FlowWorker(self.project_root)
        self._worker.ask_coverage.connect(self._handle_ask_coverage)
        self._worker.ask_availability.connect(self._handle_ask_availability)
        self._worker.ask_confirm_roster.connect(self._handle_ask_confirm_roster)
        self._worker.finished_ok.connect(self._handle_finished)
        self._worker.failed.connect(self._handle_failed)
        self._worker.start()

    def _handle_ask_coverage(self, slot, conflict: bool) -> None:
        conflict_note = "\n\nWarning: this overlaps a slot you already have." if conflict else ""
        answer = QMessageBox.question(
            self,
            "Coverage request",
            f"Can you cover this shift?\n\n"
            f"{slot.date} {slot.start_time}-{slot.end_time} ({slot.language or 'language n/a'})"
            f"{conflict_note}",
        )
        self._worker.provide_coverage_answer(answer == QMessageBox.StandardButton.Yes)

    def _handle_ask_availability(self, period) -> None:
        text, _ok = QInputDialog.getMultiLineText(
            self,
            "Availability request",
            f"What's your availability for {period or 'the requested period'}?",
        )
        self._worker.provide_availability_answer(text)

    def _handle_ask_confirm_roster(self, events, roster_image_path) -> None:
        # Opens the actual source image on screen before asking -- a vision
        # model reading a roster grid has proven, in real testing, able to
        # confidently misread which hour column a mark falls under, so this
        # confirmation is only meaningful if she can see the real image
        # right next to the question, not just a list of numbers.
        if roster_image_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(roster_image_path)))

        by_date: dict[str, list[str]] = {}
        for event in events:
            by_date.setdefault(str(event.date), []).append(f"{event.start_time}-{event.end_time}")
        lines = [f"{date}: {', '.join(times)}" for date, times in sorted(by_date.items())]
        preview = "\n".join(lines[:15])
        if len(lines) > 15:
            preview += f"\n... and {len(lines) - 15} more day(s)"

        answer = QMessageBox.question(
            self,
            "Confirm roster events",
            f"Extracted {len(events)} event(s) from a roster screenshot -- "
            f"the real image just opened for you to compare.\n\n{preview}\n\n"
            "Does this actually match the real roster?",
        )
        self._worker.provide_roster_confirm_answer(answer == QMessageBox.StandardButton.Yes)

    def _handle_finished(self, summary: dict) -> None:
        self.check_button.setEnabled(True)
        if summary.get("source") != "gmail":
            # Nothing was written to history (append_run_history skips a
            # sample-fallback run on purpose -- see its docstring), so
            # refresh_status() would just redisplay the last real entry
            # with no sign a check happened at all. Give real-time feedback
            # from the signal itself instead, without touching History/Calendar.
            self.status_label.setText("Just checked -- no new email found.")
            _set_status_style(self.status_label, "idle")
            return
        self.refresh_status()
        self._on_run_finished()
        if summary.get("needs_attention"):
            if summary.get("email_type") == "schedule":
                # In this app, a coverage/availability question always got a
                # real dialog above (the GUI never has no one to ask) -- so
                # for a schedule email specifically, needs_attention here
                # means the roster image itself couldn't be read this run
                # (a transient provider error), not an unanswered question.
                QMessageBox.information(
                    self,
                    "Action needed",
                    "The roster image couldn't be read this run (a live provider "
                    "error, likely transient). Check History for the real error, "
                    "then click \"Check email now\" again to retry.",
                )
            else:
                QMessageBox.information(
                    self,
                    "Action needed",
                    "This email needed an answer but no interactive prompt was available. "
                    "Check the History tab and re-run.",
                )
        elif summary.get("invoice_output_path"):
            name = Path(summary["invoice_output_path"]).name
            answer = QMessageBox.information(
                self,
                "Invoice generated",
                f"Done -- created \"{name}\".\n\nOpen it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._open_last_invoice()

    def _handle_failed(self, error: str) -> None:
        self.check_button.setEnabled(True)
        self.status_label.setText(f"Check failed: {error}")
        _set_status_style(self.status_label, "attention")
        QMessageBox.critical(self, "Check failed", error)
