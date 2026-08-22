from __future__ import annotations

import asyncio
from pathlib import Path

from scheduler_agents.flows.scheduler_flow import SchedulerFlow
from scheduler_agents.guardrails.schedule_guardrails import validate_schedule_events
from scheduler_agents.models.state import EmailInput
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


def test_regex_classifies_roster_as_schedule():
    # Real vendor email: calls it a "roster", never says "schedule".
    flow = SchedulerFlow()
    email = EmailInput(
        subject="May Roster",
        sender="scheduler@example.com",
        body="I'm sending the May roster, please review it and let me know if the slots work.",
    )

    flow._classify_with_regex(email)

    assert flow.state.email_type == "schedule"


def test_regex_does_not_misclassify_time_of_day_hours_as_timesheet():
    # Real vendor email said "do not login during these hours" (a time-of-day
    # reference), which used to trip the bare \bhours\b timesheet check.
    flow = SchedulerFlow()
    email = EmailInput(
        subject="May Roster",
        sender="scheduler@example.com",
        body="If your slots were reduced, do not login during these hours.",
    )

    flow._classify_with_regex(email)

    assert flow.state.email_type != "timesheet"


def test_scheduler_flow_handles_unparseable_real_roster_email(tmp_path: Path):
    # The real roster's actual data is an image/table attachment; the email
    # body itself has zero parseable dates. The system should say so clearly
    # rather than silently doing nothing or fabricating events.
    email_path = tmp_path / "roster.txt"
    email_path.write_text(
        "Subject: May Roster\n"
        "From: scheduler@example.com\n\n"
        "I'm sending the May roster, please review it and let me know if the slots work for you.\n",
        encoding="utf-8",
    )
    flow = SchedulerFlow(sample_email_path=email_path)

    state = asyncio.run(flow.run_v1_async())

    assert state.email_type == "schedule"
    assert state.extracted_events == []
    assert state.validation_errors == ["No schedule events were extracted."]
    assert state.calendar_events == []

