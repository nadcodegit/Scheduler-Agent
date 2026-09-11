# Runs the live Gmail check unattended (Windows Task Scheduler has no
# console to answer y/n prompts) and appends the result to a log file, so
# there's something to actually check afterward. Every run appends a
# timestamped header rather than overwriting, so a history builds up.
#
# V1 (schedule) and V4 (timesheet) fully self-process -- no human decision
# needed. V2 (coverage) and V3 (availability) always need one; when this
# runs with no interactive terminal, the flow itself detects that and prints
# an "ACTION NEEDED" line instead of crashing or guessing an answer (see
# NeedsHumanAttention in flows/scheduler_flow.py) -- open this log
# periodically and re-run `uv run python -m scheduler_agents.main` yourself
# in an interactive terminal whenever you see one.

$ErrorActionPreference = "Continue"
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location $projectDir

$logPath = Join-Path $projectDir "outputs\scheduled_run.log"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

Add-Content -Path $logPath -Value "`n===== $timestamp =====" -Encoding utf8
uv run python -m scheduler_agents.main 2>&1 | Add-Content -Path $logPath -Encoding utf8
