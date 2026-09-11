from pathlib import Path

from scheduler_agents.main import _describe_email_source
from scheduler_agents.models.state import SchedulerFlowState


def test_reports_sample_file_when_gmail_disabled():
    state = SchedulerFlowState()
    sample_email = Path("sample_data/x.txt")
    result = _describe_email_source(state, gmail_enabled=False, sample_email=sample_email)
    assert result == f"sample file ({sample_email})"


def test_reports_live_gmail_when_a_real_email_was_actually_fetched():
    from scheduler_agents.hooks.event_hooks import record_hook

    state = SchedulerFlowState()
    record_hook(state, "after_receive_email", subject="s", sender="a@glocco.com", source="gmail")
    result = _describe_email_source(state, gmail_enabled=True, sample_email=Path("sample_data/x.txt"))
    assert result == "LIVE Gmail (read-only)"


def test_reports_fallback_reason_when_the_oauth_token_is_expired():
    from scheduler_agents.hooks.event_hooks import record_hook

    state = SchedulerFlowState()
    record_hook(state, "gmail_fetch_failed", error="invalid_grant: Token has been expired or revoked.")
    result = _describe_email_source(state, gmail_enabled=True, sample_email=Path("sample_data/x.txt"))
    assert "sample file" in result
    assert "invalid_grant" in result


def test_reports_fallback_reason_when_no_unread_email_matches_the_query():
    from scheduler_agents.hooks.event_hooks import record_hook

    state = SchedulerFlowState()
    record_hook(state, "gmail_fetch_found_nothing")
    result = _describe_email_source(state, gmail_enabled=True, sample_email=Path("sample_data/x.txt"))
    assert "sample file" in result
    assert "GMAIL_QUERY" in result
