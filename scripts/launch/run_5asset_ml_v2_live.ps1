# run_5asset_ml_v2_live.ps1 - Live ml_v2 trading across all 5 assets simultaneously.
# Strategy: STRATEGY_MODE=ml_v2 (already set in config.py)
# Edge: LGB+XGB ensemble, hold-to-expiry, EV-gated entries (model_p - entry >= 0.03)
# All 5 assets -> logs/trades.csv | Kelly-sized trades (10% bankroll max)
# Stop: .\stop_bot.bat or create STOP_BOT.txt

$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
Set-Location $Root

Write-Host "Stopping any running bots..." -ForegroundColor Yellow
& "$Root\scripts\launch\stop_bot.ps1" 2>$null
Start-Sleep -Seconds 2

# Remove kill switch if present
$KillSwitch = "$Root\STOP_BOT.txt"
if (Test-Path $KillSwitch) {
    Remove-Item $KillSwitch -Force
    Write-Host "Removed STOP_BOT.txt." -ForegroundColor Cyan
}

# Activate venv
$venv = Join-Path $Root "venv\Scripts\Activate.ps1"
if (Test-Path $venv) { . $venv }

$assets = @("BTC", "ETH", "XRP", "SOL", "DOGE")
$procs = @()

foreach ($a in $assets) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "python"
    $psi.Arguments = "scripts/bot.py --real"
    $psi.WorkingDirectory = $Root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.Environment["TRADING_ASSET"] = $a
    $p = [System.Diagnostics.Process]::Start($psi)
    $procs += $p
    Write-Host "Started LIVE ml_v2 $a (PID $($p.Id))" -ForegroundColor Green
}

Write-Host ""
Write-Host "LIVE ml_v2: BTC + ETH + XRP + SOL + DOGE" -ForegroundColor Cyan
Write-Host "Edge: 54.9% win @ >5% conf | 61.5% win @ >10% conf" -ForegroundColor Cyan
Write-Host "EV gate: model_p - entry >= 0.03 | Max entry: 0.52 | Kelly sizing" -ForegroundColor Cyan
Write-Host "Hold to expiry: no spread cost on exit" -ForegroundColor Cyan
Write-Host "Stop: .\stop_bot.bat or create STOP_BOT.txt" -ForegroundColor Yellow
