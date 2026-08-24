import json
from typing import Any

# NOTE: no `from __future__ import annotations` here on purpose. crewai
# inspects these functions' return annotations at runtime (via
# inspect.signature) to enforce the Tuple[bool, Any] guardrail contract;
# postponed evaluation would turn the annotation into an unparsed string and
# fail that check. It also passes the task's TaskOutput object as `output`,
# not a raw string.


def validate_email_type(output: Any) -> tuple[bool, Any]:
    allowed = {"schedule", "coverage_request", "availability_request", "timesheet", "other"}
    value = str(output.raw).strip().lower()
    if value not in allowed:
        return False, f"Email type must be one of {sorted(allowed)}."
    return True, value


def validate_schedule_json(output: Any) -> tuple[bool, Any]:
    raw = str(output.raw)
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"Output must be valid JSON: {exc}"

    if not isinstance(data, list):
        return False, "Schedule extraction output must be a JSON array."

    # language/title/source are fixed, known facts filled in deterministically
    # elsewhere in this project (see extraction_tasks.yaml's extract_schedule
    # description) -- not per-email data the model should be required to
    # invent a value for.
    required = {"date", "start_time", "end_time"}
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            return False, f"Schedule item {index} must be an object."
        missing = required - set(item)
        if missing:
            return False, f"Schedule item {index} is missing fields: {sorted(missing)}."

    return True, raw

