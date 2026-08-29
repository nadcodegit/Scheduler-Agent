from __future__ import annotations

import base64
from pathlib import Path

import pytest

from scheduler_agents.tools.gmail_tool import create_draft_reply, fetch_latest_email, is_live_gmail_enabled


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


class _FakeAttachmentsResource:
    def __init__(self, get_result):
        self._get_result = get_result
        self.get_calls: list[dict] = []

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return _FakeExecutable(self._get_result)


class _FakeMessagesResource:
    def __init__(self, list_result, get_result, attachment_get_result=None):
        self._list_result = list_result
        self._get_result = get_result
        self.list_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.attachments_resource = _FakeAttachmentsResource(attachment_get_result)

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return _FakeExecutable(self._list_result)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return _FakeExecutable(self._get_result)

    def attachments(self):
        return self.attachments_resource


class _FakeDraftsResource:
    def __init__(self):
        self.create_calls: list[dict] = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return _FakeExecutable({"id": "draft-1"})


class _FakeUsersResource:
    def __init__(self, messages_resource: _FakeMessagesResource | None = None, drafts_resource: _FakeDraftsResource | None = None):
        self._messages_resource = messages_resource
        self._drafts_resource = drafts_resource or _FakeDraftsResource()

    def messages(self):
        return self._messages_resource

    def drafts(self):
        return self._drafts_resource


class _FakeService:
    def __init__(self, messages_resource: _FakeMessagesResource | None = None, drafts_resource: _FakeDraftsResource | None = None):
        self._users_resource = _FakeUsersResource(messages_resource, drafts_resource)

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
    that could pick up unrelated mail. Covers both of their real sending
    domains: glocco.com (scheduling/coverage) and glocco.sk (Purchase Order/
    invoicing, confirmed live -- a glocco.com-only query silently missed a
    real unread Purchase Order email from glocco.sk)."""

    monkeypatch.delenv("GMAIL_QUERY", raising=False)
    messages_resource = _FakeMessagesResource(list_result={"messages": []}, get_result=None)
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(messages_resource)
    )

    fetch_latest_email()

    assert messages_resource.list_calls[0]["q"] == "from:(glocco.com OR glocco.sk) is:unread"


def test_fetch_latest_email_parses_plain_text_message(monkeypatch: pytest.MonkeyPatch):
    get_result = {
        "threadId": "thread-abc",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Coverage needed"},
                {"name": "From", "value": "scheduler@example.com"},
                {"name": "Date", "value": "Thu, 5 Nov 2026 09:15:00 +0000"},
                {"name": "Message-ID", "value": "<abc123@mail.gmail.com>"},
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
    assert email.thread_id == "thread-abc"
    assert email.rfc_message_id == "<abc123@mail.gmail.com>"
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


def test_fetch_latest_email_saves_small_inline_pdf_attachment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Small attachments carry their data inline in the part itself, no
    # separate attachments().get() call needed.
    pdf_bytes = b"%PDF-1.4 fake pdf content"
    get_result = {
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Subject", "value": "Purchase Order"},
                {"name": "From", "value": "scheduler@glocco.com"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Please find attached the Purchase Order.")}},
                {
                    "filename": "Purchase_Order_1944.pdf",
                    "mimeType": "application/pdf",
                    "body": {"data": base64.urlsafe_b64encode(pdf_bytes).decode("ascii")},
                },
            ],
        }
    }
    messages_resource = _FakeMessagesResource(
        list_result={"messages": [{"id": "msg-po"}]}, get_result=get_result
    )
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(messages_resource)
    )

    save_path = tmp_path / "downloaded.pdf"
    email = fetch_latest_email(save_pdf_attachment_to=save_path)

    assert email.attachments == ["Purchase_Order_1944.pdf"]
    assert save_path.read_bytes() == pdf_bytes
    assert messages_resource.attachments_resource.get_calls == []  # inline data, no extra call needed


def test_fetch_latest_email_fetches_large_pdf_attachment_via_attachment_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Real (larger) PDFs only carry an attachmentId in the message payload;
    # the actual bytes require a separate attachments().get() call.
    pdf_bytes = b"%PDF-1.4 a bigger fake pdf"
    get_result = {
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Subject", "value": "Purchase Order"},
                {"name": "From", "value": "scheduler@glocco.com"},
            ],
            "parts": [
                {
                    "filename": "Purchase_Order_1944.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att-123", "size": 999999},
                },
            ],
        }
    }
    messages_resource = _FakeMessagesResource(
        list_result={"messages": [{"id": "msg-po"}]},
        get_result=get_result,
        attachment_get_result={"data": base64.urlsafe_b64encode(pdf_bytes).decode("ascii")},
    )
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(messages_resource)
    )

    save_path = tmp_path / "downloaded.pdf"
    email = fetch_latest_email(save_pdf_attachment_to=save_path)

    assert email.attachments == ["Purchase_Order_1944.pdf"]
    assert save_path.read_bytes() == pdf_bytes
    assert messages_resource.attachments_resource.get_calls == [
        {"userId": "me", "messageId": "msg-po", "id": "att-123"}
    ]


def test_fetch_latest_email_does_not_save_when_no_pdf_attachment_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """No PDF found -- attachments must be empty (the caller's signal that
    nothing was saved this run), and nothing should be written to disk."""

    get_result = {
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "Subject", "value": "Free slot"}, {"name": "From", "value": "scheduler@glocco.com"}],
            "body": {"data": _b64("no attachment here")},
        }
    }
    messages_resource = _FakeMessagesResource(
        list_result={"messages": [{"id": "msg-1"}]}, get_result=get_result
    )
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(messages_resource)
    )

    save_path = tmp_path / "downloaded.pdf"
    email = fetch_latest_email(save_pdf_attachment_to=save_path)

    assert email.attachments == []
    assert not save_path.exists()


def _decode_raw_message(raw: str):
    from email import message_from_bytes

    return message_from_bytes(base64.urlsafe_b64decode(raw.encode("utf-8")))


def test_create_draft_reply_never_sends_only_creates_a_draft(monkeypatch: pytest.MonkeyPatch):
    """The one write operation this integration performs -- confirms it
    calls drafts().create(), never messages().send()."""

    drafts_resource = _FakeDraftsResource()
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(drafts_resource=drafts_resource)
    )

    draft_id = create_draft_reply(to="scheduler@glocco.com", subject="Coverage needed", body="I can cover this.")

    assert draft_id == "draft-1"
    assert len(drafts_resource.create_calls) == 1


def test_create_draft_reply_threads_into_the_original_conversation(monkeypatch: pytest.MonkeyPatch):
    """Without threadId/In-Reply-To, a reply would show up as a disconnected
    new email instead of an actual reply in the vendor's inbox."""

    drafts_resource = _FakeDraftsResource()
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(drafts_resource=drafts_resource)
    )

    create_draft_reply(
        to="scheduler@glocco.com",
        subject="Coverage needed",
        body="I can cover this.",
        thread_id="thread-abc",
        in_reply_to="<original@mail.gmail.com>",
    )

    call = drafts_resource.create_calls[0]
    assert call["body"]["message"]["threadId"] == "thread-abc"
    message = _decode_raw_message(call["body"]["message"]["raw"])
    assert message["In-Reply-To"] == "<original@mail.gmail.com>"
    assert message["References"] == "<original@mail.gmail.com>"


def test_create_draft_reply_does_not_double_prefix_existing_re(monkeypatch: pytest.MonkeyPatch):
    drafts_resource = _FakeDraftsResource()
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(drafts_resource=drafts_resource)
    )

    create_draft_reply(to="scheduler@glocco.com", subject="Re: Coverage needed", body="body")

    message = _decode_raw_message(drafts_resource.create_calls[0]["body"]["message"]["raw"])
    assert message["Subject"] == "Re: Coverage needed"


def test_create_draft_reply_adds_re_prefix_when_missing(monkeypatch: pytest.MonkeyPatch):
    drafts_resource = _FakeDraftsResource()
    monkeypatch.setattr(
        "scheduler_agents.tools.gmail_tool._get_service", lambda: _FakeService(drafts_resource=drafts_resource)
    )

    create_draft_reply(to="scheduler@glocco.com", subject="Coverage needed", body="body")

    message = _decode_raw_message(drafts_resource.create_calls[0]["body"]["message"]["raw"])
    assert message["Subject"] == "Re: Coverage needed"
