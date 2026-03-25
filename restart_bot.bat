@echo off
echo Stopping all bots...
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\launch\stop_bot.ps1"
timeout /t 2 /nobreak >nul
echo.
echo Restarting: 1 live BTC + 1 live SOL + 3 paper (ETH, XRP, DOGE)...
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\launch\run_btc_live_others_paper.ps1"
echo.
echo Watch: .\watch_bot.bat   Stop: .\stop_bot.bat
pause
