from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path
from typing import Callable

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
from scheduler_agents.models.state import (
    CoverageDecision,
    CoverageSlot,
    CoverageSlotDecision,
    EmailInput,
    EmailType,
    ScheduleEvent,
    SchedulerFlowState,
)
from scheduler_agents.tools.availability_tool import draft_availability_reply, extract_requested_period
from scheduler_agents.tools.calendar_tool import build_calendar_event
from scheduler_agents.tools.coverage_tool import (
    describe_slot,
    draft_coverage_reply_multi,
    extract_coverage_slots_via_llm,
    has_conflict,
    parse_coverage_request_regex,
)
from scheduler_agents.tools.gmail_tool import fetch_latest_email, is_live_gmail_enabled
from scheduler_agents.tools.invoice_tool import fill_invoice_template
from scheduler_agents.tools.roster_vision_tool import parse_roster_image, resolve_timezone
from scheduler_agents.tools.schedule_parser_tool import parse_schedule_text
from scheduler_agents.tools.schedule_store import load_approved_schedule, save_approved_schedule
from scheduler_agents.tools.timesheet_tool import (
    extract_purchase_order_via_llm,
    parse_purchase_order_text,
    read_purchase_order_pdf_text,
)


def ask_user_can_cover_via_cli(slot: CoverageSlot, conflict: bool) -> bool:
    """Default human-in-the-loop prompt: ask directly in the terminal.

    A pluggable seam (SchedulerFlow(ask_user=...)) rather than a hardcoded
    input() call, so tests can inject a canned answer instead of blocking on
    stdin.
    """

    print(f"\nCoverage request: {describe_slot(slot)}")
    if conflict:
        print("Conflict: yes -- this overlaps something already on your approved schedule.")
    else:
        print("Conflict: no.")
    answer = input("Can you cover this shift? (y/n): ").strip().lower()
    return answer in {"y", "yes"}


def ask_availability_via_cli(period: str | None) -> str:
    """Default human-in-the-loop prompt for availability requests.

    Same pluggable-seam pattern as ask_user_can_cover_via_cli: a real
    terminal prompt by default, overridable in SchedulerFlow(ask_availability=...)
    so tests don't block on stdin.
    """

    period_desc = period or "the requested period"
    return input(f"\nWhat's your availability for {period_desc}? ").strip()


class SchedulerFlow(Flow[SchedulerFlowState]):
    """V1 CrewAI Flow for schedule email processing."""

    def __init__(
        self,
        sample_email_path: str | Path | None = None,
        memory: UserMemory | None = None,
        approved_schedule_path: str | Path | None = None,
        ask_user: Callable[[CoverageSlot, bool], bool] | None = None,
        ask_availability: Callable[[str | None], str] | None = None,
        timesheet_pdf_path: str | Path | None = None,
        invoice_template_path: str | Path | None = None,
        invoice_output_dir: str | Path | None = None,
        roster_image_path: str | Path | None = None,
    ):
        super().__init__()
        # Real crewai Flow instances auto-create self.state (a read-only property)
        # from the Flow[SchedulerFlowState] generic during super().__init__(). The
        # ImportError fallback stub above does not, so it still needs this.
        if getattr(self, "state", None) is None:
            self.state = SchedulerFlowState()
        self.sample_email_path = Path(sample_email_path) if sample_email_path else None
        self.approved_schedule_path = Path(approved_schedule_path) if approved_schedule_path else None
        self.ask_user = ask_user or ask_user_can_cover_via_cli
        self.ask_availability = ask_availability or ask_availability_via_cli
        self.timesheet_pdf_path = Path(timesheet_pdf_path) if timesheet_pdf_path else None
        self.invoice_template_path = Path(invoice_template_path) if invoice_template_path else None
        self.invoice_output_dir = Path(invoice_output_dir) if invoice_output_dir else None
        self.roster_image_path = Path(roster_image_path) if roster_image_path else None
        self.memory = memory or UserMemory()

    @start()
    def receive_email(self) -> EmailInput:
        record_hook(self.state, "before_receive_email")

        if is_live_gmail_enabled():
            try:
                pdf_save_path = self.invoice_output_dir / "downloaded_purchase_order.pdf" if self.invoice_output_dir else None
                roster_image_save_path = (
                    self.invoice_output_dir / "downloaded_roster_image.png" if self.invoice_output_dir else None
                )
                email = fetch_latest_email(
                    save_pdf_attachment_to=pdf_save_path,
                    save_roster_image_to=roster_image_save_path,
                )
                if email is not None:
                    self.state.email = email
                    self.state.memory_snapshot = self.memory.snapshot()
                    if email.attachments and pdf_save_path is not None:
                        # A PDF was actually found and saved this run -- V4 prefers
                        # this over the static local sample. Empty attachments means
                        # "none found", not "check the file": a stale download from a
                        # previous run must never be silently reused.
                        self.state.live_pdf_attachment_path = str(pdf_save_path)
                        record_hook(self.state, "gmail_pdf_attachment_saved", filename=email.attachments[0])
                    if email.roster_image_path:
                        # Same "found this run, not a stale file" rule as the PDF
                        # case above -- the real roster always arrives as an image,
                        # never text or a PDF, so this is what parse_schedule's
                        # vision fallback should actually read.
                        self.state.live_roster_image_path = email.roster_image_path
                        record_hook(self.state, "gmail_roster_image_saved", path=email.roster_image_path)
                    record_hook(
                        self.state, "after_receive_email", subject=email.subject, sender=email.sender, source="gmail"
                    )
                    return self.state.email
                record_hook(self.state, "gmail_fetch_found_nothing")
            except Exception as exc:  # network/auth failures never crash the flow
                record_hook(self.state, "gmail_fetch_failed", error=str(exc))

        raw_email = self._read_sample_email()
        subject, sender, sent_date, body = self._split_email(raw_email)

        self.state.email = EmailInput(subject=subject, sender=sender, body=body, sent_date=sent_date)
        self.state.memory_snapshot = self.memory.snapshot()
        record_hook(self.state, "after_receive_email", subject=subject, sender=sender, source="sample")
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
        """Try, in order: LLM (already done in classify_email if it found
        anything) -> regex over email.body -> vision over a roster
        screenshot. The real roster always arrives as an image with zero
        parseable text in the email body, so both text-based attempts
        legitimately come up empty before vision gets a turn.
        """

        record_hook(self.state, "before_parse_schedule")

        if self.state.extracted_events:
            record_hook(self.state, "parse_schedule_skipped", reason="already_extracted_by_llm")
            return self.state.extracted_events

        email = self._require_email()
        events = parse_schedule_text(email.body)

        # Prefer the image live Gmail actually downloaded off this message
        # over the static local sample -- same "live data wins when we have
        # it" rule handle_timesheet() already applies to the PDF path.
        roster_image_path = (
            Path(self.state.live_roster_image_path) if self.state.live_roster_image_path else self.roster_image_path
        )
        if not events and roster_image_path and roster_image_path.exists():
            try:
                events, timezone_label = parse_roster_image(roster_image_path)
                self.state.roster_timezone_label = timezone_label
                record_hook(
                    self.state,
                    "roster_image_parsed",
                    event_count=len(events),
                    timezone_label=timezone_label,
                )
            except Exception as exc:  # network/vision-model failures never crash the flow
                record_hook(self.state, "roster_image_parse_failed", error=str(exc))

        self.state.extracted_events = events
        record_hook(self.state, "after_parse_schedule", event_count=len(self.state.extracted_events))
        return self.state.extracted_events

    @listen(parse_schedule)
    def validate_schedule(self, _events: list[object] | None = None) -> list[str]:
        """Always re-runs the deterministic guardrail, regardless of whether
        events came from the LLM, regex, or vision -- this is the one
        source of truth for "safe to write to the calendar", and it must
        never be skipped just because some earlier step already ran once.
        """

        record_hook(self.state, "before_validate_schedule")

        # Language is always set here, unconditionally -- not extracted, not
        # backfilled-when-missing. This project automates one real vendor
        # relationship (Persian interpretation only), so the language is a
        # known fact about the interpreter's employment, not per-email data.
        # This is also the one choke point every extraction path (LLM crew,
        # regex, vision) passes through before the guardrail below runs.
        default_language = str(self.state.memory_snapshot.get("default_language", "Persian"))
        for event in self.state.extracted_events:
            event.language = default_language
            # Defense in depth: extraction_tasks.yaml's extract_schedule no
            # longer asks the model for a title, but if it sends one anyway
            # (e.g. an empty string), that would otherwise silently override
            # ScheduleEvent's own sensible default and produce a
            # blank-titled calendar event.
            if not event.title:
                event.title = ScheduleEvent.model_fields["title"].default

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

        default_timezone = str(self.state.memory_snapshot.get("timezone", "Asia/Yerevan"))
        # A roster image's own timezone (e.g. "UK") can differ from the
        # interpreter's default -- prefer it when vision extraction set one.
        timezone = resolve_timezone(self.state.roster_timezone_label, default_timezone)
        await asyncio.sleep(0)
        self.state.calendar_events = [
            build_calendar_event(event, timezone=timezone)
            for event in self.state.extracted_events
        ]

        # Passing the deterministic guardrail is this project's approval
        # gate for V1 (same rule create_calendar_events already applies
        # above) -- once a month's schedule clears it, it's a real
        # commitment V2's conflict check needs to know about.
        if self.state.extracted_events and self.approved_schedule_path:
            save_approved_schedule(self.state.extracted_events, self.approved_schedule_path)
            record_hook(self.state, "approved_schedule_saved", event_count=len(self.state.extracted_events))

        record_hook(self.state, "after_create_calendar_events", event_count=len(self.state.calendar_events))
        return self.state.calendar_events

    @listen("coverage_request")
    def handle_coverage_request(self) -> str | None:
        """V2: extract every open slot the email offers (a real one can list
        several dated slots at once, not just one), check each against the
        locally-approved work schedule for context, then ask the human about
        each slot in turn -- the conflict check informs the human, it
        doesn't decide for them.

        Deliberately not a real calendar integration: a personal
        Google/Outlook calendar mixes in non-work events that aren't a
        meaningful conflict signal here. The approved-schedule store (see
        schedule_store.py) -- built from this agent's own V1 approvals and
        accepted coverage slots -- is the only source of truth this project
        needs.

        Tries the LLM first (handles multiple slots, 12-hour times, combined
        date/time listings); falls back to the single-slot regex parser if
        no LLM is configured or it fails. One combined reply is drafted
        covering every slot; accepted slots are added to calendar_events.
        """

        record_hook(self.state, "before_handle_coverage_request")
        email = self._require_email()

        slots: list[CoverageSlot] = []
        unstructured_note: str | None = None

        if llm_is_configured():
            try:
                slots, unstructured_note = extract_coverage_slots_via_llm(
                    email.body, anchor_date=email.sent_date
                )
                record_hook(self.state, "coverage_slots_extracted_by_llm", count=len(slots))
            except Exception as exc:  # LLM/network failures fall back, never crash the flow
                record_hook(self.state, "coverage_llm_extraction_failed", error=str(exc))

        if not slots:
            slots = parse_coverage_request_regex(email.body)

        # Same rationale as parse_schedule/validate_schedule: language is a
        # known fact about this vendor relationship (Persian only), assigned
        # unconditionally rather than extracted/backfilled per slot.
        default_language = str(self.state.memory_snapshot.get("default_language", "Persian"))
        for slot in slots:
            slot.language = default_language

        self.state.coverage_slots = slots
        self.state.coverage_unstructured_note = unstructured_note
        self.state.coverage_approval_required = True

        if not slots:
            record_hook(self.state, "coverage_request_unparseable")
            return None

        busy_events = load_approved_schedule(self.approved_schedule_path) if self.approved_schedule_path else []
        timezone = str(self.state.memory_snapshot.get("timezone", "Asia/Yerevan"))
        decisions: list[CoverageSlotDecision] = []

        for slot in slots:
            conflict = has_conflict(slot, busy_events)
            can_cover = self.ask_user(slot, conflict)
            decision = CoverageDecision.ACCEPT if can_cover else CoverageDecision.DECLINE
            decisions.append(CoverageSlotDecision(slot=slot, conflict=conflict, decision=decision))

            if can_cover:
                schedule_event = ScheduleEvent(
                    date=slot.date,
                    start_time=slot.start_time,
                    end_time=slot.end_time,
                    language=slot.language,
                    title="Interpretation Session (coverage)",
                    source="coverage_request",
                )
                self.state.calendar_events.append(build_calendar_event(schedule_event, timezone=timezone))
                # A newly-accepted slot is now a real commitment: keep it in
                # busy_events for the rest of this loop (so a second
                # overlapping slot in the *same* email gets flagged too, not
                # just future emails) and persist it for future runs.
                busy_events.append(schedule_event)
                if self.approved_schedule_path:
                    save_approved_schedule([schedule_event], self.approved_schedule_path)

        self.state.coverage_decisions = decisions
        self.state.coverage_reply_draft = draft_coverage_reply_multi(decisions, unstructured_note)

        record_hook(
            self.state,
            "after_handle_coverage_request",
            slot_count=len(slots),
            accepted_count=sum(1 for d in decisions if d.decision == CoverageDecision.ACCEPT),
        )
        return self.state.coverage_reply_draft

    @listen("availability_request")
    def handle_availability_request(self) -> str:
        """V3: figure out which period is being asked about, ask the human
        to state their availability for it, and draft a reply.

        Unlike coverage requests there's nothing to auto-decide here -- the
        scheduler is asking a genuinely open question, so this always asks
        and always drafts. The draft still requires human approval before
        it's actually sent (this flow never sends anything itself).
        """

        record_hook(self.state, "before_handle_availability_request")
        email = self._require_email()

        period = extract_requested_period(email.body)
        self.state.availability_period = period

        statement = self.ask_availability(period)
        self.state.availability_statement = statement
        self.state.availability_reply_draft = draft_availability_reply(period, statement)
        self.state.availability_approval_required = True

        record_hook(self.state, "after_handle_availability_request", period=period)
        return self.state.availability_reply_draft

    @listen("timesheet")
    def handle_timesheet(self) -> str | None:
        """V4: the vendor's monthly notification email body carries no
        usable data ("please find attached the Purchase Order..."); job id,
        period, and amount all live in the attached PDF, so this reads that
        PDF directly rather than email.body like the other handlers.

        Prefers a PDF live Gmail actually downloaded from the classified
        message this run (receive_email() sets live_pdf_attachment_path)
        over the static local timesheet_pdf_path fallback -- the real
        Purchase Order this timesheet email carries, when there is one.

        Fills the fixed invoice template's known monthly-varying cells
        (invoice number, date, job id, amount/total) and saves a new .docx
        locally -- it never uploads or submits anything. The human reviews
        and submits it to the vendor platform themselves.
        """

        record_hook(self.state, "before_handle_timesheet")

        pdf_path = Path(self.state.live_pdf_attachment_path) if self.state.live_pdf_attachment_path else self.timesheet_pdf_path
        if pdf_path is None or not pdf_path.exists():
            record_hook(self.state, "timesheet_pdf_missing")
            return None

        pdf_text = read_purchase_order_pdf_text(pdf_path)
        data = parse_purchase_order_text(pdf_text)

        if data is None and llm_is_configured():
            # This vendor's own layout matches the regex almost always;
            # the LLM fallback exists for a purchase order that doesn't --
            # a different job-id format, unusual "Total" line wording, etc.
            try:
                data = extract_purchase_order_via_llm(pdf_text)
                record_hook(self.state, "timesheet_extracted_by_llm")
            except Exception as exc:  # LLM/network failures never crash the flow
                record_hook(self.state, "timesheet_llm_extraction_failed", error=str(exc))

        self.state.timesheet_data = data

        if data is None:
            record_hook(self.state, "timesheet_pdf_unparseable")
            return None

        if self.invoice_template_path is None or self.invoice_output_dir is None:
            record_hook(self.state, "invoice_template_or_output_dir_missing")
            return None

        vendor_id = str(self.state.memory_snapshot.get("vendor_id", "000000"))
        output_path = self.invoice_output_dir / f"INVOICE {data.period}.docx"
        fill_invoice_template(
            self.invoice_template_path,
            output_path,
            data,
            vendor_id=vendor_id,
            invoice_date=date.today(),
        )
        self.state.invoice_output_path = str(output_path)
        self.state.timesheet_approval_required = True

        record_hook(self.state, "after_handle_timesheet", job_id=data.job_id, period=data.period)
        return str(output_path)

    async def run_v1_async(self) -> SchedulerFlowState:
        """Deterministic async runner mirroring the CrewAI Flow order for V1/V2/V3/V4."""

        email = self.receive_email()
        email_type = await self.classify_email(email)
        if email_type == EmailType.SCHEDULE:
            events = self.parse_schedule()
            errors = self.validate_schedule(events)
            await self.create_calendar_events(errors)
        elif email_type == EmailType.COVERAGE_REQUEST:
            self.handle_coverage_request()
        elif email_type == EmailType.AVAILABILITY_REQUEST:
            self.handle_availability_request()
        elif email_type == EmailType.TIMESHEET:
            self.handle_timesheet()
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
    def _split_email(raw_email: str) -> tuple[str, str, date | None, str]:
        subject = ""
        sender = ""
        sent_date: date | None = None
        body_lines: list[str] = []

        for line in raw_email.splitlines():
            if line.lower().startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
            elif line.lower().startswith("from:"):
                sender = line.split(":", 1)[1].strip()
            elif line.lower().startswith("date:"):
                try:
                    sent_date = date.fromisoformat(line.split(":", 1)[1].strip())
                except ValueError:  # unparseable/missing header value; leave None
                    pass
            else:
                body_lines.append(line)

        return subject, sender, sent_date, "\n".join(body_lines).strip()

    def _require_email(self) -> EmailInput:
        if self.state.email is None:
            raise RuntimeError("Email has not been loaded into flow state.")
        return self.state.email

    def _classify_with_regex(self, email: EmailInput) -> None:
        """Offline, zero-cost fallback classifier used when no LLM key is configured.

        Uses word-boundary matching rather than plain substring checks: a
        naive "schedule" in text also matches inside "Scheduler" (this
        project's own sender name/signature), which would misclassify every
        coverage/availability/timesheet email that's signed that way.

        "availability" is ambiguous by itself: a real scheduler email said
        "We have availability from 11am-5pm, can you stay logged in longer" --
        that's the scheduler *offering* extra hours (coverage_request), not
        asking the interpreter to state their own availability
        (availability_request). Coverage-shaped phrasing is checked first so
        it wins that overlap; a bare "availability" only falls through to
        availability_request when none of those more specific signals match.

        A bare "hours" is too broad for timesheet: a real monthly roster
        email said "do not login during these hours" -- referring to
        time-of-day slots, not a worked-hours total -- and got misclassified
        as timesheet. "roster"/"rota" are also real-world synonyms for
        "schedule" this vendor actually uses, checked before the (now
        narrower) timesheet phrasing so a roster email can't fall through to
        it via a stray "hours" mention.
        """

        text = f"{email.subject}\n{email.body}"

        coverage_re = (
            r"\bcover(age|ing)?\b|\bavailable slot\b|\badditional hours?\b|\bextra hours?\b"
            r"|\bstay (logged in|online) (a bit )?longer\b|\bwe have availability\b"
        )
        timesheet_re = r"\btotal hours\b|\bworked hours\b|\btimesheet\b|\bpurchase order\b"

        if re.search(r"\bschedule\b|\broster\b|\brota\b", text, re.IGNORECASE):
            self.state.email_type = EmailType.SCHEDULE
        elif re.search(coverage_re, text, re.IGNORECASE):
            self.state.email_type = EmailType.COVERAGE_REQUEST
        elif re.search(r"\bavailability\b", text, re.IGNORECASE):
            self.state.email_type = EmailType.AVAILABILITY_REQUEST
        elif re.search(timesheet_re, text, re.IGNORECASE):
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
