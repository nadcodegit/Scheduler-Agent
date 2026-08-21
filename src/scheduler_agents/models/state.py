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


class ScheduleEvent(BaseModel):
    date: date
    start_time: time
    end_time: time
    language: str | None = None
    title: str = "Interpretation Session"
    source: str = "email"

    @field_validator("end_time")
    @classmethod
    def end_time_must_be_after_start(cls, end_time: time, info: Any) -> time:
        start_time = info.data.get("start_time")
        if start_time and end_time <= start_time:
            raise ValueError("end_time must be after start_time")
        return end_time


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

