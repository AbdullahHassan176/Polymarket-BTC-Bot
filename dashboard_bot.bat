@echo off
cd /d "%~dp0"
echo Dashboard: http://localhost:8765
if exist venv\Scripts\python.exe (
    venv\Scripts\python.exe dashboard\server.py
) else if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe dashboard\server.py
) else (
    python dashboard\server.py
)
pause
