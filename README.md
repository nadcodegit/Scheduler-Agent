# Scheduler Agents

CrewAI-based portfolio project for automating interpreter schedule workflows.

## Milestones

V1 focuses on the safest useful path:

```text
sample email -> classify email -> parse schedule -> validate events -> calendar-ready events
```

V2 adds the coverage-request workflow: an open-slot email is parsed and
checked for conflicts against the user's existing calendar, then a human is
asked directly -- "Can you cover this shift? (y/n)" -- rather than the system
deciding on its own. The conflict check is shown as context, not used to
auto-decide. A "yes" drafts an accept reply and adds the slot to
`calendar_events`; a "no" drafts a decline and leaves the calendar untouched.
The reply is always a draft -- nothing is ever sent automatically.

```text
coverage email -> parse slot -> check conflict -> ask human (y/n) -> draft reply + (if yes) update calendar
```

This step is deliberately a plain terminal prompt rather than a notification
service (Telegram, email, etc.): a real async notification/response loop is
a legitimate future upgrade, but it's a separate infrastructure concern from
"the agent asks before deciding," and adding it now would be solving a
problem this project doesn't have yet.

V3 adds the availability-request workflow: the scheduler asks for the
interpreter's availability for a period (e.g. "your June availability"),
so unlike V1/V2 there's nothing to extract a decision from -- the flow
figures out which period is being asked about, asks the human to state
their availability for it, and drafts the reply. Same rules as V2: always a
draft, human approves and sends it.

```text
availability email -> extract period -> ask human for availability -> draft reply (never sent)
```

V4 adds the timesheet/invoice workflow. Here the vendor's monthly
notification email body carries no usable data at all ("please find
attached the Purchase Order...") -- job id, period, and amount all live in
the attached PDF, so this is the one workflow that reads a PDF directly
instead of `email.body`. It fills only the known monthly-varying cells in a
fixed Word invoice template (invoice number, date, job id, amount/total) and
saves a new `.docx` locally -- it never uploads or submits anything to the
vendor platform; the human reviews and submits it themselves. Every static
field (name, address, bank details, bill-to) is left untouched.

```text
purchase-order PDF -> extract job id/period/amount -> fill invoice template -> save .docx (never submitted)
```

The project is intentionally designed with:

- CrewAI Flow orchestration
- Role-based agents
- Deterministic tools
- Pydantic state models
- Guardrails and validation
- Async execution path
- Hook-style event logging
- Simple local memory
- Human approval points for risky actions

## Project Structure

```text
scheduler-agents/
├── pyproject.toml
├── .env.example
├── README.md
├── sample_data/
│   ├── sample_schedule_email.txt
│   ├── sample_coverage_request_email.txt
│   ├── sample_availability_request_email.txt
│   ├── sample_purchase_order_email.txt
│   ├── sample_purchase_order.pdf
│   ├── sample_busy_calendar.json
│   └── invoice_template.docx
├── src/
│   └── scheduler_agents/
│       ├── main.py
│       ├── crews/
│       │   └── schedule_crew/
│       │       ├── crew.py
│       │       ├── config/
│       │       │   ├── agents.yaml
│       │       │   └── tasks.yaml
│       │       └── guardrails/
│       │           └── guardrails.py
│       ├── flows/
│       │   └── scheduler_flow.py
│       ├── guardrails/
│       │   └── schedule_guardrails.py
│       ├── hooks/
│       │   └── event_hooks.py
│       ├── memory/
│       │   └── user_memory.py
│       ├── models/
│       │   └── state.py
│       └── tools/
│           ├── availability_tool.py
│           ├── calendar_tool.py
│           ├── coverage_tool.py
│           ├── invoice_tool.py
│           ├── schedule_parser_tool.py
│           └── timesheet_tool.py
└── tests/
    ├── conftest.py
    ├── test_scheduler_flow_units.py
    ├── test_coverage_workflow.py
    ├── test_availability_workflow.py
    └── test_timesheet_workflow.py
```

## Two run modes

The flow works with **zero setup and zero cost**: if no `MODEL`/API key is
configured, `classify_email` and `parse_schedule` fall back to a deterministic
regex path, so tests, CI, and demos never need an API key.

Set `MODEL` and a matching API key in `.env` to switch on the real CrewAI
agents (`inbox_intelligence_agent`, `schedule_parser_agent`,
`schedule_validator_agent` from `crews/schedule_crew/`) for classification and
extraction. See `.env.example` for free options (Google Gemini, Groq) and
paid ones (OpenAI). If the LLM call fails for any reason (bad key, rate
limit, network), the flow logs it as a hook event and falls back to the
regex path rather than crashing.

Either way, the final schedule-validation guardrail is always the
deterministic one (`guardrails/schedule_guardrails.py`) — the LLM is trusted
to extract data, never to decide on its own that data is safe to write to a
calendar.

This project is pinned to Python 3.12 via `.python-version` (crewai's
dependency chain lags behind the newest CPython releases).

## Run

```bash
uv sync
uv run python -m scheduler_agents.main
```

Run the coverage-request path instead:

```bash
uv run python -m scheduler_agents.main --sample sample_coverage_request_email.txt
```

Run the availability-request path (it will prompt you in the terminal for
your availability):

```bash
uv run python -m scheduler_agents.main --sample sample_availability_request_email.txt
```

Run the timesheet/invoice path (fills `sample_data/invoice_template.docx`
from `sample_data/sample_purchase_order.pdf` and saves the result to
`outputs/`):

```bash
uv run python -m scheduler_agents.main --sample sample_purchase_order_email.txt
```

For a quick run without installing the package first:

```powershell
$env:PYTHONPATH="src"
python -m scheduler_agents.main
```

The run writes:

```text
outputs/calendar_payloads.json
outputs/flow_state.json
```

## Test

```bash
uv run pytest
```

`uv run` auto-loads `.env`, so without `tests/conftest.py` a configured
MODEL/API key would silently leak into the test run. `conftest.py` strips
those env vars for every test, so the suite always exercises the
deterministic path — fast, offline, free, and not dependent on a third-party
API being up.

## Next Steps

1. Replace the sample email input with Gmail or Outlook ingestion.
2. Replace the dry-run calendar tool with Google Calendar or Outlook Calendar,
   and load the busy-calendar check (V2) from the real calendar instead of
   `sample_busy_calendar.json`.
3. Move the coverage-request y/n prompt off the terminal onto an actual
   notification channel (e.g. Telegram) so it doesn't require the flow to be
   running interactively -- this needs an async "ask now, resume later"
   design (persisted pending-decision state, polling/webhook for the
   response), not just swapping `input()` for an API call.
4. Improve coverage-slot extraction to handle loosely-worded, date-less
   requests (e.g. "stay logged in until 5pm today", 12-hour times) -- these
   currently fail to parse with either the regex fallback or the LLM path,
   since neither resolves relative dates like "today".
5. Add CrewAI eval cases for classification, parsing, and safe tool use.
6. Split classification from extraction so the extractor/validator agents
   only run once an email is already known to be a schedule email, instead
   of always running the full three-task crew.
7. Read the vendor's real PDF attachment straight from Gmail/Outlook instead
   of a local `timesheet_pdf_path`, once real email ingestion (item 1) lands.
8. Extend PDF extraction with an LLM fallback for purchase orders that don't
   match this template's exact layout (job id format, "Total" line wording),
   the same way schedule extraction already has an LLM path alongside regex.

## Course-Style CrewAI Pieces

The project includes the same course-style separation used in the assignment:

- `crews/schedule_crew/config/agents.yaml` for agent role, goal, and backstory.
- `crews/schedule_crew/config/tasks.yaml` for task descriptions, expected outputs, context, and async execution.
- `crews/schedule_crew/crew.py` for `@CrewBase`, `@agent`, `@task`, and `@crew` decorators.
- `crews/schedule_crew/guardrails/guardrails.py` for task output validation.
