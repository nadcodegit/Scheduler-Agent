from __future__ import annotations

import pytest

# uv run auto-loads .env, so a developer's real MODEL/API key would otherwise
# leak into every test run, making unit tests slow, flaky (network/rate
# limits), and cost real API calls. Unit tests should exercise the
# deterministic path only; the LLM path is verified manually against a real
# key, not in the offline test suite.
_LLM_ENV_VARS = (
    "MODEL",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def _force_offline_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LLM_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    # Same rationale, for Gmail ingestion: a developer's real GMAIL_ENABLED=true
    # would otherwise make the unit suite try to open a real OAuth consent
    # screen / hit the live Gmail API.
    monkeypatch.setenv("GMAIL_ENABLED", "false")
