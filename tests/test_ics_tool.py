from __future__ import annotations

from icalendar import Calendar

from scheduler_agents.tools.calendar_tool import build_calendar_event
from scheduler_agents.tools.ics_tool import build_ics_calendar
from scheduler_agents.models.state import ScheduleEvent


def _payload(date: str, start: str, end: str, title: str = "Interpretation Session") -> dict:
    event = ScheduleEvent(date=date, start_time=start, end_time=end, language="Persian", title=title)
    return build_calendar_event(event, timezone="Europe/London")


def test_build_ics_calendar_round_trips_summary_and_times():
    payloads = [_payload("2026-09-10", "14:00", "16:00")]

    ics_bytes = build_ics_calendar(payloads)
    parsed = Calendar.from_ical(ics_bytes)

    events = list(parsed.walk("VEVENT"))
    assert len(events) == 1
    assert str(events[0]["summary"]) == "Interpretation Session"
    assert events[0]["dtstart"].dt.hour == 14
    assert events[0]["dtend"].dt.hour == 16
    assert events[0]["dtstart"].dt.tzinfo is not None


def test_build_ics_calendar_includes_one_event_per_payload():
    payloads = [
        _payload("2026-09-10", "14:00", "16:00"),
        _payload("2026-09-12", "09:00", "11:00"),
    ]

    parsed = Calendar.from_ical(build_ics_calendar(payloads))

    assert len(list(parsed.walk("VEVENT"))) == 2


def test_build_ics_calendar_adds_a_popup_alarm_not_an_email_one():
    parsed = Calendar.from_ical(build_ics_calendar([_payload("2026-09-10", "14:00", "16:00")]))
    event = list(parsed.walk("VEVENT"))[0]

    alarms = list(event.walk("VALARM"))
    assert len(alarms) == 1
    assert str(alarms[0]["action"]) == "DISPLAY"


def test_build_ics_calendar_with_no_events_is_still_a_valid_empty_calendar():
    parsed = Calendar.from_ical(build_ics_calendar([]))
    assert list(parsed.walk("VEVENT")) == []
