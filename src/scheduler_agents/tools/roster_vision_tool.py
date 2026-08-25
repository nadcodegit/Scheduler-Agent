from __future__ import annotations

import base64
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

import litellm

from scheduler_agents.models.state import ScheduleEvent
from scheduler_agents.tools.llm_json import strip_code_fence

# The real monthly roster always arrives as a screenshot of a spreadsheet,
# never as text or a text-extractable PDF -- so unlike every other tool in
# this project, there is no regex path here. This calls a vision-capable
# LLM directly via litellm (a plain multimodal completion call, not a
# CrewAI agent/task): the job is a single-shot "describe this image as
# JSON" call, not multi-step reasoning, so a CrewAI Crew would add
# ceremony without adding anything.
#
# Deliberately NOT the same MODEL env var every other LLM call in this
# project follows: MODEL is usually chosen for classification/coverage
# text tasks and isn't necessarily vision-capable. Instead, each of these
# curated known-vision-capable models is tried in order until one
# succeeds -- this is the actual provider fallback every other extraction
# path in this project already has (LLM -> regex); a roster screenshot has
# no parseable text, so the fallback has to be across vision providers
# instead.
_VISION_MODEL_CANDIDATES: list[tuple[str, str]] = [
    ("GROQ_API_KEY", "groq/qwen/qwen3.6-27b"),
    ("OPENAI_API_KEY", "gpt-4o-mini"),
    ("GEMINI_API_KEY", "gemini/gemini-3.6-flash"),
]

# Abbreviations actually seen in roster column headers. Extend as new ones
# show up rather than guessing IANA names from an LLM, which is exactly the
# kind of "safety-relevant" mapping this project prefers to keep deterministic.
_TIMEZONE_LABELS = {
    "UK": "Europe/London",
    "GMT": "Europe/London",
    "BST": "Europe/London",
}

# A real base64-encoded roster screenshot plus this prompt reliably costs
# several thousand tokens, and Groq's free tier caps at a flat 8000
# tokens/minute (org-wide, shared with every other Groq call this project
# makes) -- confirmed live: a real request was rejected outright
# ("Requested 10284" against "Limit 8000"), not merely throttled. A single
# retry after the window resets is enough in practice, same pattern
# evals/run_eval.py already uses for the same rate limit on the text path.
_RATE_LIMIT_RETRY_DELAY_SECONDS = 20
_RATE_LIMIT_MAX_ATTEMPTS = 4

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
    return any(os.getenv(env_var) for env_var, _ in _VISION_MODEL_CANDIDATES)


def resolve_timezone(label: str | None, default: str) -> str:
    if label is None:
        return default
    return _TIMEZONE_LABELS.get(label.strip().upper(), default)


def _call_vision_model(model: str, prompt: str, mime: str, image_b64: str, api_key: str) -> dict[str, Any]:
    # api_key is passed explicitly rather than left for litellm to pick up
    # from the GEMINI_API_KEY env var on its own -- verified live, those two
    # paths are NOT equivalent for Gemini: relying on the env var routed
    # litellm through a different internal auth flow (a Vertex-AI-style
    # "beta" endpoint expecting an OAuth token) and failed with a 401 on a
    # key that worked immediately once passed as this explicit parameter.
    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "temperature": 0,
        # A real full month's roster (Persian interpretation is on a
        # near-daily cadence for this vendor) can run 40+ events -- without
        # an explicit cap, the default max token limit truncates the JSON
        # mid-array on a real roster (verified live: cut off at char 4019,
        # finish_reason "length") even though it never showed up against the
        # small 5-event demo fixture used for earlier testing. High enough
        # to comfortably cover a full month, still a hard ceiling against a
        # runaway response.
        "max_tokens": 8000,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                ],
            }
        ],
    }
    if model.startswith("groq/"):
        # Qwen3.6 thinks-out-loud in <think> tags by default, which breaks
        # naive JSON parsing; this model still answers correctly with
        # reasoning off, so there's no accuracy tradeoff here. Not a
        # recognized param for the other candidates, so only sent to Groq.
        kwargs["reasoning_effort"] = "none"

    response = litellm.completion(**kwargs)
    raw = response.choices[0].message.content
    return json.loads(strip_code_fence(raw))


def parse_roster_image(path: Path) -> tuple[list[ScheduleEvent], str | None]:
    """Tries each configured vision-capable provider in priority order
    (see _VISION_MODEL_CANDIDATES) until one succeeds, and turns its JSON
    into ScheduleEvents.

    Raises if no provider is configured, or if every configured one
    failed, so the caller decides how to fall back, matching the pattern
    used for the CrewAI LLM path.
    """

    configured = [(env_var, model) for env_var, model in _VISION_MODEL_CANDIDATES if os.getenv(env_var)]
    if not configured:
        names = ", ".join(env_var for env_var, _ in _VISION_MODEL_CANDIDATES)
        raise RuntimeError(f"No vision-capable provider configured; roster image extraction needs one of: {names}.")

    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    image_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    prompt = _build_prompt()

    data: dict[str, Any] | None = None
    last_error: Exception | None = None
    for env_var, model in configured:
        for attempt in range(1, _RATE_LIMIT_MAX_ATTEMPTS + 1):
            try:
                data = _call_vision_model(model, prompt, mime, image_b64, api_key=os.getenv(env_var, ""))
                break
            except litellm.RateLimitError as exc:
                last_error = exc
                if attempt == _RATE_LIMIT_MAX_ATTEMPTS:
                    break
                time.sleep(_RATE_LIMIT_RETRY_DELAY_SECONDS)
            except Exception as exc:  # try the next configured provider rather than giving up
                last_error = exc
                break
        if data is not None:
            break

    if data is None:
        raise RuntimeError(f"All configured vision providers failed; last error: {last_error}") from last_error

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
