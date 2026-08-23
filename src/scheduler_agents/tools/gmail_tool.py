from __future__ import annotations

import base64
import os
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from scheduler_agents.models.state import EmailInput

# Read-only on purpose: this integration only ever reads a message. It never
# marks anything read, labels, archives, sends, or deletes -- the same
# never-act-on-the-real-account-automatically rule every other external
# integration in this project follows. A human decides what happens to the
# source email themselves, in their own inbox.
_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def is_live_gmail_enabled() -> bool:
    """True when the flow should fetch a real email from Gmail instead of
    reading a local sample file.

    Requires an explicit opt-in (GMAIL_ENABLED=true) *and* an OAuth
    client-secrets file to actually be present. The default is always the
    local sample-file path, so tests, CI, and the zero-setup demo never
    touch the network or need a Google account.
    """

    enabled = os.getenv("GMAIL_ENABLED", "false").strip().lower() in ("true", "1", "yes")
    if not enabled:
        return False
    return Path(os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")).exists()


def _get_credentials():
    """Loads a cached OAuth token, refreshing it if expired, or runs the
    local-browser consent flow once and caches the result to disk.

    This is the one piece of this integration that needs a real interactive
    login in the user's own browser -- it only ever runs when
    is_live_gmail_enabled() is True, never in tests or the default
    sample-file path.
    """

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = Path(os.getenv("GMAIL_TOKEN_PATH", "token.json"))
    credentials_path = Path(os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json"))

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), _SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return creds


def _get_service():
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=_get_credentials())


def fetch_latest_email(query: str | None = None) -> EmailInput | None:
    """Fetches the single most recent message matching `query` (default:
    the GMAIL_QUERY env var, or "is:unread") from Gmail. Returns None if
    nothing matches -- the caller decides how to fall back.

    Read-only: uses messages().list()/get() only, never modifies anything
    in the mailbox.
    """

    query = query or os.getenv("GMAIL_QUERY", "is:unread")
    service = _get_service()

    response = service.users().messages().list(userId="me", q=query, maxResults=1).execute()
    messages = response.get("messages", [])
    if not messages:
        return None

    message = service.users().messages().get(userId="me", id=messages[0]["id"], format="full").execute()
    headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}

    sent_date = None
    if "date" in headers:
        try:
            sent_date = parsedate_to_datetime(headers["date"]).date()
        except (TypeError, ValueError):
            pass  # unparseable Date header -- callers fall back to today

    return EmailInput(
        subject=headers.get("subject", ""),
        sender=headers.get("from", ""),
        body=_extract_plain_text_body(message.get("payload", {})),
        sent_date=sent_date,
    )


def _extract_plain_text_body(payload: dict[str, Any]) -> str:
    """Walks a Gmail message payload's MIME tree for the first text/plain
    part and decodes it. Real messages are usually multipart
    (text/plain + text/html alternatives, sometimes nested further for
    attachments) -- this recurses into `parts` until it finds one. Returns
    an empty string for an HTML-only message rather than raising, matching
    "no usable body" the same way an unparseable sample email would.
    """

    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            return _decode_body(data)

    for part in payload.get("parts", []) or []:
        text = _extract_plain_text_body(part)
        if text:
            return text

    return ""


def _decode_body(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
