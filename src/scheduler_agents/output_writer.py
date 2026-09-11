from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from scheduler_agents.models.state import CoverageDecision, SchedulerFlowState
from scheduler_agents.tools.ics_tool import build_ics_calendar


def summarize_run(state: SchedulerFlowState) -> dict[str, Any]:
    """Condenses a full SchedulerFlowState into one small, GUI-friendly
    record -- the desktop app's History tab reads a stream of these rather
    than re-parsing the much larger flow_state.json (or the terminal's Rich
    output) for every past run.
    """

    source = "unknown"
    fetch_error: str | None = None
    for event in state.hooks:
        if event.name == "after_receive_email" and event.details.get("source") in ("gmail", "sample"):
            source = event.details["source"]
        elif event.name == "gmail_fetch_failed":
            fetch_error = str(event.details.get("error"))

    email_type = str(state.email_type) if state.email_type else None
    needs_attention = state.coverage_needs_attention or state.availability_needs_attention
    gmail_draft_id = state.coverage_gmail_draft_id or state.availability_gmail_draft_id

    conversation: dict[str, Any] | None = None

    if needs_attention:
        summary = "Needs your attention -- no interactive terminal was available to ask you."
    elif email_type == "schedule":
        summary = f"{len(state.extracted_events)} event(s) extracted, {len(state.validation_errors)} validation error(s)."
    elif email_type == "coverage_request":
        accepted = sum(1 for d in state.coverage_decisions if d.decision == CoverageDecision.ACCEPT)
        summary = f"{len(state.coverage_decisions)} slot(s) decided, {accepted} accepted."
        if state.coverage_decisions:
            conversation = {
                "exchanges": [
                    {
                        "question": (
                            f"Can you cover {d.slot.date} {d.slot.start_time}-{d.slot.end_time} "
                            f"({d.slot.language or 'language n/a'})? "
                            f"Conflict with an existing commitment: {'yes' if d.conflict else 'no'}."
                        ),
                        "answer": "Yes, I can cover it." if d.decision == CoverageDecision.ACCEPT else "No, I can't.",
                    }
                    for d in state.coverage_decisions
                ],
                "draft": state.coverage_reply_draft,
            }
    elif email_type == "availability_request":
        summary = f"Stated availability for {state.availability_period or 'an unspecified period'}."
        if state.availability_statement:
            conversation = {
                "exchanges": [
                    {
                        "question": f"What's your availability for {state.availability_period or 'the requested period'}?",
                        "answer": state.availability_statement,
                    }
                ],
                "draft": state.availability_reply_draft,
            }
    elif email_type == "timesheet":
        summary = (
            f"Invoice generated: {Path(state.invoice_output_path).name}"
            if state.invoice_output_path
            else "Purchase Order could not be parsed."
        )
    else:
        summary = "No action needed."

    return {
        "run_id": state.run_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "email_type": email_type,
        "subject": state.email.subject if state.email else None,
        "sender": state.email.sender if state.email else None,
        "source": source,
        "fetch_error": fetch_error,
        "needs_attention": needs_attention,
        "gmail_draft_id": gmail_draft_id,
        "invoice_output_path": state.invoice_output_path if email_type == "timesheet" else None,
        "summary": summary,
        "conversation": conversation,
    }


def append_run_history(state: SchedulerFlowState, output_dir: Path) -> Path:
    """Appends one summarize_run() record as a line to outputs/run_history.jsonl
    -- JSON Lines rather than a single JSON array so each run is a plain
    atomic append, no read-modify-write of a growing file needed. Gitignored:
    like scheduled_run.log, this accumulates real email content over time.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    history_path = output_dir / "run_history.jsonl"
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(summarize_run(state), ensure_ascii=False))
        handle.write("\n")
    return history_path


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

