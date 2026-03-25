# run_4asset_live.ps1 - Live trading for BTC, ETH, XRP, SOL (no DOGE - insufficient reversal activity).
# All 4 -> logs/trades.csv | $5/trade (RISK_PER_TRADE_USDC in config.py)
# Stop: stop_bot.ps1 or create STOP_BOT.txt

$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
Set-Location $Root

Write-Host "Stopping any running bots..." -ForegroundColor Yellow
& "$Root\scripts\launch\stop_bot.ps1" 2>$null
Start-Sleep -Seconds 2

# Remove kill switch
$KillSwitch = "$Root\STOP_BOT.txt"
if (Test-Path $KillSwitch) {
    Remove-Item $KillSwitch -Force
    Write-Host "Removed STOP_BOT.txt." -ForegroundColor Cyan
}

# Activate venv if present
$venv = Join-Path $Root "venv\Scripts\Activate.ps1"
if (Test-Path $venv) { . $venv }

$liveAssets = @("BTC", "ETH", "XRP", "SOL")
foreach ($a in $liveAssets) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "python"
    $psi.Arguments = "scripts/bot.py --real"
    $psi.WorkingDirectory = $Root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.Environment["TRADING_ASSET"] = $a
    $p = [System.Diagnostics.Process]::Start($psi)
    Write-Host "Started LIVE $a (PID $($p.Id)) -> logs/trades.csv" -ForegroundColor Green
}

Write-Host ""
Write-Host "LIVE: BTC + ETH + XRP + SOL  |  `$5/trade  |  logs/trades.csv" -ForegroundColor Yellow
Write-Host "Stop: .\stop_bot.bat or create STOP_BOT.txt" -ForegroundColor Yellow
