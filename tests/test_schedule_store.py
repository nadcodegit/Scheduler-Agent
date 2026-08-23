from __future__ import annotations

import json
from pathlib import Path

from scheduler_agents.models.state import ScheduleEvent
from scheduler_agents.tools.schedule_store import load_approved_schedule, save_approved_schedule


def test_load_approved_schedule_returns_empty_list_for_missing_file(tmp_path: Path):
    assert load_approved_schedule(tmp_path / "does_not_exist.json") == []


def test_save_approved_schedule_writes_and_round_trips(tmp_path: Path):
    path = tmp_path / "approved_schedule.json"
    events = [ScheduleEvent(date="2026-09-10", start_time="14:00", end_time="16:00", language="English")]

    save_approved_schedule(events, path)

    loaded = load_approved_schedule(path)
    assert len(loaded) == 1
    assert loaded[0].date.isoformat() == "2026-09-10"
    assert loaded[0].language == "English"


def test_save_approved_schedule_merges_without_duplicating_existing_entries(tmp_path: Path):
    path = tmp_path / "approved_schedule.json"
    first = [ScheduleEvent(date="2026-09-10", start_time="14:00", end_time="16:00")]
    second = [
        ScheduleEvent(date="2026-09-10", start_time="14:00", end_time="16:00"),  # exact repeat
        ScheduleEvent(date="2026-09-12", start_time="09:00", end_time="11:00"),  # genuinely new
    ]

    save_approved_schedule(first, path)
    merged = save_approved_schedule(second, path)

    assert len(merged) == 2
    dates = sorted(e.date.isoformat() for e in merged)
    assert dates == ["2026-09-10", "2026-09-12"]


def test_save_approved_schedule_persists_across_separate_loads(tmp_path: Path):
    path = tmp_path / "approved_schedule.json"

    save_approved_schedule([ScheduleEvent(date="2026-09-10", start_time="14:00", end_time="16:00")], path)
    save_approved_schedule([ScheduleEvent(date="2026-09-12", start_time="09:00", end_time="11:00")], path)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert len(on_disk) == 2
