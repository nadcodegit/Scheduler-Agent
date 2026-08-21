from __future__ import annotations

import asyncio
from pathlib import Path

try:
    from crewai.flow.flow import Flow, listen, router, start
except ImportError:  # pragma: no cover
    class Flow:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

        def __class_getitem__(cls, _item):
            return cls

    def start():
        return lambda fn: fn

    def listen(_source):
        return lambda fn: fn

    def router(_source):
        return lambda fn: fn

from scheduler_agents.crews.schedule_crew.crew import llm_is_configured, run_llm_pipeline
from scheduler_agents.guardrails.schedule_guardrails import validate_schedule_events
from scheduler_agents.hooks.event_hooks import record_hook
from scheduler_agents.memory.user_memory import UserMemory
from scheduler_agents.models.state import EmailInput, EmailType, ScheduleEvent, SchedulerFlowState
from scheduler_agents.tools.calendar_tool import build_calendar_event
from scheduler_agents.tools.schedule_parser_tool import parse_schedule_text


class SchedulerFlow(Flow[SchedulerFlowState]):
    """V1 CrewAI Flow for schedule email processing."""

    def __init__(self, sample_email_path: str | Path | None = None, memory: UserMemory | None = None):
        super().__init__()
        # Real crewai Flow instances auto-create self.state (a read-only property)
        # from the Flow[SchedulerFlowState] generic during super().__init__(). The
        # ImportError fallback stub above does not, so it still needs this.
        if getattr(self, "state", None) is None:
            self.state = SchedulerFlowState()
        self.sample_email_path = Path(sample_email_path) if sample_email_path else None
        self.memory = memory or UserMemory()

    @start()
    def receive_email(self) -> EmailInput:
        record_hook(self.state, "before_receive_email")
        raw_email = self._read_sample_email()
        subject, sender, body = self._split_email(raw_email)

        self.state.email = EmailInput(subject=subject, sender=sender, body=body)
        self.state.memory_snapshot = self.memory.snapshot()
        record_hook(self.state, "after_receive_email", subject=subject, sender=sender)
        return self.state.email

    @listen(receive_email)
    async def classify_email(self, _email: EmailInput | None = None) -> EmailType:
        record_hook(self.state, "before_classify_email")
        email = self._require_email()

        if llm_is_configured():
            try:
                await self._classify_and_extract_with_llm(email)
                record_hook(
                    self.state,
                    "after_classify_email",
                    email_type=self.state.email_type.value,
                    mode="llm",
                )
                return self.state.email_type
            except Exception as exc:  # LLM/network failures fall back, never crash the flow
                record_hook(self.state, "llm_pipeline_failed", error=str(exc))

        self._classify_with_regex(email)
        record_hook(self.state, "after_classify_email", email_type=self.state.email_type.value, mode="regex")
        return self.state.email_type

    @router(classify_email)
    def route_email(self) -> str:
        record_hook(self.state, "route_email", email_type=(self.state.email_type or EmailType.OTHER).value)
        return (self.state.email_type or EmailType.OTHER).value

    @listen("schedule")
    def parse_schedule(self) -> list[object]:
        record_hook(self.state, "before_parse_schedule")
        if self.state.used_llm:
            record_hook(self.state, "parse_schedule_skipped", reason="already_extracted_by_llm")
            return self.state.extracted_events

        email = self._require_email()
        self.state.extracted_events = parse_schedule_text(email.body)
        record_hook(self.state, "after_parse_schedule", event_count=len(self.state.extracted_events))
        return self.state.extracted_events

    @listen(parse_schedule)
    def validate_schedule(self, _events: list[object] | None = None) -> list[str]:
        record_hook(self.state, "before_validate_schedule")
        if self.state.used_llm:
            record_hook(self.state, "validate_schedule_skipped", reason="already_validated_by_llm")
            return self.state.validation_errors

        self.state.validation_errors = validate_schedule_events(self.state.extracted_events)
        self.state.approval_required = bool(self.state.validation_errors)
        record_hook(
            self.state,
            "after_validate_schedule",
            error_count=len(self.state.validation_errors),
            approval_required=self.state.approval_required,
        )
        return self.state.validation_errors

    @listen(validate_schedule)
    async def create_calendar_events(self, _errors: list[str] | None = None) -> list[dict[str, object]]:
        record_hook(self.state, "before_create_calendar_events")

        if self.state.validation_errors:
            record_hook(self.state, "calendar_creation_blocked", reason="validation_errors")
            return []

        timezone = str(self.state.memory_snapshot.get("timezone", "Asia/Yerevan"))
        await asyncio.sleep(0)
        self.state.calendar_events = [
            build_calendar_event(event, timezone=timezone)
            for event in self.state.extracted_events
        ]

        record_hook(self.state, "after_create_calendar_events", event_count=len(self.state.calendar_events))
        return self.state.calendar_events

    async def run_v1_async(self) -> SchedulerFlowState:
        """Deterministic async runner mirroring the CrewAI Flow order for V1."""

        email = self.receive_email()
        email_type = await self.classify_email(email)
        if email_type == EmailType.SCHEDULE:
            events = self.parse_schedule()
            errors = self.validate_schedule(events)
            await self.create_calendar_events(errors)
        return self.state

    def _read_sample_email(self) -> str:
        if self.sample_email_path and self.sample_email_path.exists():
            return self.sample_email_path.read_text(encoding="utf-8")

        return (
            "Subject: Your September schedule is ready\n"
            "From: scheduler@example.com\n\n"
            "Sep 1, 2026, 09:00-12:00, English\n"
        )

    @staticmethod
    def _split_email(raw_email: str) -> tuple[str, str, str]:
        subject = ""
        sender = ""
        body_lines: list[str] = []

        for line in raw_email.splitlines():
            if line.lower().startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
            elif line.lower().startswith("from:"):
                sender = line.split(":", 1)[1].strip()
            else:
                body_lines.append(line)

        return subject, sender, "\n".join(body_lines).strip()

    def _require_email(self) -> EmailInput:
        if self.state.email is None:
            raise RuntimeError("Email has not been loaded into flow state.")
        return self.state.email

    def _classify_with_regex(self, email: EmailInput) -> None:
        """Offline, zero-cost fallback classifier used when no LLM key is configured."""

        text = f"{email.subject}\n{email.body}".lower()

        if "schedule" in text:
            self.state.email_type = EmailType.SCHEDULE
        elif "cover" in text or "available slot" in text:
            self.state.email_type = EmailType.COVERAGE_REQUEST
        elif "availability" in text:
            self.state.email_type = EmailType.AVAILABILITY_REQUEST
        elif "hours" in text or "timesheet" in text:
            self.state.email_type = EmailType.TIMESHEET
        else:
            self.state.email_type = EmailType.OTHER

    async def _classify_and_extract_with_llm(self, email: EmailInput) -> None:
        """Run the inbox/parser/validator CrewAI agents for this email.

        Populates email_type, and when the email is a schedule email, also
        extracted_events, validation_errors, and approval_required. The
        deterministic guardrail is always re-run over the LLM's extracted
        events rather than trusting the LLM's own validation task, so unsafe
        or malformed data never reaches calendar creation.
        """

        email_text = f"Subject: {email.subject}\nFrom: {email.sender}\n\n{email.body}"
        pipeline = await run_llm_pipeline(email_text)

        try:
            self.state.email_type = EmailType(pipeline.email_type)
        except ValueError:
            self.state.email_type = EmailType.OTHER

        self.state.used_llm = True

        if self.state.email_type != EmailType.SCHEDULE:
            return

        events: list[ScheduleEvent] = []
        parse_errors: list[str] = []
        for index, raw_event in enumerate(pipeline.raw_events, start=1):
            try:
                events.append(ScheduleEvent(**raw_event))
            except Exception as exc:
                parse_errors.append(f"LLM event {index} failed validation: {exc}")

        self.state.extracted_events = events
        self.state.validation_errors = parse_errors + validate_schedule_events(events)
        self.state.approval_required = bool(self.state.validation_errors)
