"""Covers the trickiest part of the desktop app: FlowWorker's cross-thread
handshake for ask_coverage/ask_availability. A bug here wouldn't show up as
a test failure elsewhere -- it would show up as the GUI silently freezing
forever the first time a real coverage/availability email arrives, which is
exactly the scenario worth pinning down with a real (not mocked-away)
QThread + signal/slot run."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from scheduler_agents.models.state import EmailType, SchedulerFlowState  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeSlot:
    date = "2026-10-01"
    start_time = "09:00"
    end_time = "11:00"
    language = "Persian"


class _FakeFlow:
    """Stands in for a real SchedulerFlow: calls the injected ask_user/
    ask_availability exactly like handle_coverage_request/
    handle_availability_request do, without needing CrewAI or a real
    network call."""

    def __init__(self, ask_user=None, ask_availability=None, **_kwargs):
        self._ask_user = ask_user
        self._ask_availability = ask_availability

    async def run_v1_async(self) -> SchedulerFlowState:
        can_cover = self._ask_user(_FakeSlot(), False)
        statement = self._ask_availability("October")

        state = SchedulerFlowState()
        state.email_type = EmailType.COVERAGE_REQUEST
        state.coverage_needs_attention = False
        # Stash the answers where the test can see them came through correctly.
        state.memory_snapshot = {"can_cover": can_cover, "availability_statement": statement}
        return state


def _pump_until(condition, qapp, timeout_ms=5000):
    """Runs the Qt event loop in small slices until condition() is true or
    we give up -- needed because queued signal delivery to a main-thread
    slot only happens while the event loop is actually spinning."""

    import time

    deadline = time.monotonic() + timeout_ms / 1000
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert condition(), "timed out waiting for the worker -- likely a cross-thread deadlock"


def test_flow_worker_coverage_and_availability_handshake_does_not_deadlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp
):
    monkeypatch.setattr("desktop_app.flow_worker.build_flow", lambda *_a, **_k: _FakeFlow(**_k))

    from desktop_app.flow_worker import FlowWorker

    worker = FlowWorker(tmp_path)

    seen_conflict = []
    seen_period = []

    def on_ask_coverage(slot, conflict):
        seen_conflict.append(conflict)
        worker.provide_coverage_answer(True)

    def on_ask_availability(period):
        seen_period.append(period)
        worker.provide_availability_answer("Free all month")

    results: dict = {}
    worker.ask_coverage.connect(on_ask_coverage)
    worker.ask_availability.connect(on_ask_availability)
    worker.finished_ok.connect(lambda summary: results.update(ok=summary))
    worker.failed.connect(lambda error: results.update(error=error))

    worker.start()
    _pump_until(lambda: "ok" in results or "error" in results, qapp)

    assert "error" not in results, results.get("error")
    assert seen_conflict == [False]
    assert seen_period == ["October"]
    worker.wait(1000)
