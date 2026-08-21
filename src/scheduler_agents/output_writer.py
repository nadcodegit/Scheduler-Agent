from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from scheduler_agents.models.state import SchedulerFlowState


def write_flow_outputs(state: SchedulerFlowState, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    calendar_payloads_path = output_dir / "calendar_payloads.json"
    flow_state_path = output_dir / "flow_state.json"

    _write_json(calendar_payloads_path, state.calendar_events)
    _write_json(flow_state_path, state)

    return {
        "calendar_payloads": calendar_payloads_path,
        "flow_state": flow_state_path,
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

