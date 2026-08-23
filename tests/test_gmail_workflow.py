from __future__ import annotations

from pathlib import Path

import pytest

from scheduler_agents.tools.gmail_tool import fetch_latest_email, is_live_gmail_enabled


def test_is_live_gmail_enabled_false_by_default():
    assert is_live_gmail_enabled() is False


def test_is_live_gmail_enabled_false_when_flag_false(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GMAIL_ENABLED", "false")

    assert is_live_gmail_enabled() is False


def test_is_live_gmail_enabled_false_when_credentials_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("GMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", str(tmp_path / "missing_credentials.json"))

    assert is_live_gmail_enabled() is False


def test_is_live_gmail_enabled_true_when_flag_true_and_credentials_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GMAIL_ENABLED", "true")
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", str(credentials_path))

    assert is_live_gmail_enabled() is True


class _FakeExecutable:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeMessagesResource:
    def __init__(self, list_result, get_result):
        self._list_result = list_result
        self._get_result = get_result
        self.list_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return _FakeExecutable(self._list_result)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return _FakeExecutable(self._get_result)


class _FakeUsersResource:
    def __init__(self, messages_resource: _FakeMessagesResource):
        self._messages_resource = messages_resource

    def messages(self):
        return self._messages_resource


class _FakeService:
    def __init__(self, messages_resource: _FakeMessagesResource):
        self._users_resource = _FakeUsersResource(messages_resource)

    def users(self):
        return self._users_resource


def _b64(text: str) -> str:
    import base64

    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def test_fetch_latest_email_returns_none_when_nothing_matches(monkeypatch: pytest.MonkeyPatch):
    messages_resource = _FakeMessagesResource(list_result={"messages": []}, get_result=None)
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(messages_resource)
    )

    assert fetch_latest_email() is None


def test_fetch_latest_email_defaults_to_glocco_only_query(monkeypatch: pytest.MonkeyPatch):
    """This project automates one real vendor relationship, not a generic
    inbox scanner -- the default query (no GMAIL_QUERY env var, no explicit
    query arg) should already be scoped to Glocco, not a bare "is:unread"
    that could pick up unrelated mail."""

    monkeypatch.delenv("GMAIL_QUERY", raising=False)
    messages_resource = _FakeMessagesResource(list_result={"messages": []}, get_result=None)
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(messages_resource)
    )

    fetch_latest_email()

    assert messages_resource.list_calls[0]["q"] == "from:glocco.com is:unread"


def test_fetch_latest_email_parses_plain_text_message(monkeypatch: pytest.MonkeyPatch):
    get_result = {
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Coverage needed"},
                {"name": "From", "value": "scheduler@example.com"},
                {"name": "Date", "value": "Thu, 5 Nov 2026 09:15:00 +0000"},
            ],
            "body": {"data": _b64("Sep 10, 2026, 14:00-16:00, English")},
        }
    }
    messages_resource = _FakeMessagesResource(
        list_result={"messages": [{"id": "msg-1"}]}, get_result=get_result
    )
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(messages_resource)
    )

    email = fetch_latest_email(query="is:unread")

    assert email is not None
    assert email.subject == "Coverage needed"
    assert email.sender == "scheduler@example.com"
    assert email.body == "Sep 10, 2026, 14:00-16:00, English"
    assert email.sent_date.isoformat() == "2026-11-05"
    assert messages_resource.list_calls[0]["q"] == "is:unread"


def test_fetch_latest_email_walks_multipart_for_plain_text(monkeypatch: pytest.MonkeyPatch):
    # Real Gmail messages are usually multipart/alternative: text/plain +
    # text/html siblings, sometimes nested under multipart/mixed too.
    get_result = {
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Subject", "value": "May Roster"},
                {"name": "From", "value": "scheduler@example.com"},
            ],
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64("plain body text")}},
                        {"mimeType": "text/html", "body": {"data": _b64("<p>html body</p>")}},
                    ],
                }
            ],
        }
    }
    messages_resource = _FakeMessagesResource(
        list_result={"messages": [{"id": "msg-2"}]}, get_result=get_result
    )
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(messages_resource)
    )

    email = fetch_latest_email()

    assert email.body == "plain body text"
    assert email.sent_date is None  # no Date header in this fixture
