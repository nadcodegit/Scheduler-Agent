from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from scheduler_agents.flows.scheduler_flow import SchedulerFlow
from scheduler_agents.models.state import CoverageDecision, CoverageSlot, CoverageSlotDecision, ScheduleEvent
from scheduler_agents.tools.coverage_tool import (
    _build_relative_date_hints,
    draft_coverage_reply_multi,
    extract_coverage_slots_via_llm,
    has_conflict,
    parse_coverage_request_regex,
    resolve_period_phrase,
)

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample_data"


def test_parse_coverage_request_regex_extracts_single_slot():
    text = "We have an open slot: Sep 10, 2026, 14:00-16:00, English. Can you cover it?"

    slots = parse_coverage_request_regex(text)

    assert len(slots) == 1
    assert slots[0].date.isoformat() == "2026-09-10"
    assert slots[0].language == "English"


def test_parse_coverage_request_regex_returns_empty_when_unparseable():
    assert parse_coverage_request_regex("Can you help cover a shift sometime next week?") == []


def test_has_conflict_true_for_overlapping_slot():
    slot = CoverageSlot(date="2026-09-10", start_time="14:00", end_time="16:00", language="English")
    busy = [ScheduleEvent(date="2026-09-10", start_time="15:00", end_time="17:00", language="English")]

    assert has_conflict(slot, busy) is True


def test_has_conflict_false_for_non_overlapping_slot():
    slot = CoverageSlot(date="2026-09-11", start_time="14:00", end_time="16:00", language="English")
    busy = [ScheduleEvent(date="2026-09-10", start_time="15:00", end_time="17:00", language="English")]

    assert has_conflict(slot, busy) is False


def test_draft_reply_multi_lists_accepted_and_declined_separately():
    accepted_slot = CoverageSlot(date="2026-09-10", start_time="14:00", end_time="16:00")
    declined_slot = CoverageSlot(date="2026-09-11", start_time="09:00", end_time="10:00")
    decisions = [
        CoverageSlotDecision(slot=accepted_slot, conflict=False, decision=CoverageDecision.ACCEPT),
        CoverageSlotDecision(slot=declined_slot, conflict=True, decision=CoverageDecision.DECLINE),
    ]

    draft = draft_coverage_reply_multi(decisions, unstructured_note=None)

    assert "I can cover" in draft
    assert "2026-09-10" in draft
    assert "I'm unable to cover" in draft
    assert "2026-09-11" in draft


def test_draft_reply_multi_includes_unstructured_note():
    slot = CoverageSlot(date="2026-09-10", start_time="14:00", end_time="16:00")
    decisions = [CoverageSlotDecision(slot=slot, conflict=False, decision=CoverageDecision.ACCEPT)]

    draft = draft_coverage_reply_multi(decisions, unstructured_note="Mon-Wed 11am-1pm all month")

    assert "Mon-Wed 11am-1pm all month" in draft


def test_extract_coverage_slots_via_llm_raises_without_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MODEL", raising=False)

    with pytest.raises(RuntimeError):
        extract_coverage_slots_via_llm("some email text")


def test_relative_date_hints_anchor_to_the_given_date():
    # 2026-04-08 is a Wednesday.
    hints = _build_relative_date_hints(date(2026, 4, 8))

    assert hints["today"] == "2026-04-08"
    assert hints["tomorrow"] == "2026-04-09"


def test_relative_date_hints_resolve_this_and_next_weekday():
    anchor = date(2026, 4, 8)  # Wednesday
    hints = _build_relative_date_hints(anchor)

    # "this <weekday>" is the next occurrence on/after the anchor, including
    # the anchor's own weekday; "next <weekday>" is a week after that.
    assert hints["this wednesday"] == "2026-04-08"
    assert hints["next wednesday"] == "2026-04-15"
    assert hints["this friday"] == "2026-04-10"
    assert hints["next friday"] == "2026-04-17"


def test_extract_coverage_slots_via_llm_anchors_prompt_to_email_sent_date(
    monkeypatch: pytest.MonkeyPatch,
):
    """The prompt sent to the LLM must resolve relative phrases against the
    email's own send date, not whatever day the flow happens to run --
    otherwise "today" in a three-day-old email would resolve wrong."""

    monkeypatch.setenv("MODEL", "gemini/gemini-1.5-flash")
    captured: dict = {}

    class _FakeMessage:
        content = '{"slots": [{"date": "2026-04-08", "start_time": "17:00", "end_time": "18:00"}], "unstructured_note": null}'

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    def fake_completion(*, model, messages, temperature):
        captured["prompt"] = messages[0]["content"]
        return _FakeResponse()

    monkeypatch.setattr("scheduler_agents.tools.coverage_tool.litellm.completion", fake_completion)

    slots, _ = extract_coverage_slots_via_llm(
        "Can you stay logged in until 5pm today?", anchor_date=date(2026, 4, 8)
    )

    assert "sent on 2026-04-08 (Wednesday)" in captured["prompt"]
    assert '"today": "2026-04-08"' in captured["prompt"]
    assert len(slots) == 1
    assert slots[0].date.isoformat() == "2026-04-08"


def test_resolve_period_phrase_this_and_next_week():
    anchor = date(2026, 4, 8)  # Wednesday

    assert resolve_period_phrase("this week", anchor).isoformat() == "2026-04-06"  # Monday of that week
    assert resolve_period_phrase("next week", anchor).isoformat() == "2026-04-13"


def test_resolve_period_phrase_month_week_prefers_upcoming_occurrence():
    # A real vendor email ("first week of March") received on Aug 23 must
    # resolve to the *next* March, not one that already passed 5 months ago.
    anchor = date(2026, 8, 23)

    start = resolve_period_phrase("first week of March", anchor)

    assert start.isoformat() == "2027-03-01"
    assert start.strftime("%A") == "Monday"


def test_resolve_period_phrase_month_week_honors_explicit_year():
    assert resolve_period_phrase("last week of February 2026", date(2026, 8, 23)).isoformat() == "2026-02-22"


def test_resolve_period_phrase_returns_none_for_unrecognized_text():
    assert resolve_period_phrase("sometime soon", date(2026, 8, 23)) is None


def test_extract_coverage_slots_via_llm_resolves_weekday_slots_against_period(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression test for a real vendor email that lists bare weekday names
    ("Monday: 11am-3pm") under a "first week of March" heading. The LLM
    itself mis-aligned these by one day (Monday's hours landed on the actual
    Sunday) when asked to compute the date directly -- this checks that the
    weekday-name -> date mapping happens in deterministic Python instead."""

    monkeypatch.setenv("MODEL", "gemini/gemini-1.5-flash")

    class _FakeMessage:
        content = json.dumps(
            {
                "slots": [],
                "weekday_slots": [
                    {"weekday": "Monday", "start_time": "11:00", "end_time": "15:00"},
                    {"weekday": "Sunday", "start_time": "13:00", "end_time": "15:00"},
                ],
                "period": "first week of March",
                "unstructured_note": None,
            }
        )

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    monkeypatch.setattr(
        "scheduler_agents.tools.coverage_tool.litellm.completion",
        lambda **kwargs: _FakeResponse(),
    )

    slots, note = extract_coverage_slots_via_llm(
        "irrelevant body", anchor_date=date(2026, 8, 23)
    )

    by_date = {s.date.isoformat(): s for s in slots}
    assert by_date["2027-03-01"].date.strftime("%A") == "Monday"
    assert by_date["2027-03-07"].date.strftime("%A") == "Sunday"
    assert note is None


def test_extract_coverage_slots_via_llm_flags_weekday_slots_with_no_resolvable_period(
    monkeypatch: pytest.MonkeyPatch,
):
    """When the period phrase can't be resolved, weekday slots must not turn
    into guessed dates -- they get surfaced to the human instead."""

    monkeypatch.setenv("MODEL", "gemini/gemini-1.5-flash")

    class _FakeMessage:
        content = json.dumps(
            {
                "slots": [],
                "weekday_slots": [{"weekday": "Monday", "start_time": "11:00", "end_time": "15:00"}],
                "period": "sometime soon",
                "unstructured_note": None,
            }
        )

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    monkeypatch.setattr(
        "scheduler_agents.tools.coverage_tool.litellm.completion",
        lambda **kwargs: _FakeResponse(),
    )

    slots, note = extract_coverage_slots_via_llm("irrelevant body", anchor_date=date(2026, 8, 23))

    assert slots == []
    assert "1 weekday-labeled slot" in note
    assert "sometime soon" in note


def test_extract_coverage_slots_via_llm_flags_weekday_slots_with_no_period_at_all(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression test for a real vendor email with recurring, unbounded
    slots ("Saturdays: 2-4pm") -- no period phrase at all, unlike the
    "sometime soon" case above. The note must read cleanly, not leak
    Python's `None` repr ('period: "None"') into human-facing text."""

    monkeypatch.setenv("MODEL", "gemini/gemini-1.5-flash")

    class _FakeMessage:
        content = json.dumps(
            {
                "slots": [],
                "weekday_slots": [{"weekday": "Saturday", "start_time": "14:00", "end_time": "16:00"}],
                "period": None,
                "unstructured_note": None,
            }
        )

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    monkeypatch.setattr(
        "scheduler_agents.tools.coverage_tool.litellm.completion",
        lambda **kwargs: _FakeResponse(),
    )

    slots, note = extract_coverage_slots_via_llm("irrelevant body", anchor_date=date(2026, 8, 23))

    assert slots == []
    assert "None" not in note
    assert "no period stated" in note


def test_scheduler_flow_declines_when_human_says_no():
    flow = SchedulerFlow(
        sample_email_path=SAMPLE_DATA / "sample_coverage_request_email.txt",
        approved_schedule_path=SAMPLE_DATA / "sample_approved_schedule.json",
        ask_user=lambda slot, conflict: False,
    )

    state = asyncio.run(flow.run_v1_async())

    assert state.email_type == "coverage_request"
    assert len(state.coverage_slots) == 1
    assert state.coverage_decisions[0].conflict is True  # shown to the human, but doesn't decide for them
    assert state.coverage_decisions[0].decision == "decline"
    assert state.coverage_approval_required is True
    assert state.coverage_reply_draft is not None
    assert state.calendar_events == []
    assert any(hook.name == "after_handle_coverage_request" for hook in state.hooks)


def test_scheduler_flow_accepts_and_updates_calendar_when_human_says_yes(tmp_path: Path):
    # Accepting a slot now persists it to approved_schedule_path -- must use
    # an isolated copy, not the real sample_data fixture, or this test would
    # mutate a tracked file on every run.
    approved_schedule_path = tmp_path / "approved_schedule.json"
    approved_schedule_path.write_text(
        (SAMPLE_DATA / "sample_approved_schedule.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    flow = SchedulerFlow(
        sample_email_path=SAMPLE_DATA / "sample_coverage_request_email.txt",
        approved_schedule_path=approved_schedule_path,
        ask_user=lambda slot, conflict: True,
    )

    state = asyncio.run(flow.run_v1_async())

    assert state.coverage_decisions[0].decision == "accept"
    assert "I can cover" in state.coverage_reply_draft
    assert len(state.calendar_events) == 1
    assert state.calendar_events[0]["start"]["dateTime"].startswith("2026-09-10T14:00")

    # The accepted slot itself should now be persisted in the store.
    saved = json.loads(approved_schedule_path.read_text(encoding="utf-8"))
    assert any(item["date"] == "2026-09-10" and item["start_time"] == "14:00:00" for item in saved)


def test_ask_user_receives_the_conflict_flag():
    seen: dict = {}

    def fake_ask_user(slot, conflict):
        seen["conflict"] = conflict
        return False

    flow = SchedulerFlow(
        sample_email_path=SAMPLE_DATA / "sample_coverage_request_email.txt",
        approved_schedule_path=SAMPLE_DATA / "sample_approved_schedule.json",
        ask_user=fake_ask_user,
    )

    asyncio.run(flow.run_v1_async())

    assert seen["conflict"] is True


def test_scheduler_flow_handles_multiple_slots_via_mocked_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Mock llm_is_configured() directly rather than setting real-looking env
    # vars: those would also make classify_email attempt a real (failing)
    # network call, which is exactly the offline-test guarantee conftest.py
    # exists to protect.
    monkeypatch.setattr("scheduler_agents.flows.scheduler_flow.llm_is_configured", lambda: True)

    email_path = tmp_path / "coverage.txt"
    email_path.write_text(
        "Subject: Coverage needed\nFrom: scheduler@example.com\n\n"
        "We need help covering: April 8: 3-4pm and April 9: 10am-11am.\n",
        encoding="utf-8",
    )

    def fake_extract(email_text, anchor_date=None):
        return (
            [
                CoverageSlot(date="2026-04-08", start_time="15:00", end_time="16:00"),
                CoverageSlot(date="2026-04-09", start_time="10:00", end_time="11:00"),
            ],
            "Mon-Wed 11am-1pm all month",
        )

    monkeypatch.setattr(
        "scheduler_agents.flows.scheduler_flow.extract_coverage_slots_via_llm", fake_extract
    )

    decisions_seen = []

    def fake_ask_user(slot, conflict):
        accept = slot.date.isoformat() == "2026-04-08"
        decisions_seen.append((slot.date.isoformat(), accept))
        return accept

    flow = SchedulerFlow(sample_email_path=email_path, ask_user=fake_ask_user)
    state = asyncio.run(flow.run_v1_async())

    assert len(state.coverage_slots) == 2
    assert len(state.coverage_decisions) == 2
    assert state.coverage_unstructured_note == "Mon-Wed 11am-1pm all month"
    assert len(state.calendar_events) == 1  # only the April 8 slot was accepted
    assert "Mon-Wed 11am-1pm all month" in state.coverage_reply_draft


def test_scheduler_flow_reads_date_header_and_passes_it_as_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A 'Date:' header in the source email should become EmailInput.sent_date
    and flow through to extract_coverage_slots_via_llm as anchor_date --
    relative phrases like "today" must resolve against when the email was
    sent, not whatever day the flow happens to run."""

    monkeypatch.setattr("scheduler_agents.flows.scheduler_flow.llm_is_configured", lambda: True)

    email_path = tmp_path / "coverage.txt"
    email_path.write_text(
        "Subject: Coverage needed\nFrom: scheduler@example.com\nDate: 2026-04-08\n\n"
        "Can you stay logged in until 5pm today?\n",
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_extract(email_text, anchor_date=None):
        captured["anchor_date"] = anchor_date
        return [CoverageSlot(date="2026-04-08", start_time="14:00", end_time="17:00")], None

    monkeypatch.setattr(
        "scheduler_agents.flows.scheduler_flow.extract_coverage_slots_via_llm", fake_extract
    )

    flow = SchedulerFlow(sample_email_path=email_path, ask_user=lambda slot, conflict: False)
    state = asyncio.run(flow.run_v1_async())

    assert state.email.sent_date.isoformat() == "2026-04-08"
    assert captured["anchor_date"].isoformat() == "2026-04-08"


def test_accepting_a_slot_flags_a_later_overlapping_slot_in_the_same_email(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A coverage email can offer two overlapping slots at once. Accepting
    the first must be treated as a real commitment immediately -- not just
    persisted for *future* emails -- so the second slot's conflict check
    (still in the same loop) sees it too."""

    monkeypatch.setattr("scheduler_agents.flows.scheduler_flow.llm_is_configured", lambda: True)

    email_path = tmp_path / "coverage.txt"
    email_path.write_text(
        "Subject: Coverage needed\nFrom: scheduler@example.com\n\nOverlapping slots.\n",
        encoding="utf-8",
    )

    def fake_extract(email_text, anchor_date=None):
        return (
            [
                CoverageSlot(date="2026-09-20", start_time="09:00", end_time="12:00"),
                CoverageSlot(date="2026-09-20", start_time="11:00", end_time="13:00"),
            ],
            None,
        )

    monkeypatch.setattr(
        "scheduler_agents.flows.scheduler_flow.extract_coverage_slots_via_llm", fake_extract
    )

    conflicts_seen = []

    def fake_ask_user(slot, conflict):
        conflicts_seen.append(conflict)
        return True  # accept both

    approved_schedule_path = tmp_path / "approved_schedule.json"
    flow = SchedulerFlow(
        sample_email_path=email_path,
        approved_schedule_path=approved_schedule_path,
        ask_user=fake_ask_user,
    )
    state = asyncio.run(flow.run_v1_async())

    assert conflicts_seen == [False, True]  # first slot clean, second overlaps the just-accepted first
    assert len(state.calendar_events) == 2

    saved = json.loads(approved_schedule_path.read_text(encoding="utf-8"))
    assert len(saved) == 2
