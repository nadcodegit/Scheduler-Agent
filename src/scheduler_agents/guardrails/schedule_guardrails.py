from __future__ import annotations

from datetime import date

from scheduler_agents.models.state import ScheduleEvent


def validate_schedule_events(events: list[ScheduleEvent]) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[date, str, str]] = set()

    if not events:
        return ["No schedule events were extracted."]

    for index, event in enumerate(events, start=1):
        key = (event.date, event.start_time.isoformat(timespec="minutes"), event.end_time.isoformat(timespec="minutes"))

        if key in seen:
            errors.append(f"Duplicate event at row {index}: {event.date} {event.start_time}-{event.end_time}.")
        seen.add(key)

        if not event.language:
            errors.append(f"Missing language at row {index}.")

        if event.language and event.language not in {"English", "Turkish", "Persian"}:
            errors.append(f"Unsupported language at row {index}: {event.language}.")

    return errors

