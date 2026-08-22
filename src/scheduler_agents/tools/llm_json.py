from __future__ import annotations

import re


def strip_code_fence(text: str) -> str:
    """Strip a ```json ... ``` (or bare ```...```) wrapper some models add
    around JSON output despite being told not to. Shared by every tool that
    parses free-form LLM output as JSON.
    """

    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1) if match else text
