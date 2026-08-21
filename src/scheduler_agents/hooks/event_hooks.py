from __future__ import annotations

from typing import Any

from scheduler_agents.models.state import FlowEvent, SchedulerFlowState


def record_hook(state: SchedulerFlowState, name: str, **details: Any) -> None:
    """Record lightweight lifecycle events for debugging and observability."""

    state.hooks.append(FlowEvent(name=name, details=details))

