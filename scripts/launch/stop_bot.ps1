# stop_bot.ps1  -  Stop BTC bot + watchdog + all related bot processes
$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
$KillSwitch = "$Root\STOP_BOT.txt"
$WdogPid = "$Root\watchdog_btc.pid"
$PidFile = "$Root\btc_bot.pid"

# Create kill switch so any bot still running will exit on next loop
$null = New-Item -Path $KillSwitch -ItemType File -Force

if (Test-Path $WdogPid) {
    $wPid = Get-Content $WdogPid -ErrorAction SilentlyContinue
    if ($wPid) {
        $p = Get-Process -Id $wPid -ErrorAction SilentlyContinue
        if ($p) {
            Write-Host "Stopping watchdog PID $wPid..." -ForegroundColor Yellow
            Stop-Process -Id $wPid -Force
            Write-Host "Watchdog stopped." -ForegroundColor Green
        }
    }
    Remove-Item $WdogPid -ErrorAction SilentlyContinue
}
if (Test-Path $PidFile) {
    $botPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($botPid) {
        Stop-Process -Id $botPid -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
}

# Kill all Python processes running watchdog, bot.py or run_*.py (job with timeout so WMI doesn't hang)
$job = Start-Job -ScriptBlock {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -like "*watchdog_btc*" -or $_.CommandLine -like "*scripts\bot*" -or $_.CommandLine -like "*scripts\run_*"
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $_.ProcessId
    }
}
$pids = Wait-Job $job -Timeout 12 | Receive-Job $job; Remove-Job $job -Force -ErrorAction SilentlyContinue
if ($pids) {
    Write-Host "Stopped bot process(es): $($pids -join ', ')" -ForegroundColor Yellow
}

# Synchronous fallback: ensure any remaining watchdog/bot Python process is killed (avoids missed kills from job timeout or path mismatch)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $cmd = if ($_.CommandLine) { $_.CommandLine } else { "" }
    $cmd -like "*watchdog_btc*" -or $cmd -like "*scripts\bot*" -or $cmd -like "*scripts\run_*"
} | ForEach-Object {
    Write-Host "Stopping remaining process PID $($_.ProcessId)..." -ForegroundColor Yellow
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Remove-Item $WdogPid -ErrorAction SilentlyContinue
Remove-Item $PidFile -ErrorAction SilentlyContinue

# Leave STOP_BOT.txt in place so any remaining bot exits on next loop. start_bot.bat clears it when restarting.
Write-Host "BTC bot fully stopped. (STOP_BOT.txt left in place.)" -ForegroundColor Green
Write-Host "Run  .\start_bot.bat  to restart."
