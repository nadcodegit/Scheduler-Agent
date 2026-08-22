from __future__ import annotations

import base64
import json
import os
from datetime import date
from pathlib import Path

import httpx

from scheduler_agents.models.state import ScheduleEvent
from scheduler_agents.tools.llm_json import strip_code_fence

# The real monthly roster always arrives as a screenshot of a spreadsheet,
# never as text or a text-extractable PDF -- so unlike every other tool in
# this project, there is no regex path here. This calls a vision-capable
# LLM directly (a plain API call, not a CrewAI agent/task): the job is a
# single-shot "describe this image as JSON" call, not multi-step reasoning,
# so a CrewAI Crew would add ceremony without adding anything.

GROQ_VISION_MODEL = "qwen/qwen3.6-27b"

# Abbreviations actually seen in roster column headers. Extend as new ones
# show up rather than guessing IANA names from an LLM, which is exactly the
# kind of "safety-relevant" mapping this project prefers to keep deterministic.
_TIMEZONE_LABELS = {
    "UK": "Europe/London",
    "GMT": "Europe/London",
    "BST": "Europe/London",
}

def _build_prompt() -> str:
    # The model has no notion of "today" on its own and will otherwise guess
    # a training-time year (seen guessing 2024 in testing); telling it the
    # real current date fixes year inference for rosters that only show a
    # day-of-month and weekday.
    today = date.today().isoformat()
    return f"""This image is a monthly interpreter roster: a grid where each row is a \
date (with day of week) and each column is an hourly time slot labeled with a \
time range and a timezone abbreviation, e.g. "9-10AM UK".

A cell containing 1 means the interpreter is scheduled to work that slot. A \
blank cell means not scheduled. A 0 (often shown in a different color) means \
the slot was cancelled. Only include 1-cells as events; skip blank and 0 cells.

Return strict JSON only, no prose, no markdown fences, in exactly this shape:
{{"timezone_label": "<abbreviation from the column headers, e.g. UK>", \
"events": [{{"date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM"}}]}}

Today's real date is {today}. If the roster doesn't state a year, use the \
year that makes the month closest to today's date. Use 24-hour HH:MM times."""


def is_vision_configured() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def resolve_timezone(label: str | None, default: str) -> str:
    if label is None:
        return default
    return _TIMEZONE_LABELS.get(label.strip().upper(), default)


def parse_roster_image(path: Path) -> tuple[list[ScheduleEvent], str | None]:
    """Call the vision model once and turn its JSON into ScheduleEvents.

    Raises on missing key / network / bad-JSON errors so the caller decides
    how to fall back, matching the pattern used for the CrewAI LLM path.
    """

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set; roster image extraction needs a vision-capable key.")

    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    image_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")

    response = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": GROQ_VISION_MODEL,
            "temperature": 0,
            # Qwen3.6 thinks-out-loud in <think> tags by default, which
            # breaks naive JSON parsing; this model still answers correctly
            # with reasoning off, so there's no accuracy tradeoff here.
            "reasoning_effort": "none",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _build_prompt()},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                    ],
                }
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"]
    data = json.loads(strip_code_fence(raw))

    events: list[ScheduleEvent] = []
    for item in data.get("events", []):
        try:
            events.append(
                ScheduleEvent(
                    date=item["date"],
                    start_time=item["start_time"],
                    end_time=item["end_time"],
                    source="roster_image",
                )
            )
        except Exception:
            continue  # malformed row from the model; skip it rather than fail the whole batch

    return events, data.get("timezone_label")
