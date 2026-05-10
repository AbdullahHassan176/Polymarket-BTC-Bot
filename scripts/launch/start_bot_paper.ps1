# start_bot_paper.ps1  -  Start BTC bot + watchdog in PAPER mode (no CLOB orders).
$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
$Python = if (Test-Path "$Root\.venv\Scripts\python.exe") { "$Root\.venv\Scripts\python.exe" } elseif (Test-Path "$Root\venv\Scripts\python.exe") { "$Root\venv\Scripts\python.exe" } else { "python" }
$Watchdog = "$Root\scripts\watchdog_btc.py"
$PidFile = "$Root\btc_bot.pid"
$WdogPid = "$Root\watchdog_btc.pid"
$KillSwitch = "$Root\STOP_BOT.txt"

if (-not (Test-Path $Watchdog)) { Write-Host "ERROR: watchdog_btc.py not found" -ForegroundColor Red; exit 1 }

if (Test-Path $KillSwitch) { Remove-Item $KillSwitch -Force; Write-Host "Cleared stale STOP_BOT.txt" -ForegroundColor Yellow }

$env:BOT_SCRIPT = "$Root\scripts\bot.py"

foreach ($pf in @($WdogPid, $PidFile)) {
    if (Test-Path $pf) {
        $oldId = Get-Content $pf -ErrorAction SilentlyContinue
        if ($oldId) { Stop-Process -Id $oldId -Force -ErrorAction SilentlyContinue }
        Remove-Item $pf -ErrorAction SilentlyContinue
    }
}
Get-WmiObject Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*watchdog_btc*" -or $_.CommandLine -like "*scripts\bot*" -or $_.CommandLine -like "*scripts\run_*"
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

Write-Host "Starting BTC bot watchdog (PAPER mode)..." -ForegroundColor Cyan
$env:BOT_ARGS = "--paper"
$proc = Start-Process -FilePath $Python -ArgumentList "-u `"$Watchdog`"" -WorkingDirectory $Root -WindowStyle Normal -PassThru
$proc.Id | Out-File -FilePath $WdogPid -Encoding ascii
Write-Host "Watchdog PID $($proc.Id)" -ForegroundColor Green
Write-Host "  Trades: logs\paper_trades.csv  |  Watch: .\watch_bot.bat  |  Stop: .\stop_bot.bat" -ForegroundColor Yellow
