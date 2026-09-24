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


def test_summarize_run_distinguishes_a_failed_vision_call_from_an_unanswered_question():
    """A roster-image read failure and "no interactive terminal to ask you"
    are different situations -- the summary text must say which one, not
    collapse them into the same generic "needs your attention"."""

    state = _state_with_email(EmailType.SCHEDULE)
    state.schedule_needs_attention = True
    record_hook(state, "roster_image_parse_failed", error="rate_limited")

    summary = summarize_run(state)

    assert summary["needs_attention"] is True
    assert "roster image couldn't be read" in summary["summary"].lower()
    assert "rate_limited" in summary["summary"]


def test_summarize_run_includes_whichever_gmail_draft_id_is_set():
    state = _state_with_email(EmailType.AVAILABILITY_REQUEST)
    state.availability_gmail_draft_id = "draft-9"

    summary = summarize_run(state)

    assert summary["gmail_draft_id"] == "draft-9"


def test_summarize_run_builds_coverage_conversation_from_decisions():
    from datetime import date, time

    state = _state_with_email(EmailType.COVERAGE_REQUEST)
    slot = CoverageSlot(date=date(2026, 10, 1), start_time=time(9, 0), end_time=time(12, 0), language="Persian")
    state.coverage_decisions = [CoverageSlotDecision(slot=slot, conflict=False, decision=CoverageDecision.ACCEPT)]
    state.coverage_reply_draft = "Hi,\n\nI can cover it.\n\nBest,\nNadereh"

    summary = summarize_run(state)

    assert summary["conversation"]["exchanges"] == [
        {
            "question": "Can you cover 2026-10-01 09:00:00-12:00:00 (Persian)? "
            "Conflict with an existing commitment: no.",
            "answer": "Yes, I can cover it.",
        }
    ]
    assert summary["conversation"]["draft"] == state.coverage_reply_draft


def test_summarize_run_builds_availability_conversation_from_statement():
    state = _state_with_email(EmailType.AVAILABILITY_REQUEST)
    state.availability_period = "June"
    state.availability_statement = "Free weekdays 9-5."
    state.availability_reply_draft = "Hi,\n\nFree weekdays 9-5.\n\nBest,\nNadereh"

    summary = summarize_run(state)

    assert summary["conversation"]["exchanges"] == [
        {"question": "What's your availability for June?", "answer": "Free weekdays 9-5."}
    ]
    assert summary["conversation"]["draft"] == state.availability_reply_draft


def test_summarize_run_conversation_is_none_for_schedule_and_needs_attention():
    schedule_state = _state_with_email(EmailType.SCHEDULE)
    assert summarize_run(schedule_state)["conversation"] is None

    attention_state = _state_with_email(EmailType.COVERAGE_REQUEST)
    attention_state.coverage_needs_attention = True
    assert summarize_run(attention_state)["conversation"] is None


def test_summarize_run_includes_invoice_path_and_filename_in_summary():
    state = _state_with_email(EmailType.TIMESHEET)
    state.invoice_output_path = "C:\\outputs\\INVOICE September 2026.docx"

    summary = summarize_run(state)

    assert summary["invoice_output_path"] == "C:\\outputs\\INVOICE September 2026.docx"
    assert summary["summary"] == "Invoice generated: INVOICE September 2026.docx"


def test_summarize_run_invoice_path_is_none_for_other_email_types():
    state = _state_with_email(EmailType.SCHEDULE)
    summary = summarize_run(state)

    assert summary["invoice_output_path"] is None


def _gmail_state(email_type: EmailType, subject: str = "s", sender: str = "a@glocco.com") -> SchedulerFlowState:
    state = _state_with_email(email_type, subject=subject, sender=sender)
    record_hook(state, "after_receive_email", subject=subject, sender=sender, source="gmail")
    return state


def test_append_run_history_writes_one_json_line_per_call(tmp_path: Path):
    state1 = _gmail_state(EmailType.SCHEDULE, subject="first")
    state2 = _gmail_state(EmailType.SCHEDULE, subject="second")

    append_run_history(state1, tmp_path)
    history_path = append_run_history(state2, tmp_path)

    lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["subject"] == "first"
    assert json.loads(lines[1])["subject"] == "second"


def test_append_run_history_skips_a_sample_fallback_run(tmp_path: Path):
    """A scheduled check that finds nothing new falls back to the offline
    sample fixture -- that shouldn't be recorded as if it were real
    activity (it would bury genuine history under noise and show
    scheduler@example.com instead of a real address)."""

    state = _state_with_email(EmailType.SCHEDULE)  # no "gmail" hook -> source stays "unknown"

    result = append_run_history(state, tmp_path)

    assert result is None
    assert not (tmp_path / "run_history.jsonl").exists()


def test_append_run_history_records_a_real_run_after_skipping_a_sample_one(tmp_path: Path):
    skipped_state = _state_with_email(EmailType.SCHEDULE)
    real_state = _gmail_state(EmailType.SCHEDULE, subject="real")

    append_run_history(skipped_state, tmp_path)
    history_path = append_run_history(real_state, tmp_path)

    lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["subject"] == "real"
