from __future__ import annotations

import json
from pathlib import Path

from scheduler_agents.hooks.event_hooks import record_hook
from scheduler_agents.models.state import (
    CoverageDecision,
    CoverageSlot,
    CoverageSlotDecision,
    EmailInput,
    EmailType,
    SchedulerFlowState,
)
from scheduler_agents.output_writer import append_run_history, summarize_run


def _state_with_email(email_type: EmailType, subject: str = "s", sender: str = "a@glocco.com") -> SchedulerFlowState:
    state = SchedulerFlowState()
    state.email = EmailInput(subject=subject, sender=sender, body="body")
    state.email_type = email_type
    return state


def test_summarize_run_reports_gmail_source_from_hooks():
    state = _state_with_email(EmailType.SCHEDULE)
    record_hook(state, "after_receive_email", subject="s", sender="a@glocco.com", source="gmail")

    summary = summarize_run(state)

    assert summary["source"] == "gmail"
    assert summary["fetch_error"] is None


def test_summarize_run_reports_fetch_error_when_gmail_fell_back():
    state = _state_with_email(EmailType.SCHEDULE)
    record_hook(state, "gmail_fetch_failed", error="invalid_grant: Token has been expired or revoked.")

    summary = summarize_run(state)

    assert summary["source"] == "unknown"
    assert "invalid_grant" in summary["fetch_error"]


def test_summarize_run_schedule_counts_events_and_errors():
    from scheduler_agents.models.state import ScheduleEvent
    from datetime import date, time

    state = _state_with_email(EmailType.SCHEDULE)
    state.extracted_events = [
        ScheduleEvent(date=date(2026, 10, 1), start_time=time(9, 0), end_time=time(12, 0))
    ]
    state.validation_errors = []

    summary = summarize_run(state)

    assert "1 event(s)" in summary["summary"]
    assert "0 validation error(s)" in summary["summary"]


def test_summarize_run_coverage_counts_accepted_slots():
    from datetime import date, time

    state = _state_with_email(EmailType.COVERAGE_REQUEST)
    slot = CoverageSlot(date=date(2026, 10, 1), start_time=time(9, 0), end_time=time(12, 0))
    state.coverage_decisions = [
        CoverageSlotDecision(slot=slot, conflict=False, decision=CoverageDecision.ACCEPT),
        CoverageSlotDecision(slot=slot, conflict=True, decision=CoverageDecision.DECLINE),
    ]

    summary = summarize_run(state)

    assert summary["summary"] == "2 slot(s) decided, 1 accepted."


def test_summarize_run_flags_needs_attention_over_any_other_summary():
    state = _state_with_email(EmailType.COVERAGE_REQUEST)
    state.coverage_needs_attention = True

    summary = summarize_run(state)

    assert summary["needs_attention"] is True
    assert "attention" in summary["summary"]


def test_summarize_run_includes_whichever_gmail_draft_id_is_set():
    state = _state_with_email(EmailType.AVAILABILITY_REQUEST)
    state.availability_gmail_draft_id = "draft-9"

    summary = summarize_run(state)

    assert summary["gmail_draft_id"] == "draft-9"


def test_append_run_history_writes_one_json_line_per_call(tmp_path: Path):
    state1 = _state_with_email(EmailType.SCHEDULE, subject="first")
    state2 = _state_with_email(EmailType.SCHEDULE, subject="second")

    append_run_history(state1, tmp_path)
    history_path = append_run_history(state2, tmp_path)

    lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["subject"] == "first"
    assert json.loads(lines[1])["subject"] == "second"
