from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from scheduler_agents.main import build_flow
from scheduler_agents.output_writer import append_run_history, summarize_run, write_flow_outputs


class FlowWorker(QThread):
    """Runs one SchedulerFlow check off the GUI thread, so a live Gmail/LLM
    call never freezes the window.

    V2/V3 still need to ask the human a real question mid-run. Rather than
    reinventing that, this reuses SchedulerFlow's existing ask_user/
    ask_availability injection points (the same ones the CLI's input()-based
    prompts use) -- just backed by Qt dialogs on the main thread instead.
    ask_coverage/ask_availability are emitted (Qt auto-queues delivery to
    whatever thread the connected slot lives on) and this thread then
    blocks on a plain threading.Event until the main thread's dialog calls
    provide_coverage_answer()/provide_availability_answer() and sets it --
    the GUI event loop keeps running the whole time, only this worker
    thread actually blocks.
    """

    ask_coverage = Signal(object, bool)
    ask_availability = Signal(object)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, project_root: Path, parent=None):
        super().__init__(parent)
        self.project_root = project_root
        self._answer_event = threading.Event()
        self._coverage_answer: bool = False
        self._availability_answer: str = ""

    def provide_coverage_answer(self, can_cover: bool) -> None:
        self._coverage_answer = can_cover
        self._answer_event.set()

    def provide_availability_answer(self, statement: str) -> None:
        self._availability_answer = statement
        self._answer_event.set()

    def _ask_user(self, slot, conflict: bool) -> bool:
        self._answer_event.clear()
        self.ask_coverage.emit(slot, conflict)
        self._answer_event.wait()
        return self._coverage_answer

    def _ask_availability(self, period) -> str:
        self._answer_event.clear()
        self.ask_availability.emit(period)
        self._answer_event.wait()
        return self._availability_answer

    def run(self) -> None:
        try:
            flow = build_flow(
                self.project_root,
                ask_user=self._ask_user,
                ask_availability=self._ask_availability,
            )
            state = asyncio.run(flow.run_v1_async())
            write_flow_outputs(state, self.project_root / "outputs")
            append_run_history(state, self.project_root / "outputs")
            self.finished_ok.emit(summarize_run(state))
        except Exception as exc:  # a bad run must never crash the GUI
            self.failed.emit(str(exc))
