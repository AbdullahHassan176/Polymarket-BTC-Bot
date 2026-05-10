@echo off
echo Single LIVE BTC bot + watchdog (see scripts/config.py: RISK_PER_TRADE_USDC, REAL_TRADING).
echo Requires .env with POLY credentials and USDC + POL on Polygon. Stop: .\stop_bot.bat
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\launch\start_bot.ps1"
echo.
echo Watch: .\watch_bot.bat   Stop: .\stop_bot.bat   Status: .\bot_status.bat
pause
