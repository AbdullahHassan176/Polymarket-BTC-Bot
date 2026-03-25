@echo off
echo Starting LIVE trading: ETH + SOL (best performers, FOK exits)...
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\launch\run_eth_sol_live.ps1"
echo.
echo Live trades: logs\trades.csv
pause
