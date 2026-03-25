# DOGE live trading + BTC/ETH/XRP/SOL paper trading.
# DOGE is proven (82%+ win rate lower bound, 195 trades).
# Others remain on paper until confidence intervals clear.
#
# Stop: .\stop_bot.bat  or  New-Item STOP_BOT.txt

$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
Set-Location $Root

Write-Host "Stopping any running bots..." -ForegroundColor Yellow
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

$KillSwitch = "$Root\STOP_BOT.txt"
if (Test-Path $KillSwitch) { Remove-Item $KillSwitch -Force }

$RunDir = "$Root\logs\paper_run_{0:yyyyMMdd_HHmmss}" -f (Get-Date)
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
Write-Host "Paper run dir (for non-live assets): $RunDir" -ForegroundColor Green

$VenvPython = "$Root\venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
} else {
    $PythonExe = "C:\Users\dullz\AppData\Local\Programs\Python\Python311\python.exe"
}
Write-Host "Using Python: $PythonExe" -ForegroundColor Gray

# DOGE — LIVE
$env:TRADING_ASSET  = "DOGE"
$env:PAPER_RUN_DIR  = ""
$env:LOG_PRICE_PATH = "0"
$p = Start-Process -FilePath $PythonExe `
    -ArgumentList "scripts\bot.py --real" `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru
Write-Host "  [LIVE]  DOGE  PID=$($p.Id)" -ForegroundColor Green
Start-Sleep -Milliseconds 600

# BTC/ETH/XRP/SOL — PAPER (with price logging for data collection)
foreach ($a in @("BTC","ETH","XRP","SOL")) {
    $env:TRADING_ASSET  = $a
    $env:PAPER_RUN_DIR  = $RunDir
    $env:LOG_PRICE_PATH = "1"
    $p = Start-Process -FilePath $PythonExe `
        -ArgumentList "scripts\run_paper_with_price_logging.py" `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    Write-Host "  [paper] $a   PID=$($p.Id)" -ForegroundColor Cyan
    Start-Sleep -Milliseconds 500
}

Write-Host ""
Write-Host "=== RUNNING ===" -ForegroundColor Yellow
Write-Host "  LIVE  : DOGE" -ForegroundColor Green
Write-Host "  PAPER : BTC, ETH, XRP, SOL (data collection continues)" -ForegroundColor Cyan
Write-Host "  Paper data: $RunDir" -ForegroundColor Gray
Write-Host "  Stop: .\stop_bot.bat" -ForegroundColor Gray
