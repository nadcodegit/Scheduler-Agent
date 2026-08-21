from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from scheduler_agents.models.state import CoverageDecision, CoverageSlot, ScheduleEvent
from scheduler_agents.tools.schedule_parser_tool import SCHEDULE_LINE_RE


def parse_coverage_request(text: str) -> CoverageSlot | None:
    """Parse the first date/time/language slot mentioned in a coverage email.

    Coverage emails describe a single open slot, so unlike schedule emails
    (which can list many rows) this only looks for the first match.
    """

    match = SCHEDULE_LINE_RE.search(text)
    if not match:
        return None

    parsed_date = datetime.strptime(match.group("date"), "%b %d, %Y").date()
    start_time = datetime.strptime(match.group("start"), "%H:%M").time()
    end_time = datetime.strptime(match.group("end"), "%H:%M").time()
    return CoverageSlot(date=parsed_date, start_time=start_time, end_time=end_time, language=match.group("language"))


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


def draft_coverage_reply(slot: CoverageSlot, decision: CoverageDecision) -> str:
    """Template-based reply draft -- deterministic on purpose.

    This is a draft only: the flow never sends it. A human reviews and sends
    it manually (or approves it through a future notification/UI step).
    """

    slot_desc = describe_slot(slot)

    if decision == CoverageDecision.ACCEPT:
        body = f"I'm available to cover the {slot_desc} slot. Please confirm and I'll be online."
    else:
        body = (
            f"Thanks for reaching out, but I already have a commitment during the {slot_desc} "
            "slot and can't cover it this time."
        )

    return f"Hi,\n\n{body}\n\nBest,\n[Your name]"
