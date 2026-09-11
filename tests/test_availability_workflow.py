from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scheduler_agents.flows.scheduler_flow import SchedulerFlow
from scheduler_agents.models.state import EmailInput
from scheduler_agents.tools.availability_tool import draft_availability_reply, extract_requested_period

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample_data"


def test_extract_requested_period_finds_month_name():
    text = "Could you please let me know your June availability?"

    assert extract_requested_period(text) == "June"


def test_extract_requested_period_includes_year_when_present():
    text = "Please send your availability for October 2026."

    assert extract_requested_period(text) == "October 2026"


def test_extract_requested_period_returns_none_when_no_month_mentioned():
    assert extract_requested_period("Please let me know your availability soon.") is None


def test_draft_availability_reply_uses_fallback_when_period_unknown():
    draft = draft_availability_reply(None, "I'm free every weekday.")

    assert "the requested period" in draft
    assert "I'm free every weekday." in draft


def test_scheduler_flow_drafts_availability_reply():
    flow = SchedulerFlow(
        sample_email_path=SAMPLE_DATA / "sample_availability_request_email.txt",
        ask_availability=lambda period: "Available weekdays 9am-5pm, off the last week of the month.",
    )

    state = asyncio.run(flow.run_v1_async())

    assert state.email_type == "availability_request"
    assert state.availability_period == "June"
    assert state.availability_statement == "Available weekdays 9am-5pm, off the last week of the month."
    assert "June" in state.availability_reply_draft
    assert "Available weekdays 9am-5pm" in state.availability_reply_draft
    assert state.availability_approval_required is True
    assert any(hook.name == "after_handle_availability_request" for hook in state.hooks)


def test_availability_request_creates_a_real_gmail_draft_for_a_live_threaded_email(
    monkeypatch: pytest.MonkeyPatch,
):
    """Mirrors handle_coverage_request's Gmail-draft behavior: only for a
    real live email (thread_id set), never for a sample-file run, and it
    must never call the send endpoint -- create_draft_reply is the only
    write this integration is allowed to perform."""

    create_calls: list[dict] = []

    def fake_create_draft_reply(**kwargs):
        create_calls.append(kwargs)
        return "draft-42"

    monkeypatch.setattr("scheduler_agents.flows.scheduler_flow.is_live_gmail_enabled", lambda: True)
    monkeypatch.setattr("scheduler_agents.flows.scheduler_flow.create_draft_reply", fake_create_draft_reply)

    flow = SchedulerFlow(ask_availability=lambda period: "Available weekdays 9am-5pm.")
    flow.state.email = EmailInput(
        subject="Availability for October",
        sender="dominika@glocco.com",
        body="Please send your availability for October.",
        thread_id="thread-oct",
        rfc_message_id="<orig@mail.gmail.com>",
    )

    flow.handle_availability_request()

    assert flow.state.availability_gmail_draft_id == "draft-42"
    assert create_calls == [
        {
            "to": "dominika@glocco.com",
            "subject": "Availability for October",
            "body": flow.state.availability_reply_draft,
            "thread_id": "thread-oct",
            "in_reply_to": "<orig@mail.gmail.com>",
        }
    ]
    assert any(hook.name == "availability_gmail_draft_created" for hook in flow.state.hooks)


def test_availability_request_skips_gmail_draft_for_a_sample_file_email(monkeypatch: pytest.MonkeyPatch):
    create_calls: list[dict] = []
    monkeypatch.setattr("scheduler_agents.flows.scheduler_flow.is_live_gmail_enabled", lambda: True)
    monkeypatch.setattr(
        "scheduler_agents.flows.scheduler_flow.create_draft_reply",
        lambda **kwargs: create_calls.append(kwargs),
    )

    flow = SchedulerFlow(
        sample_email_path=SAMPLE_DATA / "sample_availability_request_email.txt",
        ask_availability=lambda period: "Free all month.",
    )

    asyncio.run(flow.run_v1_async())

    assert create_calls == []
    assert flow.state.availability_gmail_draft_id is None


def test_ask_availability_receives_extracted_period():
    seen: dict = {}

    def fake_ask_availability(period):
        seen["period"] = period
        return "Free all month."

    flow = SchedulerFlow(
        sample_email_path=SAMPLE_DATA / "sample_availability_request_email.txt",
        ask_availability=fake_ask_availability,
    )

    asyncio.run(flow.run_v1_async())

    assert seen["period"] == "June"
