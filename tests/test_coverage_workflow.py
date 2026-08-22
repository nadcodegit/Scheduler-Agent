from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scheduler_agents.flows.scheduler_flow import SchedulerFlow
from scheduler_agents.models.state import CoverageDecision, CoverageSlot, CoverageSlotDecision, ScheduleEvent
from scheduler_agents.tools.coverage_tool import (
    draft_coverage_reply_multi,
    extract_coverage_slots_via_llm,
    has_conflict,
    parse_coverage_request_regex,
)

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample_data"


def test_parse_coverage_request_regex_extracts_single_slot():
    text = "We have an open slot: Sep 10, 2026, 14:00-16:00, English. Can you cover it?"

    slots = parse_coverage_request_regex(text)

    assert len(slots) == 1
    assert slots[0].date.isoformat() == "2026-09-10"
    assert slots[0].language == "English"


def test_parse_coverage_request_regex_returns_empty_when_unparseable():
    assert parse_coverage_request_regex("Can you help cover a shift sometime next week?") == []


def test_has_conflict_true_for_overlapping_slot():
    slot = CoverageSlot(date="2026-09-10", start_time="14:00", end_time="16:00", language="English")
    busy = [ScheduleEvent(date="2026-09-10", start_time="15:00", end_time="17:00", language="English")]

    assert has_conflict(slot, busy) is True


def test_has_conflict_false_for_non_overlapping_slot():
    slot = CoverageSlot(date="2026-09-11", start_time="14:00", end_time="16:00", language="English")
    busy = [ScheduleEvent(date="2026-09-10", start_time="15:00", end_time="17:00", language="English")]

    assert has_conflict(slot, busy) is False


def test_draft_reply_multi_lists_accepted_and_declined_separately():
    accepted_slot = CoverageSlot(date="2026-09-10", start_time="14:00", end_time="16:00")
    declined_slot = CoverageSlot(date="2026-09-11", start_time="09:00", end_time="10:00")
    decisions = [
        CoverageSlotDecision(slot=accepted_slot, conflict=False, decision=CoverageDecision.ACCEPT),
        CoverageSlotDecision(slot=declined_slot, conflict=True, decision=CoverageDecision.DECLINE),
    ]

    draft = draft_coverage_reply_multi(decisions, unstructured_note=None)

    assert "I can cover" in draft
    assert "2026-09-10" in draft
    assert "I'm unable to cover" in draft
    assert "2026-09-11" in draft


def test_draft_reply_multi_includes_unstructured_note():
    slot = CoverageSlot(date="2026-09-10", start_time="14:00", end_time="16:00")
    decisions = [CoverageSlotDecision(slot=slot, conflict=False, decision=CoverageDecision.ACCEPT)]

    draft = draft_coverage_reply_multi(decisions, unstructured_note="Mon-Wed 11am-1pm all month")

    assert "Mon-Wed 11am-1pm all month" in draft


def test_extract_coverage_slots_via_llm_raises_without_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MODEL", raising=False)

    with pytest.raises(RuntimeError):
        extract_coverage_slots_via_llm("some email text")


def test_scheduler_flow_declines_when_human_says_no():
    flow = SchedulerFlow(
        sample_email_path=SAMPLE_DATA / "sample_coverage_request_email.txt",
        busy_calendar_path=SAMPLE_DATA / "sample_busy_calendar.json",
        ask_user=lambda slot, conflict: False,
    )

    state = asyncio.run(flow.run_v1_async())

    assert state.email_type == "coverage_request"
    assert len(state.coverage_slots) == 1
    assert state.coverage_decisions[0].conflict is True  # shown to the human, but doesn't decide for them
    assert state.coverage_decisions[0].decision == "decline"
    assert state.coverage_approval_required is True
    assert state.coverage_reply_draft is not None
    assert state.calendar_events == []
    assert any(hook.name == "after_handle_coverage_request" for hook in state.hooks)


def test_scheduler_flow_accepts_and_updates_calendar_when_human_says_yes():
    flow = SchedulerFlow(
        sample_email_path=SAMPLE_DATA / "sample_coverage_request_email.txt",
        busy_calendar_path=SAMPLE_DATA / "sample_busy_calendar.json",
        ask_user=lambda slot, conflict: True,
    )

    state = asyncio.run(flow.run_v1_async())

    assert state.coverage_decisions[0].decision == "accept"
    assert "I can cover" in state.coverage_reply_draft
    assert len(state.calendar_events) == 1
    assert state.calendar_events[0]["start"]["dateTime"].startswith("2026-09-10T14:00")


def test_ask_user_receives_the_conflict_flag():
    seen: dict = {}

    def fake_ask_user(slot, conflict):
        seen["conflict"] = conflict
        return False

    flow = SchedulerFlow(
        sample_email_path=SAMPLE_DATA / "sample_coverage_request_email.txt",
        busy_calendar_path=SAMPLE_DATA / "sample_busy_calendar.json",
        ask_user=fake_ask_user,
    )

    asyncio.run(flow.run_v1_async())

    assert seen["conflict"] is True


def test_scheduler_flow_handles_multiple_slots_via_mocked_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Mock llm_is_configured() directly rather than setting real-looking env
    # vars: those would also make classify_email attempt a real (failing)
    # network call, which is exactly the offline-test guarantee conftest.py
    # exists to protect.
    monkeypatch.setattr("scheduler_agents.flows.scheduler_flow.llm_is_configured", lambda: True)

    email_path = tmp_path / "coverage.txt"
    email_path.write_text(
        "Subject: Coverage needed\nFrom: scheduler@example.com\n\n"
        "We need help covering: April 8: 3-4pm and April 9: 10am-11am.\n",
        encoding="utf-8",
    )

    def fake_extract(email_text):
        return (
            [
                CoverageSlot(date="2026-04-08", start_time="15:00", end_time="16:00"),
                CoverageSlot(date="2026-04-09", start_time="10:00", end_time="11:00"),
            ],
            "Mon-Wed 11am-1pm all month",
        )

    monkeypatch.setattr(
        "scheduler_agents.flows.scheduler_flow.extract_coverage_slots_via_llm", fake_extract
    )

    decisions_seen = []

    def fake_ask_user(slot, conflict):
        accept = slot.date.isoformat() == "2026-04-08"
        decisions_seen.append((slot.date.isoformat(), accept))
        return accept

    flow = SchedulerFlow(sample_email_path=email_path, ask_user=fake_ask_user)
    state = asyncio.run(flow.run_v1_async())

    assert len(state.coverage_slots) == 2
    assert len(state.coverage_decisions) == 2
    assert state.coverage_unstructured_note == "Mon-Wed 11am-1pm all month"
    assert len(state.calendar_events) == 1  # only the April 8 slot was accepted
    assert "Mon-Wed 11am-1pm all month" in state.coverage_reply_draft
