"""Live evaluation suite for classify_email (+ extraction/safety) against
real sample emails with known-correct expected results.

NOT part of the offline pytest suite -- tests/conftest.py deliberately
strips MODEL/API key env vars, since the offline suite must stay free and
network-free (see README.md's "Two run modes"). This script is the
opposite: it needs a real MODEL/API key configured and makes real LLM
calls, one classify_email call per case (plus one extraction call for each
case expected to be a schedule email).

Run manually, e.g. after changing classify_email's prompt in
crews/schedule_crew/config/tasks.yaml, to check nothing regressed across
every real-world case this project's prompt has already been hardened
against this session (see README.md's "Milestones" section for the actual
false positives each case is named after):

    uv run python evals/run_eval.py

"Safe tool use": every case expected to be `other` also asserts zero
events get extracted -- the actual failure mode found live this session
(a misclassified email had its referenced-but-irrelevant date extracted
as a brand-new calendar event) wasn't just a wrong label, it was a wrong
label AND a side effect. Catching both here is the point.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

import litellm

from scheduler_agents.crews.schedule_crew.crew import run_llm_pipeline

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample_data"

# Groq's free tier caps at 8000 tokens/minute; classify_email's prompt alone
# runs ~1500-1700 tokens, so running every case back-to-back reliably blows
# through it partway through a 12-case run. Retry with a fixed backoff
# rather than a tighter per-request delay -- simpler, and correct regardless
# of exactly how close to the limit the previous calls landed.
_RATE_LIMIT_RETRY_DELAY_SECONDS = 20
_RATE_LIMIT_MAX_ATTEMPTS = 4


@dataclass
class Case:
    name: str
    sample_file: str
    expected_type: str
    # Only meaningful for expected_type == "schedule"; None means "don't check".
    expected_event_count: int | None = None


CASES: list[Case] = [
    # -- Classification + parsing: the straightforward, correctly-labeled cases --
    Case("finalized_schedule", "sample_schedule_email.txt", "schedule", expected_event_count=3),
    # Real rosters arrive as an image with no parseable text in the body --
    # classification must still say "schedule" (roster/rota is a real
    # synonym this vendor uses), but text-only extraction correctly finds
    # nothing; vision extraction is a separate flow-level fallback not
    # exercised by run_llm_pipeline.
    Case("roster_synonym_no_body_dates", "sample_roster_email.txt", "schedule", expected_event_count=0),
    Case("coverage_request", "sample_coverage_request_email.txt", "coverage_request"),
    Case("coverage_relative_dates", "sample_coverage_request_relative_dates_email.txt", "coverage_request"),
    Case("coverage_weekly_availability", "sample_coverage_request_weekly_availability_email.txt", "coverage_request"),
    Case("coverage_mixed_dates", "sample_coverage_request_mixed_dates_email.txt", "coverage_request"),
    Case("availability_request", "sample_availability_request_email.txt", "availability_request"),
    Case("purchase_order", "sample_purchase_order_email.txt", "timesheet"),
    # -- Safe tool use: real false positives classify_email was hardened
    # against this session. Each MUST be "other" AND extract zero events --
    # a misclassification here previously created a real bogus calendar
    # event from an email that only referenced a date in passing.
    Case("attendance_complaint", "sample_other_attendance_complaint_email.txt", "other", expected_event_count=0),
    Case("compliance_deadline", "sample_other_compliance_deadline_email.txt", "other", expected_event_count=0),
    Case("shift_removal_confirmation", "sample_other_shift_removal_confirmation_email.txt", "other", expected_event_count=0),
    Case("shift_reinstated_confirmation", "sample_other_shift_reinstated_confirmation_email.txt", "other", expected_event_count=0),
]


async def _run_pipeline_with_rate_limit_retry(email_text: str):
    for attempt in range(1, _RATE_LIMIT_MAX_ATTEMPTS + 1):
        try:
            return await run_llm_pipeline(email_text)
        except litellm.RateLimitError:
            if attempt == _RATE_LIMIT_MAX_ATTEMPTS:
                raise
            print(f"  (rate limited, waiting {_RATE_LIMIT_RETRY_DELAY_SECONDS}s before retry {attempt + 1}...)")
            await asyncio.sleep(_RATE_LIMIT_RETRY_DELAY_SECONDS)


async def run_case(case: Case) -> tuple[bool, str]:
    email_text = (SAMPLE_DATA / case.sample_file).read_text(encoding="utf-8")
    try:
        result = await _run_pipeline_with_rate_limit_retry(email_text)
    except Exception as exc:  # noqa: BLE001 - report any failure as a failing case, don't crash the whole run
        return False, f"ERROR: {exc}"

    if result.email_type != case.expected_type:
        return False, f"expected type={case.expected_type!r}, got {result.email_type!r}"

    if case.expected_event_count is not None and len(result.raw_events) != case.expected_event_count:
        return False, f"expected {case.expected_event_count} event(s), got {len(result.raw_events)}"

    return True, "ok"


async def main() -> None:
    print(f"{'CASE':32} {'RESULT':6} DETAIL")
    print("-" * 70)

    passed = 0
    for case in CASES:
        ok, detail = await run_case(case)
        passed += ok
        print(f"{case.name:32} {'PASS' if ok else 'FAIL':6} {detail}")

    print("-" * 70)
    print(f"{passed}/{len(CASES)} passed")

    if passed < len(CASES):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
