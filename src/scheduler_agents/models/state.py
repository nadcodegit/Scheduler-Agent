from __future__ import annotations

from datetime import date, time
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class EmailType(StrEnum):
    SCHEDULE = "schedule"
    COVERAGE_REQUEST = "coverage_request"
    AVAILABILITY_REQUEST = "availability_request"
    TIMESHEET = "timesheet"
    OTHER = "other"


class EmailInput(BaseModel):
    subject: str
    sender: str
    body: str
    attachments: list[str] = Field(default_factory=list)
    # The day the email was actually sent, when known (parsed from a "Date:"
    # header). Relative phrases in the body ("today", "next Monday") must
    # anchor to this, not to whatever day the flow happens to run -- those
    # can be days apart. None when the source has no such header; callers
    # fall back to date.today().
    sent_date: date | None = None


class TimeSlot(BaseModel):
    """A single date/start/end/language slot, shared by schedule events and
    coverage requests -- both are "one block of interpretation time", just
    reached through different email types."""

    date: date
    start_time: time
    end_time: time
    language: str | None = None
    source: str = "email"

    @field_validator("end_time")
    @classmethod
    def end_time_must_be_after_start(cls, end_time: time, info: Any) -> time:
        start_time = info.data.get("start_time")
        if start_time and end_time <= start_time:
            raise ValueError("end_time must be after start_time")
        return end_time


class ScheduleEvent(TimeSlot):
    title: str = "Interpretation Session"


class CoverageDecision(StrEnum):
    ACCEPT = "accept"
    DECLINE = "decline"


class CoverageSlot(TimeSlot):
    pass


class CoverageSlotDecision(BaseModel):
    """One slot's outcome: the slot itself, whether it conflicted with the
    existing calendar (context only, not the decision), and what the human
    said. A real coverage email can offer several distinct slots at once,
    so the flow tracks one of these per slot rather than a single decision."""

    slot: CoverageSlot
    conflict: bool
    decision: CoverageDecision


class TimesheetData(BaseModel):
    """Extracted from the vendor's monthly Purchase Order PDF -- the email
    body carries no usable data, only this attachment does."""

    job_id: str
    period: str
    total_amount: float


class FlowEvent(BaseModel):
    name: str
    details: dict[str, Any] = Field(default_factory=dict)


class SchedulerFlowState(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    email: EmailInput | None = None
    email_type: EmailType | None = None
    extracted_events: list[ScheduleEvent] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    approval_required: bool = False
    calendar_events: list[dict[str, Any]] = Field(default_factory=list)
    memory_snapshot: dict[str, Any] = Field(default_factory=dict)
    hooks: list[FlowEvent] = Field(default_factory=list)
    used_llm: bool = False
    # Timezone label read off a roster image (e.g. "UK"), when schedule data
    # came from vision extraction rather than email text. The roster's own
    # timezone can differ from the interpreter's default (UserMemory.timezone).
    roster_timezone_label: str | None = None

    # Coverage-request workflow (V2)
    coverage_slots: list[CoverageSlot] = Field(default_factory=list)
    coverage_decisions: list[CoverageSlotDecision] = Field(default_factory=list)
    coverage_reply_draft: str | None = None
    coverage_approval_required: bool = False
    # A real coverage email can also make a vague, non-dated appeal (e.g.
    # "we need help Mon-Wed 11am-1pm all month") alongside specific-dated
    # slots. That can't be safely turned into calendar dates, so it's kept
    # here as a note for the human rather than silently dropped.
    coverage_unstructured_note: str | None = None

    # Availability-request workflow (V3)
    availability_period: str | None = None
    availability_statement: str | None = None
    availability_reply_draft: str | None = None
    availability_approval_required: bool = False

    # Timesheet/invoice workflow (V4)
    timesheet_data: TimesheetData | None = None
    invoice_output_path: str | None = None
    timesheet_approval_required: bool = False
    # Set by receive_email() when live Gmail found a PDF attachment on the
    # fetched message and saved it locally -- handle_timesheet() prefers
    # this over the static timesheet_pdf_path fallback when set. None means
    # no live attachment was found this run (not "use the local sample" --
    # that distinction matters so a stale downloaded file from a previous
    # run is never silently reused).
    live_pdf_attachment_path: str | None = None

