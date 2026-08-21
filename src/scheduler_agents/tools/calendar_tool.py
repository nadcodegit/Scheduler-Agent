from __future__ import annotations

from typing import Any

from scheduler_agents.models.state import ScheduleEvent


def build_calendar_event(event: ScheduleEvent, timezone: str) -> dict[str, Any]:
    """Build a calendar API-ready payload without writing to a real calendar."""

    start = f"{event.date.isoformat()}T{event.start_time.isoformat(timespec='minutes')}:00"
    end = f"{event.date.isoformat()}T{event.end_time.isoformat(timespec='minutes')}:00"

    return {
        "summary": event.title,
        "description": f"Language: {event.language or 'Unknown'}",
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 30},
                {"method": "email", "minutes": 120},
            ],
        },
    }

