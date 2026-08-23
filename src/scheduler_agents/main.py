from __future__ import annotations

import asyncio
import argparse
import shutil
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
    parser.add_argument(
        "--roster-image",
        default=None,
        help=(
            "Path to a roster screenshot to use for schedule extraction when the "
            "email body has no parseable dates. Defaults to sample_data/sample_roster.png."
        ),
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

    approved_schedule_path = project_root / "outputs" / "approved_schedule.json"
    if not approved_schedule_path.exists():
        # First run: seed from the sample fixture so the coverage-request
        # demo shows a real conflict warning out of the box. Every run after
        # that reads/writes this file directly -- this is the one-time
        # bootstrap, not something later runs repeat.
        approved_schedule_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(project_root / "sample_data" / "sample_approved_schedule.json", approved_schedule_path)
    timesheet_pdf = project_root / "sample_data" / "sample_purchase_order.pdf"
    invoice_template = project_root / "sample_data" / "invoice_template.docx"
    roster_image = Path(args.roster_image) if args.roster_image else project_root / "sample_data" / "sample_roster.png"
    flow = SchedulerFlow(
        sample_email_path=sample_email,
        approved_schedule_path=approved_schedule_path,
        timesheet_pdf_path=timesheet_pdf,
        invoice_template_path=invoice_template,
        invoice_output_dir=project_root / "outputs",
        roster_image_path=roster_image,
    )
    state = asyncio.run(flow.run_v1_async())
    output_paths = write_flow_outputs(state, project_root / "outputs")

    print(f"Run id: {state.run_id}")
    print(f"Sample email: {sample_email}")
    print(f"Email type: {state.email_type}")

    if state.email_type == "schedule":
        print(f"Extracted events: {len(state.extracted_events)}")
        if state.roster_timezone_label:
            print(f"Roster timezone label: {state.roster_timezone_label}")
        print(f"Validation errors: {state.validation_errors}")
        print(f"Calendar payloads: {len(state.calendar_events)}")
        for event in state.calendar_events:
            print(event)
    elif state.email_type == "coverage_request":
        print(f"Coverage slots found: {len(state.coverage_slots)}")
        for d in state.coverage_decisions:
            print(f"  {d.slot.date} {d.slot.start_time}-{d.slot.end_time}: {d.decision} (conflict={d.conflict})")
        if state.coverage_unstructured_note:
            print(f"Unstructured (non-dated) request noted: {state.coverage_unstructured_note}")
        print(f"Approval required before sending: {state.coverage_approval_required}")
        print(f"Reply draft:\n{state.coverage_reply_draft}")
    elif state.email_type == "availability_request":
        print(f"Requested period: {state.availability_period}")
        print(f"Approval required before sending: {state.availability_approval_required}")
        print(f"Reply draft:\n{state.availability_reply_draft}")
    elif state.email_type == "timesheet":
        print(f"Timesheet data: {state.timesheet_data}")
        print(f"Approval required before submitting: {state.timesheet_approval_required}")
        print(f"Filled invoice saved to: {state.invoice_output_path}")

    print(f"Saved calendar payloads: {output_paths['calendar_payloads']}")
    print(f"Saved flow state: {output_paths['flow_state']}")


if __name__ == "__main__":
    main()
