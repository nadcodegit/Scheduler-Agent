' Launches run_scheduled_check.ps1 with zero visible window -- passing
' -WindowStyle Hidden directly to the top-level process Task Scheduler
' launches is well-known to be unreliable (Windows can still flash a
' console briefly before it hides). Routing through WScript.Shell.Run
' with window style 0 (SW_HIDE) is the standard, actually-reliable fix.
Set objShell = CreateObject("WScript.Shell")
objShell.Run "powershell.exe -ExecutionPolicy Bypass -File ""C:\Users\ZenBook\OneDrive\Documents\New project\scheduler-agents\scripts\run_scheduled_check.ps1""", 0, False
