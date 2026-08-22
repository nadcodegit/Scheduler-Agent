from __future__ import annotations

import json
import os
from datetime import date, datetime
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


def _build_extraction_prompt(email_text: str) -> str:
    today = date.today().isoformat()
    return f"""A scheduler is asking interpreters to cover open hours. The email may list \
several distinct dated slots (e.g. "April 8: 3-4pm"), combine several times on one \
date (e.g. "April 9: 10am-12pm and 1-2pm"), or combine several dates with one time \
(e.g. "April 29, 30: 10-11am") -- each of those is a SEPARATE slot.

It may also make a vague, non-dated appeal (e.g. "we need help Monday-Wednesday \
11am-1pm this month") with no specific calendar dates -- do NOT invent dates for \
that; instead put a short description of it in "unstructured_note".

Email:
{email_text}

Today's real date is {today}. If a date has no year, use the year that makes the \
month closest to today's date. Convert all times to 24-hour HH:MM.

Return strict JSON only, no prose, no markdown fences, in exactly this shape:
{{"slots": [{{"date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM"}}], \
"unstructured_note": "<description of any vague/non-dated request, or null>"}}"""


def extract_coverage_slots_via_llm(email_text: str) -> tuple[list[CoverageSlot], str | None]:
    """Multi-slot extraction via a direct LLM call (not a CrewAI agent/task --
    this is one single-shot "read this email as JSON" call, the same
    rationale as roster_vision_tool's direct call).

    Raises on missing MODEL / network / bad-JSON errors so the caller decides
    how to fall back, matching every other LLM path in this project.
    """

    model = os.getenv("MODEL")
    if not model:
        raise RuntimeError("MODEL is not set; coverage slot extraction needs an LLM configured.")

    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": _build_extraction_prompt(email_text)}],
        temperature=0,
    )
    raw = response.choices[0].message.content
    data = json.loads(strip_code_fence(raw))

    slots: list[CoverageSlot] = []
    for item in data.get("slots", []):
        try:
            slots.append(CoverageSlot(date=item["date"], start_time=item["start_time"], end_time=item["end_time"]))
        except Exception:
            continue  # malformed row from the model; skip it rather than fail the whole batch

    return slots, data.get("unstructured_note")


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
