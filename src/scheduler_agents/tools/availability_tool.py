from __future__ import annotations

import re

_MONTH_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"(?:\s+\d{4})?\b",
    re.IGNORECASE,
)


def extract_requested_period(text: str) -> str | None:
    """Pull the month (and year, if present) the scheduler is asking about.

    Deterministic on purpose, like the rest of this project's extraction:
    availability-request emails reliably name a specific month ("your June
    availability"), so a small regex covers this without needing an LLM
    call. Falls back to None (the flow then says "the requested period")
    when no month name is found.
    """

    match = _MONTH_RE.search(text)
    return match.group(0) if match else None


def draft_availability_reply(period: str | None, statement: str) -> str:
    """Template-based reply draft -- deterministic on purpose, like
    coverage_tool.draft_coverage_reply. This is a draft only: the flow never
    sends it. A human reviews and sends it manually.
    """

    period_desc = period or "the requested period"
    return f"Hi,\n\nHere is my availability for {period_desc}:\n\n{statement}\n\nBest,\n[Your name]"
