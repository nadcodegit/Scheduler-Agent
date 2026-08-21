from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from scheduler_agents.models.state import TimesheetData

# The vendor's monthly notification email carries no useful data in its body
# text ("Please find attached the Purchase Order...") -- job id, period, and
# amount all live in the attached PDF. Parsing is split into a pure
# text-in/data-out function (unit-testable against plain extracted text) and
# a thin PDF-reading wrapper around it.

JOB_ID_RE = re.compile(r"\b(\d{4}/\d+/#\d+/\d+)\b")
# (?<![A-Za-z]) rather than a leading \b: the PDF's Description field writes
# the period as "..._March 2026" -- underscore is a \w character, so \b
# doesn't see a boundary between "_" and "M" and would silently fail to
# match here.
MONTH_YEAR_RE = re.compile(
    r"(?<![A-Za-z])(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b",
    re.IGNORECASE,
)
# \bTotal\b (not \bSubtotal\b, which "Total" is a substring of but doesn't
# share a word boundary with) so this matches only the final total line.
TOTAL_RE = re.compile(r"\bTotal\b\D{0,20}?([\d]+\.\d{2})", re.IGNORECASE)


def parse_purchase_order_text(text: str) -> TimesheetData | None:
    job_id_match = JOB_ID_RE.search(text)
    period_match = MONTH_YEAR_RE.search(text)
    total_match = TOTAL_RE.search(text)

    if not (job_id_match and period_match and total_match):
        return None

    month, year = period_match.group(1), period_match.group(2)
    return TimesheetData(
        job_id=job_id_match.group(1),
        period=f"{month.capitalize()} {year}",
        total_amount=float(total_match.group(1)),
    )


def parse_purchase_order_pdf(path: Path) -> TimesheetData | None:
    with pdfplumber.open(path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return parse_purchase_order_text(text)
