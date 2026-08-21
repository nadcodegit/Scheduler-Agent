from __future__ import annotations

import asyncio
import argparse
import sys
from pathlib import Path

# crewai's console output includes emoji; Windows' default console codepage
# (cp1252) can't encode them, which otherwise spams non-fatal "charmap codec"
# errors from crewai's own internal event handlers.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

from scheduler_agents.flows.scheduler_flow import SchedulerFlow
from scheduler_agents.output_writer import write_flow_outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Scheduler Agents flow.")
    parser.add_argument(
        "--sample",
        default="sample_schedule_email.txt",
        help="Sample email filename from sample_data, or an absolute path.",
    )
    return parser.parse_args()


def main() -> None:
    if load_dotenv is not None:
        load_dotenv()
    args = parse_args()
    project_root = Path(__file__).resolve().parents[2]
    sample_email = Path(args.sample)
    if not sample_email.is_absolute():
        sample_email = project_root / "sample_data" / sample_email

    busy_calendar = project_root / "sample_data" / "sample_busy_calendar.json"
    flow = SchedulerFlow(sample_email_path=sample_email, busy_calendar_path=busy_calendar)
    state = asyncio.run(flow.run_v1_async())
    output_paths = write_flow_outputs(state, project_root / "outputs")

    print(f"Run id: {state.run_id}")
    print(f"Sample email: {sample_email}")
    print(f"Email type: {state.email_type}")

    if state.email_type == "schedule":
        print(f"Extracted events: {len(state.extracted_events)}")
        print(f"Validation errors: {state.validation_errors}")
        print(f"Calendar payloads: {len(state.calendar_events)}")
        for event in state.calendar_events:
            print(event)
    elif state.email_type == "coverage_request":
        print(f"Coverage slot: {state.coverage_slot}")
        print(f"Conflict with existing calendar: {state.coverage_conflict}")
        print(f"Decision: {state.coverage_decision}")
        print(f"Approval required before sending: {state.coverage_approval_required}")
        print(f"Reply draft:\n{state.coverage_reply_draft}")
        if state.coverage_decision == "accept":
            print(f"Added to calendar_events: {state.calendar_events[-1]}")

    print(f"Saved calendar payloads: {output_paths['calendar_payloads']}")
    print(f"Saved flow state: {output_paths['flow_state']}")


if __name__ == "__main__":
    main()
