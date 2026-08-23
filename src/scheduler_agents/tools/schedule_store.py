from __future__ import annotations

import json
from pathlib import Path

from scheduler_agents.models.state import ScheduleEvent


def load_approved_schedule(path: Path) -> list[ScheduleEvent]:
    """Reads the local store of approved work-schedule slots.

    This is deliberately V2's only source of truth for "is this coverage
    slot already committed" -- a personal Google/Outlook calendar mixes in
    dentist appointments, school pickups, and everything else that has
    nothing to do with work scheduling, so it isn't a meaningful conflict
    signal for this agent. The only things that belong here are slots this
    agent itself put there: an approved monthly schedule (V1) or an
    accepted coverage slot (V2).
    """

    if not path.exists():
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    return [ScheduleEvent(**item) for item in data]


def save_approved_schedule(new_events: list[ScheduleEvent], path: Path) -> list[ScheduleEvent]:
    """Merges newly-approved events into the local store and persists the
    result, deduping exact date/start/end repeats (e.g. re-running the same
    month's import twice). Returns the full merged list.

    Called once a monthly schedule clears the deterministic guardrail (V1)
    or a human accepts a coverage slot (V2) -- either way, it's now a real
    work commitment future conflict checks need to know about.
    """

    existing = load_approved_schedule(path)
    seen = {(event.date, event.start_time, event.end_time) for event in existing}

    merged = list(existing)
    for event in new_events:
        key = (event.date, event.start_time, event.end_time)
        if key not in seen:
            merged.append(event)
            seen.add(key)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([json.loads(event.model_dump_json()) for event in merged], indent=2),
        encoding="utf-8",
    )
    return merged
