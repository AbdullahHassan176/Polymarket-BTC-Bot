# start_bot.ps1  -  Start BTC bot + watchdog
$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
$Python = if (Test-Path "$Root\.venv\Scripts\python.exe") { "$Root\.venv\Scripts\python.exe" } elseif (Test-Path "$Root\venv\Scripts\python.exe") { "$Root\venv\Scripts\python.exe" } else { "python" }
$Watchdog = "$Root\scripts\watchdog_btc.py"
$PidFile = "$Root\btc_bot.pid"
$WdogPid = "$Root\watchdog_btc.pid"
$KillSwitch = "$Root\STOP_BOT.txt"

if (-not (Test-Path $Watchdog)) { Write-Host "ERROR: watchdog_btc.py not found" -ForegroundColor Red; exit 1 }

# Remove stale kill switch so bot runs (run_* timed scripts create it; start_bot uses bot.py which never does)
if (Test-Path $KillSwitch) { Remove-Item $KillSwitch -Force; Write-Host "Cleared stale STOP_BOT.txt" -ForegroundColor Yellow }

# Ensure we run main bot.py, not a timed run_* script (which would create STOP_BOT.txt after N hours)
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

Write-Host "Starting BTC bot watchdog (REAL mode)..." -ForegroundColor Cyan
$env:BOT_ARGS = "--real"
$proc = Start-Process -FilePath $Python -ArgumentList "-u `"$Watchdog`"" -WorkingDirectory $Root -WindowStyle Normal -PassThru
$proc.Id | Out-File -FilePath $WdogPid -Encoding ascii
Write-Host "Watchdog PID $($proc.Id)" -ForegroundColor Green
Write-Host "  Watch: .\watch_bot.bat  |  Stop: .\stop_bot.bat  |  Status: .\bot_status.bat" -ForegroundColor Yellow
