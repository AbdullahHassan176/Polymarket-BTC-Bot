@echo off
echo Stopping all bots, then starting paper run (all 5 assets, 1s price path)...
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\launch\run_paper_all_price_logging.ps1"
echo.
echo Data in logs\paper_run_YYYYMMDD_HHMMSS\
pause
