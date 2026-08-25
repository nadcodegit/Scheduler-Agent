from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from scheduler_agents.models.state import SchedulerFlowState
from scheduler_agents.tools.ics_tool import build_ics_calendar


def write_flow_outputs(state: SchedulerFlowState, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    calendar_payloads_path = output_dir / "calendar_payloads.json"
    flow_state_path = output_dir / "flow_state.json"
    ics_path = output_dir / "schedule.ics"

    _write_json(calendar_payloads_path, state.calendar_events)
    _write_json(flow_state_path, state)
    # Shared by V1 (a month's approved schedule) and V2 (accepted coverage
    # slots) -- both already write into the same calendar_events list, so
    # this one file covers whichever workflow actually ran. A real,
    # double-clickable calendar file, still with zero connection to any
    # real calendar account/API.
    ics_path.write_bytes(build_ics_calendar(state.calendar_events))

    return {
        "calendar_payloads": calendar_payloads_path,
        "flow_state": flow_state_path,
        "ics": ics_path,
    }


def _write_json(path: Path, value: Any) -> None:
    if isinstance(value, BaseModel):
        data = value.model_dump(mode="json")
    else:
        data = value

    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

