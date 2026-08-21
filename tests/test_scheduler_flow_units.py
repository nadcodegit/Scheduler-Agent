from __future__ import annotations

import asyncio

from scheduler_agents.flows.scheduler_flow import SchedulerFlow
from scheduler_agents.guardrails.schedule_guardrails import validate_schedule_events
from scheduler_agents.tools.schedule_parser_tool import parse_schedule_text


def test_parse_schedule_text_extracts_events():
    text = "Sep 1, 2026, 09:00-12:00, English\nSep 4, 2026, 10:00-14:00, Turkish"

    events = parse_schedule_text(text)

    assert len(events) == 2
    assert events[0].date.isoformat() == "2026-09-01"
    assert events[0].language == "English"


def test_guardrail_rejects_missing_events():
    assert validate_schedule_events([]) == ["No schedule events were extracted."]


def test_scheduler_flow_v1_creates_calendar_payloads():
    flow = SchedulerFlow()

    state = asyncio.run(flow.run_v1_async())

    assert state.email_type == "schedule"
    assert len(state.extracted_events) == 1
    assert state.validation_errors == []
    assert len(state.calendar_events) == 1
    assert state.calendar_events[0]["start"]["timeZone"] == "Asia/Yerevan"
    assert any(hook.name == "after_create_calendar_events" for hook in state.hooks)

