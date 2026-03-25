# Paper trading for ALL 5 assets (BTC, ETH, XRP, SOL, DOGE) with full data tracking.
# Outputs per session to logs/paper_run_<timestamp>/:
#   price_paths.csv       - yes/no price every second + issues flags
#   signals_evaluated.csv - every signal evaluated, traded Y/N, reason
#   paper_trades.csv      - all paper trade opens/closes with PnL
# Includes all strategy improvements: sustained-dip filter, slice gap, shifted TP tiers.
# Stop: stop_bot.ps1 or create STOP_BOT.txt

$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
Set-Location $Root

Write-Host "Stopping any running bots..." -ForegroundColor Yellow
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

$KillSwitch = "$Root\STOP_BOT.txt"
if (Test-Path $KillSwitch) { Remove-Item $KillSwitch -Force }

# Timestamped run directory — all 5 assets write here
$RunDir = "$Root\logs\paper_run_{0:yyyyMMdd_HHmmss}" -f (Get-Date)
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
Write-Host "Paper run dir: $RunDir" -ForegroundColor Green

# Detect python executable
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($PythonCmd) { $PythonExe = $PythonCmd.Source } else { $PythonExe = "C:\Users\dullz\AppData\Local\Programs\Python\Python311\python.exe" }

# Collect current environment vars to pass to child processes
$EnvSnapshot = @{}
[System.Environment]::GetEnvironmentVariables().GetEnumerator() | ForEach-Object {
    try { $EnvSnapshot[$_.Key] = $_.Value } catch {}
}
$EnvSnapshot["PAPER_RUN_DIR"]  = $RunDir
$EnvSnapshot["LOG_PRICE_PATH"] = "1"

$assets = @("BTC", "ETH", "XRP", "SOL", "DOGE")
foreach ($a in $assets) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName        = $PythonExe
    $psi.Arguments       = "scripts/run_paper_with_price_logging.py"
    $psi.WorkingDirectory = $Root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow  = $false

    foreach ($kvp in $EnvSnapshot.GetEnumerator()) {
        try { $psi.Environment[$kvp.Key] = $kvp.Value } catch {}
    }
    $psi.Environment["TRADING_ASSET"]  = $a
    $psi.Environment["PAPER_RUN_DIR"]  = $RunDir
    $psi.Environment["LOG_PRICE_PATH"] = "1"

    $p = [System.Diagnostics.Process]::Start($psi)
    Write-Host "Started PAPER $a  PID=$($p.Id)" -ForegroundColor Cyan
    Start-Sleep -Milliseconds 500   # stagger starts to avoid CLOB rate limits
}

Write-Host ""
Write-Host "=== ALL 5 ASSETS PAPER RUNNING ===" -ForegroundColor Yellow
Write-Host "  Data dir : $RunDir" -ForegroundColor White
Write-Host "  Tracking : price_paths.csv (1s) + signals_evaluated.csv + paper_trades.csv" -ForegroundColor Gray
Write-Host "  Filters  : sustained-dip 20s, slice-gap 45s, shifted TP tiers" -ForegroundColor Gray
Write-Host "  Stop     : .\stop_bot.bat  or  New-Item STOP_BOT.txt" -ForegroundColor Gray
