from __future__ import annotations

from datetime import date

from scheduler_agents.models.state import ScheduleEvent


def validate_schedule_events(events: list[ScheduleEvent]) -> list[str]:
    """Deterministic final check before anything reaches a calendar.

    No language check here: this project automates one real vendor
    relationship that is Persian-only, so scheduler_flow.py always assigns
    event.language from UserMemory.default_language before this runs --
    it's a known fact about the interpreter's employment, not something to
    extract or validate per event.
    """

    errors: list[str] = []
    seen: set[tuple[date, str, str]] = set()

    if not events:
        return ["No schedule events were extracted."]

    for index, event in enumerate(events, start=1):
        key = (event.date, event.start_time.isoformat(timespec="minutes"), event.end_time.isoformat(timespec="minutes"))

        if key in seen:
            errors.append(f"Duplicate event at row {index}: {event.date} {event.start_time}-{event.end_time}.")
        seen.add(key)

    return errors

