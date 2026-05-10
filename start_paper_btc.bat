@echo off
echo PAPER trading: BTC bot + watchdog (no real orders). REAL_TRADING=False in scripts\config.py recommended.
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\launch\start_bot_paper.ps1"
echo.
pause
