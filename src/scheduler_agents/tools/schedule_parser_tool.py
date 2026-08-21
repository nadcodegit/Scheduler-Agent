from __future__ import annotations

import re
from datetime import datetime

from scheduler_agents.models.state import ScheduleEvent


SCHEDULE_LINE_RE = re.compile(
    r"(?P<date>[A-Za-z]{3}\s+\d{1,2},\s+\d{4}),\s+"
    r"(?P<start>\d{2}:\d{2})-(?P<end>\d{2}:\d{2}),\s+"
    r"(?P<language>[A-Za-z]+)"
)


def parse_schedule_text(text: str) -> list[ScheduleEvent]:
    """Parse simple schedule lines into structured events.

    This deterministic parser is the V1 baseline. Later, an LLM extraction task
    can call this first and only escalate ambiguous cases to structured output.
    """

    events: list[ScheduleEvent] = []
    for match in SCHEDULE_LINE_RE.finditer(text):
        parsed_date = datetime.strptime(match.group("date"), "%b %d, %Y").date()
        start_time = datetime.strptime(match.group("start"), "%H:%M").time()
        end_time = datetime.strptime(match.group("end"), "%H:%M").time()
        events.append(
            ScheduleEvent(
                date=parsed_date,
                start_time=start_time,
                end_time=end_time,
                language=match.group("language"),
            )
        )
    return events

