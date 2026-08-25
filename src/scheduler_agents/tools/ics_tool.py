from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event


def build_ics_calendar(calendar_events: list[dict[str, Any]]) -> bytes:
    """Turns this project's calendar-API-shaped payloads (see
    calendar_tool.build_calendar_event) into a standard .ics file --
    double-clickable in Outlook/Google Calendar/Apple Calendar, with zero
    real calendar integration (no API, no OAuth, no account needed). Shared
    by V1 (a month's approved schedule) and V2 (accepted coverage slots):
    both already write into the same SchedulerFlowState.calendar_events
    list, so one writer covers both without knowing which workflow ran.

    Uses the icalendar library rather than hand-rolled ICS text -- the
    format has real interop gotchas (line folding at 75 octets, mandatory
    CRLF line endings, TEXT-field escaping, VTIMEZONE blocks for named
    zones) that are easy to get subtly wrong in ways that only surface in
    stricter clients like Outlook.
    """

    calendar = Calendar()
    calendar.add("prodid", "-//Scheduler Agents//scheduler-agents//EN")
    calendar.add("version", "2.0")

    for payload in calendar_events:
        event = Event()
        event.add("summary", payload["summary"])
        event.add("description", payload["description"])
        event.add("dtstart", _parse_datetime(payload["start"]))
        event.add("dtend", _parse_datetime(payload["end"]))
        # UID and DTSTAMP are REQUIRED by RFC 5545, not optional extras --
        # missing them can make stricter clients (Outlook historically more
        # so than Google/Apple Calendar) reject or mishandle the import.
        # UID is a deterministic hash of the event's own content rather than
        # a random uuid4, so re-generating this file from the same source
        # data twice produces the same UID -- reimporting is an update, not
        # a duplicate.
        event.add("uid", _build_uid(payload))
        event.add("dtstamp", datetime.now(timezone.utc))

        for override in payload.get("reminders", {}).get("overrides", []):
            if override.get("method") != "popup":
                # ICS VALARM does have an ACTION:EMAIL, but it requires an
                # ATTENDEE and is unevenly supported across clients; the
                # popup/DISPLAY alarm alone already satisfies the actual
                # goal here (a usable, double-clickable event reminder).
                continue
            alarm = Alarm()
            alarm.add("action", "DISPLAY")
            alarm.add("description", payload["summary"])
            alarm.add("trigger", -_minutes(override["minutes"]))
            event.add_component(alarm)

        calendar.add_component(event)

    # Outlook in particular is stricter than Google/Apple Calendar about
    # expecting a VTIMEZONE block for any TZID it sees rather than just
    # trusting the IANA zone name -- this embeds one per zone actually used.
    calendar.add_missing_timezones()
    return calendar.to_ical()


def _build_uid(payload: dict[str, Any]) -> str:
    key = f"{payload['start']['dateTime']}|{payload['end']['dateTime']}|{payload['summary']}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return f"{digest}@scheduler-agents.local"


def _parse_datetime(spec: dict[str, str]) -> datetime:
    naive = datetime.fromisoformat(spec["dateTime"])
    return naive.replace(tzinfo=ZoneInfo(spec["timeZone"]))


def _minutes(count: int) -> timedelta:
    return timedelta(minutes=count)
