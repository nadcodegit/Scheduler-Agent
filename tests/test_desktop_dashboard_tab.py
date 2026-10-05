from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_a_failed_gmail_login_is_reported_instead_of_no_new_email(
    tmp_path: Path, qapp, monkeypatch: pytest.MonkeyPatch
):
    from desktop_app.dashboard_tab import DashboardTab

    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: shown.append(args[1]))

    tab = DashboardTab(tmp_path, on_run_finished=lambda: None)
    tab._handle_finished({"source": "sample", "fetch_error": "invalid_grant: Token has been expired or revoked."})

    assert "login expired" in tab.status_label.text()
    assert "no new email" not in tab.status_label.text()
    assert shown == ["Gmail login expired"]


def test_no_fetch_error_still_reports_no_new_email(tmp_path: Path, qapp):
    from desktop_app.dashboard_tab import DashboardTab

    tab = DashboardTab(tmp_path, on_run_finished=lambda: None)
    tab._handle_finished({"source": "sample", "fetch_error": None})

    assert tab.status_label.text() == "Just checked -- no new email found."
