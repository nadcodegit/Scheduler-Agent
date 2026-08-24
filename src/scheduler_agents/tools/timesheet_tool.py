from __future__ import annotations

import json
import os
import re
from pathlib import Path

import litellm
import pdfplumber

from scheduler_agents.models.state import TimesheetData
from scheduler_agents.tools.llm_json import strip_code_fence

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


def read_purchase_order_pdf_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def parse_purchase_order_pdf(path: Path) -> TimesheetData | None:
    return parse_purchase_order_text(read_purchase_order_pdf_text(path))


def _build_llm_extraction_prompt(text: str) -> str:
    return f"""This is text extracted from a vendor Purchase Order PDF for an interpreter's \
monthly work. Extract exactly three fields:
- job_id: the job identifier, usually formatted like "YYYY/NNNN/#N/N" (e.g. "2026/2609/#1/1")
- period: the billing month and year this purchase order covers, as "Month YYYY" \
(e.g. "March 2026") -- often appears right after the interpreter's name in a \
"Description" field like "..._March 2026"
- total_amount: the final total amount as a plain number with no currency symbol, \
from the line labeled "Total" -- if "Subtotal" and "Total" differ, use "Total"

Purchase order text:
{text}

Return strict JSON only, no prose, no markdown fences, in exactly this shape:
{{"job_id": "...", "period": "Month YYYY", "total_amount": 0.00}}"""


def extract_purchase_order_via_llm(text: str) -> TimesheetData:
    """Single-shot LLM fallback for purchase orders that don't match this
    vendor's known layout closely enough for the regex parser (a different
    job-id format, unusual "Total" line wording, etc.) -- same single-shot
    "read this as JSON" pattern as coverage/roster extraction, not a
    CrewAI crew, since there's no multi-step reasoning here.

    Raises on missing MODEL / network / bad-JSON errors so the caller
    decides how to fall back, matching every other LLM path in this
    project.
    """

    model = os.getenv("MODEL")
    if not model:
        raise RuntimeError("MODEL is not set; purchase-order LLM fallback needs an LLM configured.")

    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": _build_llm_extraction_prompt(text)}],
        temperature=0,
    )
    raw = response.choices[0].message.content
    data = json.loads(strip_code_fence(raw))

    return TimesheetData(
        job_id=str(data["job_id"]),
        period=str(data["period"]),
        total_amount=float(data["total_amount"]),
    )
