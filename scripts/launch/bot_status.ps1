# bot_status.ps1  -  Status for BTC 5-min bots (multi-asset: BTC+SOL live, ETH/XRP/DOGE paper)
$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
$WdogPid = "$Root\watchdog_btc.pid"
$PidFile = "$Root\btc_bot.pid"
$SessCSV = "$Root\logs\btc_sessions.csv"
$StdoutLog = "$Root\logs\btc_stdout.txt"
$BotLog = "$Root\logs\bot.log"
$Sep = "=" * 60

Write-Host $Sep -ForegroundColor DarkGray
Write-Host "  BTC 5-MIN BOTS - STATUS  (BTC+SOL live, ETH/XRP/DOGE paper)" -ForegroundColor Cyan
Write-Host $Sep -ForegroundColor DarkGray

# Count running bot processes (bot.py)
$botProcs = Get-WmiObject Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*scripts\bot*" }
$nBots = if ($botProcs) { $botProcs.Count } else { 0 }
if ($nBots -gt 0) {
    Write-Host "  Bots     : $nBots running  (run  .\start_bot.bat  to start all)" -ForegroundColor Green
} else {
    Write-Host "  Bots     : NOT RUNNING  -  run  .\start_bot.bat" -ForegroundColor Red
}

# Watchdog (optional; only when using start_bot.ps1 / single-BTC mode)
if (Test-Path $WdogPid) {
    $wPid = Get-Content $WdogPid -ErrorAction SilentlyContinue
    $wProc = Get-Process -Id $wPid -ErrorAction SilentlyContinue
    if ($wProc) {
        Write-Host "  Watchdog : RUNNING  (PID $wPid)" -ForegroundColor Green
    }
}
Write-Host ""

# State + trades summary (BTC and SOL state files)
$StateFile = "$Root\state.json"
$StateSol = "$Root\state_SOL.json"
$TradesCsv = "$Root\logs\trades.csv"
foreach ($sf in @(@{f=$StateFile; n="BTC"}, @{f=$StateSol; n="SOL"})) {
    if (Test-Path $sf.f) {
        $state = Get-Content $sf.f -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
        if ($state) {
            $start = [double]$state.starting_balance_usdc
            $cum = [double]$state.cumulative_pnl_usdc
            $bal = $start + $cum
            Write-Host "  $($sf.n) balance : `$$([math]::Round($bal, 2))  |  PnL: `$$([math]::Round($cum, 2))" -ForegroundColor Cyan
        }
    }
}
Write-Host ""
if (Test-Path $TradesCsv) {
    $rows = Import-Csv $TradesCsv -ErrorAction SilentlyContinue
    $closes = $rows | Where-Object { $_.action -eq "CLOSE" }
    $wins = $closes | Where-Object { $_.outcome -eq "WIN" }
    $losses = $closes | Where-Object { $_.outcome -eq "LOSS" }
    if ($closes) {
        $wr = [math]::Round(100 * $wins.Count / $closes.Count, 0)
        Write-Host "  Trades   : $($closes.Count) completed  |  W: $($wins.Count)  L: $($losses.Count)  |  Win rate: $wr%" -ForegroundColor Cyan
    }
}

if (Test-Path $SessCSV) {
    Write-Host ""
    Write-Host $Sep -ForegroundColor DarkGray
    Write-Host "  SESSION HISTORY  (last 5)" -ForegroundColor Cyan
    Write-Host $Sep -ForegroundColor DarkGray
    Import-Csv $SessCSV -ErrorAction SilentlyContinue | Select-Object -Last 5 | Format-Table -AutoSize | Out-String | Write-Host
}

$LogToShow = if (Test-Path $BotLog) { $BotLog } else { $StdoutLog }
if (Test-Path $LogToShow) {
    Write-Host $Sep -ForegroundColor DarkGray
    $logName = if ($LogToShow -eq $BotLog) { "logs/bot.log (all bots)" } else { "logs/btc_stdout.txt" }
    Write-Host "  LAST 20 LINES  ($logName)" -ForegroundColor Cyan
    Write-Host $Sep -ForegroundColor DarkGray
    Get-Content $LogToShow -Tail 20 -ErrorAction SilentlyContinue | Write-Host
}

Write-Host ""
Write-Host "  Watch: .\watch_bot.bat  |  Dashboard: .\dashboard_bot.bat  |  Stop: .\stop_bot.bat" -ForegroundColor DarkGray
Write-Host $Sep -ForegroundColor DarkGray
