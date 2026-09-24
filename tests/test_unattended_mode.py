from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scheduler_agents.flows.scheduler_flow import (
    NeedsHumanAttention,
    SchedulerFlow,
    ask_availability_via_cli,
    ask_user_can_cover_via_cli,
    confirm_roster_events_via_cli,
)
from scheduler_agents.models.state import CoverageSlot, ScheduleEvent

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample_data"


def test_ask_user_can_cover_via_cli_raises_without_a_terminal():
    """A scheduled/unattended run (Windows Task Scheduler, no console
    attached) must never block on input() forever or crash with a raw
    EOFError -- pytest's own stdin isn't a real terminal either, so this
    exercises the same path without needing to fake anything."""

    slot = CoverageSlot(date="2026-09-10", start_time="14:00", end_time="16:00", language="Persian")

    with pytest.raises(NeedsHumanAttention):
        ask_user_can_cover_via_cli(slot, conflict=False)


def test_ask_availability_via_cli_raises_without_a_terminal():
    with pytest.raises(NeedsHumanAttention):
        ask_availability_via_cli("June")


def test_confirm_roster_events_via_cli_raises_without_a_terminal():
    events = [ScheduleEvent(date="2026-10-01", start_time="09:00", end_time="10:00", source="roster_image")]

    with pytest.raises(NeedsHumanAttention):
        confirm_roster_events_via_cli(events, None)


def test_coverage_request_flags_needs_attention_instead_of_crashing(monkeypatch: pytest.MonkeyPatch):
    """The default (non-injected) ask_user, run with no interactive
    terminal, must leave the flow in a safe "needs a human" state --
    slots extracted, nothing decided, nothing drafted, nothing crashed."""

    monkeypatch.setattr("scheduler_agents.flows.scheduler_flow.llm_is_configured", lambda: False)

    flow = SchedulerFlow(sample_email_path=SAMPLE_DATA / "sample_coverage_request_email.txt")
    state = asyncio.run(flow.run_v1_async())

    assert state.email_type == "coverage_request"
    assert len(state.coverage_slots) > 0  # extraction still ran fine
    assert state.coverage_needs_attention is True
    assert state.coverage_decisions == []
    assert state.coverage_reply_draft is None
    assert state.calendar_events == []  # nothing guessed as accepted


def test_availability_request_flags_needs_attention_instead_of_crashing():
    flow = SchedulerFlow(sample_email_path=SAMPLE_DATA / "sample_availability_request_email.txt")

    state = asyncio.run(flow.run_v1_async())

    assert state.email_type == "availability_request"
    assert state.availability_needs_attention is True
    assert state.availability_statement is None
    assert state.availability_reply_draft is None
