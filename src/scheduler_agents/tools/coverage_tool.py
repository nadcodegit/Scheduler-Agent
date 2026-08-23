from __future__ import annotations

import calendar
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import litellm

from scheduler_agents.models.state import CoverageDecision, CoverageSlot, CoverageSlotDecision, ScheduleEvent
from scheduler_agents.tools.llm_json import strip_code_fence
from scheduler_agents.tools.schedule_parser_tool import SCHEDULE_LINE_RE


def parse_coverage_request_regex(text: str) -> list[CoverageSlot]:
    """Deterministic, zero-cost fallback: one slot in the strict
    "Mon DD, YYYY, HH:MM-HH:MM, Language" format.

    Real coverage emails are rarely this tidy (12-hour times, several dates
    in one email, dates without a year) -- this exists purely so the flow
    still works offline / without an LLM key, the same tradeoff every other
    extraction path in this project makes.
    """

    match = SCHEDULE_LINE_RE.search(text)
    if not match:
        return []

    parsed_date = datetime.strptime(match.group("date"), "%b %d, %Y").date()
    start_time = datetime.strptime(match.group("start"), "%H:%M").time()
    end_time = datetime.strptime(match.group("end"), "%H:%M").time()
    return [CoverageSlot(date=parsed_date, start_time=start_time, end_time=end_time, language=match.group("language"))]


_WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _build_relative_date_hints(anchor: date) -> dict[str, str]:
    """A deterministic date lookup for the anchor day, so the LLM looks
    relative dates up instead of computing them itself -- date arithmetic is
    exactly the kind of thing a model gets subtly wrong (off-by-one weekday,
    wrong week) where plain code doesn't.

    "this <weekday>" is the next occurrence of that weekday on/after the
    anchor (today counts); "next <weekday>" is the one after that.
    """

    hints = {
        "today": anchor.isoformat(),
        "tomorrow": (anchor + timedelta(days=1)).isoformat(),
    }
    for offset, name in enumerate(_WEEKDAY_NAMES):
        days_ahead = (offset - anchor.weekday()) % 7
        this_occurrence = anchor + timedelta(days=days_ahead)
        hints[f"this {name}"] = this_occurrence.isoformat()
        hints[f"next {name}"] = (this_occurrence + timedelta(days=7)).isoformat()
    return hints


_MONTH_BY_NAME = {
    name.lower(): index
    for index, name in enumerate(calendar.month_name)
    if name
} | {
    abbr.lower(): index
    for index, abbr in enumerate(calendar.month_abbr)
    if abbr
}

_ORDINAL_WEEKS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}

_PERIOD_WEEK_OF_MONTH_RE = re.compile(
    r"\b(?P<ordinal>first|second|third|fourth|1st|2nd|3rd|4th|last)\s+week\s+of\s+"
    r"(?P<month>[a-zA-Z]+)(?:\s+(?P<year>\d{4}))?",
    re.IGNORECASE,
)


def _week_start_in_month(year: int, month: int, ordinal: str) -> date | None:
    """First day of the "1st/2nd/.../last week" of a month, where weeks are
    simply consecutive 7-day blocks starting on the 1st (day 1-7, 8-14, ...)
    -- the simplest convention that doesn't depend on which weekday a month
    happens to start on, and matches how these emails use the phrase.
    """

    days_in_month = calendar.monthrange(year, month)[1]
    if ordinal == "last":
        start_day = ((days_in_month - 1) // 7) * 7 + 1
    else:
        start_day = (_ORDINAL_WEEKS[ordinal] - 1) * 7 + 1
        if start_day > days_in_month:
            return None
    return date(year, month, start_day)


def resolve_period_phrase(period: str, anchor: date) -> date | None:
    """Deterministically resolve a period phrase like "first week of March",
    "this week", or "next week" to the first day of that week, so bare
    weekday names ("Monday: 11am-3pm") can be mapped to real calendar dates
    without asking the LLM to do date arithmetic itself -- the same
    rationale as _build_relative_date_hints. Returns None for anything not
    recognized, so the caller can flag it for a human rather than guess.

    When the phrase names a month with no year, picks the nearest occurrence
    that hasn't already fully passed relative to `anchor` (never the one
    that's already over), since these are always requests about upcoming
    availability.
    """

    text = period.strip().lower()

    if text == "this week":
        return anchor - timedelta(days=anchor.weekday())
    if text == "next week":
        return anchor - timedelta(days=anchor.weekday()) + timedelta(days=7)

    match = _PERIOD_WEEK_OF_MONTH_RE.search(text)
    if not match:
        return None

    month = _MONTH_BY_NAME.get(match.group("month").lower())
    if not month:
        return None
    ordinal = match.group("ordinal")

    if match.group("year"):
        return _week_start_in_month(int(match.group("year")), month, ordinal)

    for year in (anchor.year, anchor.year + 1):
        start = _week_start_in_month(year, month, ordinal)
        if start is not None and start + timedelta(days=6) >= anchor:
            return start
    return _week_start_in_month(anchor.year, month, ordinal)  # every candidate already passed


def _build_extraction_prompt(email_text: str, anchor_date: date) -> str:
    hints = _build_relative_date_hints(anchor_date)
    return f"""A scheduler is asking interpreters to cover open hours. The email may list \
several distinct dated slots (e.g. "April 8: 3-4pm"), combine several times on one \
date (e.g. "April 9: 10am-12pm and 1-2pm"), or combine several dates with one time \
(e.g. "April 29, 30: 10-11am") -- each of those is a SEPARATE slot.

It may also make a vague, non-dated appeal (e.g. "we need help Monday-Wednesday \
11am-1pm this month") with no specific calendar dates -- do NOT invent dates for \
that; instead put a short description of it in "unstructured_note".

Some emails list weekday NAMES instead of calendar dates (e.g. "Monday: 11am-3pm, \
4-5pm"), usually next to a phrase naming which week that applies to (e.g. "first \
week of March", "next week", "this week"). Do NOT compute a calendar date for \
these yourself -- you are bad at this and will get the weekday wrong. Instead put \
each such row in "weekday_slots" as {{"weekday": "Monday", "start_time": "HH:MM", \
"end_time": "HH:MM"}}, and copy the phrase naming which week into "period" exactly \
as written (null if there is no such phrase). One "period" applies to the whole \
email's weekday_slots.

Email:
{email_text}

The email was sent on {anchor_date.isoformat()} ({anchor_date.strftime("%A")}). If a \
date has no year, use the year that makes the month closest to that date.

The email may use relative phrases ("today", "tomorrow", "this Friday", "next \
Monday") instead of calendar dates. Do NOT compute these yourself -- look them up \
in this table instead (all dates are anchored to the day the email was sent):
{json.dumps(hints, indent=2)}
For any relative phrase not in the table (e.g. "in two weeks"), compute it as an \
offset from {anchor_date.isoformat()}. Convert all times to 24-hour HH:MM.

Return strict JSON only, no prose, no markdown fences, in exactly this shape:
{{"slots": [{{"date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM"}}], \
"weekday_slots": [{{"weekday": "Monday", "start_time": "HH:MM", "end_time": "HH:MM"}}], \
"period": "<phrase naming the week weekday_slots refers to, or null>", \
"unstructured_note": "<description of any vague/non-dated request, or null>"}}"""


def extract_coverage_slots_via_llm(
    email_text: str, anchor_date: date | None = None
) -> tuple[list[CoverageSlot], str | None]:
    """Multi-slot extraction via a direct LLM call (not a CrewAI agent/task --
    this is one single-shot "read this email as JSON" call, the same
    rationale as roster_vision_tool's direct call).

    `anchor_date` should be the day the email was sent (EmailInput.sent_date);
    it defaults to today only when that's unknown, since relative phrases
    ("today", "next Monday") must resolve against the send date, not
    whatever day the flow happens to run.

    Raises on missing MODEL / network / bad-JSON errors so the caller decides
    how to fall back, matching every other LLM path in this project.
    """

    model = os.getenv("MODEL")
    if not model:
        raise RuntimeError("MODEL is not set; coverage slot extraction needs an LLM configured.")

    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": _build_extraction_prompt(email_text, anchor_date or date.today())}],
        temperature=0,
    )
    raw = response.choices[0].message.content
    data = json.loads(strip_code_fence(raw))
    anchor = anchor_date or date.today()

    slots: list[CoverageSlot] = []
    for item in data.get("slots", []):
        try:
            slots.append(CoverageSlot(date=item["date"], start_time=item["start_time"], end_time=item["end_time"]))
        except Exception:
            continue  # malformed row from the model; skip it rather than fail the whole batch

    period = data.get("period")
    period_start = resolve_period_phrase(period, anchor) if period else None
    unresolved_weekday_slots = 0

    for item in data.get("weekday_slots", []):
        weekday_name = str(item.get("weekday", "")).strip().lower()
        if weekday_name not in _WEEKDAY_NAMES or period_start is None:
            unresolved_weekday_slots += 1
            continue
        weekday_index = _WEEKDAY_NAMES.index(weekday_name)
        slot_date = period_start + timedelta(days=(weekday_index - period_start.weekday()) % 7)
        try:
            slots.append(CoverageSlot(date=slot_date, start_time=item["start_time"], end_time=item["end_time"]))
        except Exception:
            continue  # malformed row from the model; skip it rather than fail the whole batch

    note = data.get("unstructured_note")
    if unresolved_weekday_slots:
        # A human needs to resolve these manually -- guessing a date here is
        # exactly the failure mode this function exists to avoid.
        flag = (
            f"{unresolved_weekday_slots} weekday-labeled slot(s) with no resolvable "
            f'week (period: "{period}")'
        )
        note = f"{note}; {flag}" if note else flag

    return slots, note


def load_busy_events(path: Path) -> list[ScheduleEvent]:
    """Load the user's already-committed schedule to check new slots against.

    V1/V2 have no live calendar integration yet, so this reads a JSON fixture
    representing "what's already on the calendar" instead of calling a real
    Calendar API.
    """

    if not path.exists():
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    return [ScheduleEvent(**item) for item in data]


def has_conflict(slot: CoverageSlot, busy_events: list[ScheduleEvent]) -> bool:
    """True if the slot overlaps any already-committed event on the same date."""

    return any(
        event.date == slot.date and slot.start_time < event.end_time and event.start_time < slot.end_time
        for event in busy_events
    )


def describe_slot(slot: CoverageSlot) -> str:
    return (
        f"{slot.date.isoformat()} {slot.start_time.isoformat(timespec='minutes')}"
        f"-{slot.end_time.isoformat(timespec='minutes')} ({slot.language or 'Unknown'})"
    )


def draft_coverage_reply_multi(decisions: list[CoverageSlotDecision], unstructured_note: str | None) -> str:
    """Template-based reply draft covering every slot at once -- deterministic
    on purpose, like every other draft in this project. Never sent by the
    flow; a human reviews and sends it manually.
    """

    accepted = [d.slot for d in decisions if d.decision == CoverageDecision.ACCEPT]
    declined = [d.slot for d in decisions if d.decision == CoverageDecision.DECLINE]

    lines = ["Hi,", "", "Thank you for reaching out. Here's my availability for the requested slots:"]

    if accepted:
        lines.append("")
        lines.append("I can cover:")
        lines.extend(f"- {describe_slot(slot)}" for slot in accepted)

    if declined:
        lines.append("")
        lines.append("I'm unable to cover:")
        lines.extend(f"- {describe_slot(slot)}" for slot in declined)

    if unstructured_note:
        lines.append("")
        lines.append(f"Regarding \"{unstructured_note}\": I'll follow up separately once I check my availability.")

    lines.append("")
    lines.append("Best,")
    lines.append("[Your name]")

    return "\n".join(lines)
