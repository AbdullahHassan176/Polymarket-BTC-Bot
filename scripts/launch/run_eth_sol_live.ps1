# run_eth_sol_live.ps1 - Live ETH + SOL only.
# Analysis showed ETH (+11.3% ROI) and SOL (+14.2% ROI) are the profitable assets.
# BTC (win rate 19%, -5.8% ROI) and XRP (-11% ROI) excluded until strategy is proven.
# Stop: stop_bot.ps1 or create STOP_BOT.txt

$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
Set-Location $Root

Write-Host "Stopping any running bots..." -ForegroundColor Yellow
& "$Root\scripts\launch\stop_bot.ps1" 2>$null
Start-Sleep -Seconds 2

$KillSwitch = "$Root\STOP_BOT.txt"
if (Test-Path $KillSwitch) { Remove-Item $KillSwitch -Force }

$venv = Join-Path $Root "venv\Scripts\Activate.ps1"
if (Test-Path $venv) { . $venv }

foreach ($a in @("ETH", "SOL")) {
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
Write-Host "LIVE: ETH + SOL only  |  FOK exits  |  logs/trades.csv" -ForegroundColor Yellow
Write-Host "Stop: .\stop_bot.bat or create STOP_BOT.txt" -ForegroundColor Yellow
