from __future__ import annotations

import base64
import os
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from scheduler_agents.models.state import EmailInput

# Read-only for fetching, plus the narrowest scope that allows creating a
# draft reply -- gmail.compose, not gmail.modify or full mail.google.com
# access. gmail.compose covers create/read/update/delete of drafts and
# *only* sending messages this app itself created; it cannot touch, label,
# archive, or delete anything already in the mailbox. This project never
# calls the send endpoint -- every draft sits in the account for the human
# to review and send themselves, the same never-auto-act rule as
# everywhere else, just extended one step from "read" to "propose."
_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]


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


def fetch_latest_email(
    query: str | None = None,
    save_pdf_attachment_to: Path | None = None,
    save_roster_image_to: Path | None = None,
) -> EmailInput | None:
    """Fetches the single most recent message matching `query` (default:
    the GMAIL_QUERY env var, or a Glocco-only default covering both of
    their real sending domains -- this project automates one real vendor
    relationship, not a generic inbox scanner) from Gmail. Returns None if
    nothing matches -- the caller decides how to fall back.

    Read-only: uses messages().list()/get()/attachments().get() only, never
    modifies anything in the mailbox.

    When `save_pdf_attachment_to` is given and the message has a PDF
    attachment (V4's real Purchase Order notifications carry the actual
    job id/period/amount data as a PDF, not in the body), the first one
    found is downloaded and written there; `EmailInput.attachments` is
    populated with its filename as the caller's signal that a save
    happened -- an empty list means no PDF was found, not "check the file",
    since a stale file from a previous run may still exist on disk.

    When `save_roster_image_to` is given and the message has an embedded
    image (the real monthly roster always arrives as a screenshot, never
    text or a PDF), the *first* one in document order is downloaded and
    written there -- see `_find_first_image_part` for why document order,
    not size, is the reliable signal for which embedded image is actually
    the roster. The saved file's extension matches the image's real
    mimeType, since roster_vision_tool relies on it to pick the right
    content-type when calling a vision model. `EmailInput.roster_image_path`
    is set to the actual saved path (not just a filename) as the caller's
    signal that a save happened -- None means no image was found this run.
    """

    # Glocco uses two real sending domains: glocco.com for scheduling/
    # coverage mail, glocco.sk for Purchase Order/invoicing notifications
    # (their XTRF platform) -- confirmed live, a from:glocco.com-only query
    # silently missed a real unread Purchase Order email from glocco.sk.
    query = query or os.getenv("GMAIL_QUERY", "from:(glocco.com OR glocco.sk) is:unread")
    service = _get_service()

    response = service.users().messages().list(userId="me", q=query, maxResults=1).execute()
    messages = response.get("messages", [])
    if not messages:
        return None

    message_id = messages[0]["id"]
    message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = message.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

    sent_date = None
    if "date" in headers:
        try:
            sent_date = parsedate_to_datetime(headers["date"]).date()
        except (TypeError, ValueError):
            pass  # unparseable Date header -- callers fall back to today

    attachments: list[str] = []
    if save_pdf_attachment_to is not None:
        pdf_part = _find_first_pdf_part(payload)
        if pdf_part is not None:
            pdf_bytes = _fetch_attachment_bytes(service, message_id, pdf_part)
            save_pdf_attachment_to.parent.mkdir(parents=True, exist_ok=True)
            save_pdf_attachment_to.write_bytes(pdf_bytes)
            attachments = [pdf_part.get("filename") or save_pdf_attachment_to.name]

    roster_image_path: str | None = None
    if save_roster_image_to is not None:
        image_part = _find_first_image_part(payload)
        if image_part is not None:
            image_bytes = _fetch_attachment_bytes(service, message_id, image_part)
            extension = _extension_for_mime_type(image_part.get("mimeType", ""))
            actual_path = save_roster_image_to.with_suffix(extension)
            actual_path.parent.mkdir(parents=True, exist_ok=True)
            actual_path.write_bytes(image_bytes)
            roster_image_path = str(actual_path)

    return EmailInput(
        subject=headers.get("subject", ""),
        sender=headers.get("from", ""),
        body=_extract_plain_text_body(payload),
        attachments=attachments,
        sent_date=sent_date,
        roster_image_path=roster_image_path,
        thread_id=message.get("threadId"),
        rfc_message_id=headers.get("message-id"),
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


def _find_first_pdf_part(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Walks a Gmail message payload's MIME tree for the first part that
    looks like a PDF attachment (a filename ending in .pdf, or an explicit
    application/pdf mimeType) and returns that part's raw dict, or None.
    """

    filename = payload.get("filename") or ""
    mime_type = payload.get("mimeType") or ""
    if filename.lower().endswith(".pdf") or mime_type == "application/pdf":
        return payload

    for part in payload.get("parts", []) or []:
        found = _find_first_pdf_part(part)
        if found is not None:
            return found

    return None


def _find_first_image_part(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Walks a Gmail message payload's MIME tree for the first part whose
    mimeType starts with "image/" -- both inline (cid-referenced) and
    regular attachments show up this way in the API's payload structure,
    regardless of how they're displayed in a mail client.

    A real roster email carries more than one image -- the roster
    screenshot itself plus signature images (a favicon, a LinkedIn banner
    used as a footer). Picking by byte size was tried and verified wrong
    against a real message: a photographic marketing banner ("Respect the
    Locals") outweighed the actual roster screenshot (flat colors, much
    more compressible) by 6x despite being irrelevant. Document order is
    the real signal instead -- Outlook places the content image the sender
    actually referenced ("...your September roster, kindly see it
    below:") before the signature block's images, confirmed against a real
    message where image001.png (first) was the roster, and
    image002.png/image003.jpg (signature logo + banner) came after.
    """

    if (payload.get("mimeType") or "").startswith("image/"):
        return payload

    for part in payload.get("parts", []) or []:
        found = _find_first_image_part(part)
        if found is not None:
            return found

    return None


def _extension_for_mime_type(mime_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(mime_type.lower(), ".png")


def _fetch_attachment_bytes(service, message_id: str, part: dict[str, Any]) -> bytes:
    """Decodes an attachment part's content. Small attachments carry their
    data inline in the part itself; larger ones (real PDFs, almost always)
    only carry an attachmentId requiring a separate API call to fetch.
    """

    body = part.get("body", {})
    data = body.get("data")
    if not data:
        attachment_id = body["attachmentId"]
        attachment = (
            service.users().messages().attachments().get(userId="me", messageId=message_id, id=attachment_id).execute()
        )
        data = attachment["data"]
    return base64.urlsafe_b64decode(data.encode("utf-8"))


def create_draft_reply(
    to: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
) -> str | None:
    """Creates a real Gmail draft -- never sends it. This is the one write
    operation this integration performs, using the narrowest scope that
    allows it (see _SCOPES above); a human still has to open Gmail, review
    it, and hit send themselves.

    When `thread_id`/`in_reply_to` are given (always true for a live-fetched
    email, never for a sample-file run), the draft is filed into the
    *original* conversation via the standard In-Reply-To/References headers
    plus Gmail's own threadId -- otherwise it would show up as a
    disconnected new email instead of an actual reply.
    """

    service = _get_service()

    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    draft_message: dict[str, Any] = {"raw": raw}
    if thread_id:
        draft_message["threadId"] = thread_id

    draft = service.users().drafts().create(userId="me", body={"message": draft_message}).execute()
    return draft.get("id")
