@echo off
rem Launch log_visualizer with file/folder dialogs (double-click to run)
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m log_visualizer --gui %*
) else (
    python -m log_visualizer --gui %*
)
pause
