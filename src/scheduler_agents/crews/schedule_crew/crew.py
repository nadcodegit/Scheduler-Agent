from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

try:
    from crewai import Agent, Crew, Process, Task
    from crewai.project import CrewBase, agent, crew, task
except ImportError:  # pragma: no cover
    Agent = Crew = Process = Task = None

    def CrewBase(cls):  # type: ignore[no-redef]
        return cls

    def agent(fn):
        return fn

    def crew(fn):
        return fn

    def task(fn):
        return fn
else:
    # crewai marks the last stable-prefix message with a "cache_breakpoint"
    # flag meant for Anthropic-style prompt caching, but never strips it for
    # providers routed through LiteLLM (Groq, and other OpenAI-compatible
    # APIs), which then reject the unknown field. mark_cache_breakpoint is
    # imported lazily at call time in crewai's agent executor, so patching it
    # here (before any crew runs) is enough. This only disables a caching
    # optimization, not correctness. See crewAIInc/crewAI#5886.
    import crewai.llms.cache as _crewai_llm_cache

    _crewai_llm_cache.mark_cache_breakpoint = lambda message: dict(message)

from scheduler_agents.crews.schedule_crew.guardrails.guardrails import (
    validate_email_type,
    validate_schedule_json,
)

# Any of these being set (alongside MODEL) is treated as "an LLM key is available".
_KNOWN_LLM_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "ANTHROPIC_API_KEY",
)


def llm_is_configured() -> bool:
    """True when a MODEL and a matching API key are present in the environment."""

    return bool(os.getenv("MODEL")) and any(os.getenv(name) for name in _KNOWN_LLM_KEY_ENV_VARS)


class ScheduleValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    approval_required: bool = False
    summary: str


class PipelineResult(BaseModel):
    """Parsed output of the three-agent CrewAI sequential pipeline."""

    email_type: str
    raw_events: list[dict] = Field(default_factory=list)
    validation: ScheduleValidationResult | None = None


@CrewBase
class ScheduleCrew:
    """YAML-configured CrewAI crew following the course project pattern."""

    base_dir = Path(__file__).parent
    agents_config = str(base_dir / "config" / "agents.yaml")
    tasks_config = str(base_dir / "config" / "tasks.yaml")

    # crewai's built-in agent/crew memory (memory=True) needs its own LLM for
    # memory analysis and defaults to OpenAI if none is configured, which
    # breaks the "works with any free provider" design here. This project's
    # own UserMemory class already covers the memory this app needs.

    @agent
    def inbox_intelligence_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["inbox_intelligence_agent"],
            verbose=True,
            memory=False,
        )

    @agent
    def schedule_parser_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["schedule_parser_agent"],
            verbose=True,
            memory=False,
        )

    @agent
    def schedule_validator_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["schedule_validator_agent"],
            verbose=True,
            memory=False,
        )

    @task
    def classify_email(self) -> Task:
        return Task(
            config=self.tasks_config["classify_email"],
            guardrail=validate_email_type,
        )

    @task
    def extract_schedule(self) -> Task:
        return Task(
            config=self.tasks_config["extract_schedule"],
            guardrail=validate_schedule_json,
            async_execution=True,
        )

    @task
    def validate_schedule(self) -> Task:
        # No output_pydantic here on purpose: structured output requires
        # tool-calling, which isn't reliable across every free-tier provider
        # (e.g. Groq has rejected well-formed JSON with "tool_use_failed" in
        # testing). run_llm_pipeline() never trusts this task's result for
        # the actual approval decision anyway -- validate_schedule_events()
        # (the deterministic guardrail) always re-checks the extracted
        # events, so this task only needs to run for the demonstrated
        # 3-agent pipeline, not for correctness.
        return Task(config=self.tasks_config["validate_schedule"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
            memory=False,
        )


async def run_llm_pipeline(email_text: str) -> PipelineResult:
    """Run the full classify -> extract -> validate crew for one email.

    Uses kickoff_async because the flow drives everything through
    asyncio.run(); crewai's synchronous kickoff() refuses to run inside an
    already-running event loop.

    Raises whatever the underlying crewai/LLM call raises (network errors,
    auth errors, etc.) so the caller can decide how to fall back.
    """

    if Crew is None:
        raise RuntimeError("crewai is not installed.")

    output = await ScheduleCrew().crew().kickoff_async(inputs={"email": email_text})
    tasks_output = list(output.tasks_output)

    email_type = str(tasks_output[0].raw).strip().lower() if tasks_output else "other"

    raw_events: list[dict] = []
    if len(tasks_output) > 1 and tasks_output[1].raw:
        try:
            parsed = json.loads(tasks_output[1].raw)
            if isinstance(parsed, list):
                raw_events = [item for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            raw_events = []

    validation: ScheduleValidationResult | None = None
    if len(tasks_output) > 2:
        validation = tasks_output[2].pydantic

    return PipelineResult(email_type=email_type, raw_events=raw_events, validation=validation)
