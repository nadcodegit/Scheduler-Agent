from __future__ import annotations

import asyncio
import json as json_lib
from pathlib import Path

import pytest

from scheduler_agents.flows.scheduler_flow import SchedulerFlow
from scheduler_agents.memory.user_memory import UserMemory
from scheduler_agents.models.state import ScheduleEvent
from scheduler_agents.tools.roster_vision_tool import (
    is_vision_configured,
    parse_roster_image,
    resolve_timezone,
)

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample_data"


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


def _fake_completion_returning(payload: dict, *, wrap_in_fence: bool = False):
    content = json_lib.dumps(payload)
    if wrap_in_fence:
        content = f"```json\n{content}\n```"

    def fake_completion(**kwargs):
        return _FakeResponse(content)

    return fake_completion


def test_resolve_timezone_maps_known_label():
    assert resolve_timezone("UK", default="Asia/Yerevan") == "Europe/London"


def test_resolve_timezone_is_case_insensitive():
    assert resolve_timezone("uk", default="Asia/Yerevan") == "Europe/London"


def test_resolve_timezone_falls_back_to_default_for_unknown_label():
    assert resolve_timezone("MARS", default="Asia/Yerevan") == "Asia/Yerevan"


def test_resolve_timezone_falls_back_when_label_is_none():
    assert resolve_timezone(None, default="Asia/Yerevan") == "Asia/Yerevan"


def test_is_vision_configured_reflects_any_candidate_provider(monkeypatch: pytest.MonkeyPatch):
    for env_var in ("GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)
    assert is_vision_configured() is False

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    assert is_vision_configured() is True


def test_parse_roster_image_raises_without_any_provider_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    for env_var in ("GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)
    fake_image = tmp_path / "roster.png"
    fake_image.write_bytes(b"not a real png, just needs to exist")

    with pytest.raises(RuntimeError):
        parse_roster_image(fake_image)


def test_parse_roster_image_builds_events_from_model_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    fake_image = tmp_path / "roster.png"
    fake_image.write_bytes(b"not a real png, just needs to exist")

    payload = {
        "timezone_label": "UK",
        "events": [
            {"date": "2026-05-01", "start_time": "09:00", "end_time": "10:00"},
            {"date": "2026-05-04", "start_time": "09:00", "end_time": "10:00"},
        ],
    }
    monkeypatch.setattr(
        "scheduler_agents.tools.roster_vision_tool.litellm.completion",
        _fake_completion_returning(payload, wrap_in_fence=True),
    )

    events, timezone_label = parse_roster_image(fake_image)

    assert timezone_label == "UK"
    assert len(events) == 2
    assert all(isinstance(e, ScheduleEvent) for e in events)
    assert events[0].date.isoformat() == "2026-05-01"
    assert events[0].source == "roster_image"


def test_parse_roster_image_skips_malformed_event_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    fake_image = tmp_path / "roster.png"
    fake_image.write_bytes(b"fake")

    payload = {
        "timezone_label": "UK",
        "events": [
            {"date": "2026-05-01", "start_time": "09:00", "end_time": "10:00"},
            {"date": "not-a-date", "start_time": "09:00", "end_time": "10:00"},
        ],
    }
    monkeypatch.setattr(
        "scheduler_agents.tools.roster_vision_tool.litellm.completion",
        _fake_completion_returning(payload),
    )

    events, _ = parse_roster_image(fake_image)

    assert len(events) == 1
    assert events[0].date.isoformat() == "2026-05-01"


def test_parse_roster_image_falls_back_to_next_provider_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Groq configured but failing (e.g. rate limit, bad key) must not be
    the end of the road when a second vision-capable provider is also
    configured -- this is the actual "provider fallback" every other
    extraction path in this project already has."""

    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    fake_image = tmp_path / "roster.png"
    fake_image.write_bytes(b"fake")

    payload = {"timezone_label": "UK", "events": [{"date": "2026-05-01", "start_time": "09:00", "end_time": "10:00"}]}
    calls: list[str] = []

    def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"].startswith("groq/"):
            raise RuntimeError("rate limit exceeded")
        return _FakeResponse(json_lib.dumps(payload))

    monkeypatch.setattr("scheduler_agents.tools.roster_vision_tool.litellm.completion", fake_completion)

    events, timezone_label = parse_roster_image(fake_image)

    assert calls == ["groq/qwen/qwen3.6-27b", "gpt-4o-mini"]  # tried Groq first, fell back to OpenAI
    assert timezone_label == "UK"
    assert len(events) == 1


def test_parse_roster_image_raises_when_every_configured_provider_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    fake_image = tmp_path / "roster.png"
    fake_image.write_bytes(b"fake")

    def failing_completion(**kwargs):
        raise RuntimeError("network error")

    monkeypatch.setattr("scheduler_agents.tools.roster_vision_tool.litellm.completion", failing_completion)

    with pytest.raises(RuntimeError):
        parse_roster_image(fake_image)


def test_scheduler_flow_falls_back_to_roster_image_when_body_has_no_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    email_path = tmp_path / "roster_email.txt"
    email_path.write_text(
        "Subject: May Roster\nFrom: scheduler@example.com\n\nPlease review the attached roster.\n",
        encoding="utf-8",
    )
    fake_image = tmp_path / "roster.png"
    fake_image.write_bytes(b"fake")

    def fake_parse_roster_image(path):
        return (
            [ScheduleEvent(date="2026-05-01", start_time="09:00", end_time="10:00", source="roster_image")],
            "UK",
        )

    monkeypatch.setattr(
        "scheduler_agents.flows.scheduler_flow.parse_roster_image", fake_parse_roster_image
    )

    flow = SchedulerFlow(sample_email_path=email_path, roster_image_path=fake_image)
    state = asyncio.run(flow.run_v1_async())

    assert state.email_type == "schedule"
    assert len(state.extracted_events) == 1
    assert state.roster_timezone_label == "UK"
    assert any(hook.name == "roster_image_parsed" for hook in state.hooks)


def test_scheduler_flow_uses_roster_timezone_for_calendar_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    email_path = tmp_path / "roster_email.txt"
    email_path.write_text(
        "Subject: May Roster\nFrom: scheduler@example.com\n\nPlease review the attached roster.\n",
        encoding="utf-8",
    )
    fake_image = tmp_path / "roster.png"
    fake_image.write_bytes(b"fake")

    def fake_parse_roster_image(path):
        return (
            [
                ScheduleEvent(
                    date="2026-05-01",
                    start_time="09:00",
                    end_time="10:00",
                    language="English",
                    source="roster_image",
                )
            ],
            "UK",
        )

    monkeypatch.setattr(
        "scheduler_agents.flows.scheduler_flow.parse_roster_image", fake_parse_roster_image
    )

    flow = SchedulerFlow(sample_email_path=email_path, roster_image_path=fake_image)
    state = asyncio.run(flow.run_v1_async())

    assert state.validation_errors == []
    assert len(state.calendar_events) == 1
    assert state.calendar_events[0]["start"]["timeZone"] == "Europe/London"


def test_scheduler_flow_defaults_missing_language_from_user_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Real Glocco roster screenshots never have a language column -- the
    vendor relationship is Persian-only, so it's implicit, never written
    anywhere in the source. Regression test: parse_roster_image's own
    prompt correctly never asks for language, so every real roster event
    comes back with language=None; the guardrail must not false-positive
    block on that, and should default it from UserMemory instead."""

    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    email_path = tmp_path / "roster_email.txt"
    email_path.write_text(
        "Subject: Free slot\nFrom: scheduler@glocco.com\n\nPlease review the attached roster.\n",
        encoding="utf-8",
    )
    fake_image = tmp_path / "roster.png"
    fake_image.write_bytes(b"fake")

    def fake_parse_roster_image(path):
        # No language kwarg -- matches parse_roster_image's real prompt/
        # ScheduleEvent construction, which never asks the vision model for one.
        return (
            [ScheduleEvent(date="2026-05-01", start_time="09:00", end_time="10:00", source="roster_image")],
            "UK",
        )

    monkeypatch.setattr(
        "scheduler_agents.flows.scheduler_flow.parse_roster_image", fake_parse_roster_image
    )

    flow = SchedulerFlow(sample_email_path=email_path, roster_image_path=fake_image)
    state = asyncio.run(flow.run_v1_async())

    assert state.validation_errors == []
    assert state.extracted_events[0].language == "Persian"
    assert len(state.calendar_events) == 1
    assert "Language: Persian" in state.calendar_events[0]["description"]


def test_missing_language_default_reads_from_user_memory_not_hardcoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The default isn't hardcoded to "Persian" in the flow -- it comes from
    UserMemory.default_language, so a different configured user gets their
    own default instead."""

    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    email_path = tmp_path / "roster_email.txt"
    email_path.write_text(
        "Subject: Free slot\nFrom: scheduler@glocco.com\n\nPlease review the attached roster.\n",
        encoding="utf-8",
    )
    fake_image = tmp_path / "roster.png"
    fake_image.write_bytes(b"fake")

    def fake_parse_roster_image(path):
        return (
            [ScheduleEvent(date="2026-05-01", start_time="09:00", end_time="10:00", source="roster_image")],
            "UK",
        )

    monkeypatch.setattr(
        "scheduler_agents.flows.scheduler_flow.parse_roster_image", fake_parse_roster_image
    )

    flow = SchedulerFlow(
        sample_email_path=email_path,
        roster_image_path=fake_image,
        memory=UserMemory(default_language="French"),
    )
    state = asyncio.run(flow.run_v1_async())

    assert state.extracted_events[0].language == "French"
