@echo off
cd /d "C:\Users\ZenBook\OneDrive\Documents\New project\scheduler-agents"
rem Calling the venv's own python.exe directly (not "uv run") skips uv's
rem project-sync/rebuild check, which otherwise silently re-builds the
rem local package on every single launch (often 15-30s with no visible
rem progress, likely triggered by OneDrive sync touching file mtimes) --
rem that delay was easy to mistake for "double-clicking does nothing."
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found -- run "uv sync" once from a terminal first.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m desktop_app.main
if errorlevel 1 (
    echo.
    echo The app failed to start ^(see the error above^).
    pause
)
