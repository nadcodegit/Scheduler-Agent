# Runs the live Gmail check unattended (Windows Task Scheduler has no
# console to answer y/n prompts) and appends the result to a log file, so
# there's something to actually check afterward. Every run appends a
# timestamped header rather than overwriting, so a history builds up.
#
# V1 (schedule) and V4 (timesheet) fully self-process -- no human decision
# needed. V2 (coverage) and V3 (availability) always need one; when this
# runs with no interactive terminal, the flow itself detects that and prints
# an "ACTION NEEDED" line instead of crashing or guessing an answer (see
# NeedsHumanAttention in flows/scheduler_flow.py). When that happens, this
# script also pops a real Windows toast notification -- the log alone is
# easy to forget to check, a notification is not.

$ErrorActionPreference = "Continue"
$projectDir = Split-Path -Parent $PSScriptRoot
Set-Location $projectDir

# Without this, crewai's Rich-drawn box characters get captured in the
# console's non-UTF-8 codepage and come out as mojibake in the log file --
# this forces both the console and the python subprocess itself to speak
# UTF-8 end to end.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$logPath = Join-Path $projectDir "outputs\scheduled_run.log"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

Add-Content -Path $logPath -Value "`n===== $timestamp =====" -Encoding utf8
$runOutput = uv run python -m scheduler_agents.main 2>&1
$runOutput | Add-Content -Path $logPath -Encoding utf8

function Show-SchedulerToast {
    param([string]$Title, [string]$Message)

    # Uses the built-in WinRT toast API directly -- no extra module install
    # (e.g. BurntToast) needed on Windows 10/11. Runs as plain PowerShell,
    # which has no toast identity of its own, so it borrows PowerShell's
    # own well-known AppUserModelID -- the standard, widely-used workaround
    # for toasts from a plain script rather than a packaged app; the toast
    # shows up attributed to "Windows PowerShell".
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

        $template = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>$Title</text>
      <text>$Message</text>
    </binding>
  </visual>
</toast>
"@
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($template)
        $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
        $appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
    } catch {
        Add-Content -Path $logPath -Value "(toast notification failed: $_)" -Encoding utf8
    }
}

if ($runOutput -match "ACTION NEEDED") {
    Show-SchedulerToast -Title "Scheduler Agents" -Message "A coverage or availability email needs your answer. Check outputs\scheduled_run.log."
}
