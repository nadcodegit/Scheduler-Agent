from __future__ import annotations

import asyncio
from pathlib import Path

from scheduler_agents.flows.scheduler_flow import SchedulerFlow
from scheduler_agents.models.state import CoverageSlot, ScheduleEvent
from scheduler_agents.tools.coverage_tool import draft_coverage_reply, has_conflict, parse_coverage_request

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample_data"


def test_parse_coverage_request_extracts_single_slot():
    text = "We have an open slot: Sep 10, 2026, 14:00-16:00, English. Can you cover it?"

    slot = parse_coverage_request(text)

    assert slot is not None
    assert slot.date.isoformat() == "2026-09-10"
    assert slot.language == "English"


def test_parse_coverage_request_returns_none_when_unparseable():
    assert parse_coverage_request("Can you help cover a shift sometime next week?") is None


def test_has_conflict_true_for_overlapping_slot():
    slot = CoverageSlot(date="2026-09-10", start_time="14:00", end_time="16:00", language="English")
    busy = [ScheduleEvent(date="2026-09-10", start_time="15:00", end_time="17:00", language="English")]

    assert has_conflict(slot, busy) is True


def test_has_conflict_false_for_non_overlapping_slot():
    slot = CoverageSlot(date="2026-09-11", start_time="14:00", end_time="16:00", language="English")
    busy = [ScheduleEvent(date="2026-09-10", start_time="15:00", end_time="17:00", language="English")]

    assert has_conflict(slot, busy) is False


def test_draft_reply_mentions_decline_reason_on_conflict():
    from scheduler_agents.models.state import CoverageDecision

    slot = CoverageSlot(date="2026-09-10", start_time="14:00", end_time="16:00", language="English")

    draft = draft_coverage_reply(slot, CoverageDecision.DECLINE)

    assert "can't cover" in draft
    assert "2026-09-10" in draft


def test_scheduler_flow_declines_when_human_says_no():
    flow = SchedulerFlow(
        sample_email_path=SAMPLE_DATA / "sample_coverage_request_email.txt",
        busy_calendar_path=SAMPLE_DATA / "sample_busy_calendar.json",
        ask_user=lambda slot, conflict: False,
    )

    state = asyncio.run(flow.run_v1_async())

    assert state.email_type == "coverage_request"
    assert state.coverage_slot is not None
    assert state.coverage_conflict is True  # shown to the human, but doesn't decide for them
    assert state.coverage_decision == "decline"
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

    assert state.coverage_decision == "accept"
    assert "available to cover" in state.coverage_reply_draft
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
