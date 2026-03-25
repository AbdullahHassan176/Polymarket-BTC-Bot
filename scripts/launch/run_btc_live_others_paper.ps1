# run_btc_live_others_paper.ps1 - 1 live BTC + 1 live SOL + 3 paper (ETH, XRP, DOGE)
# Real BTC and SOL -> logs/trades.csv
# Paper ETH/XRP/DOGE -> logs/paper_trades.csv
$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
Set-Location $Root

# Stop any running bots first
Write-Host "Stopping any running bots..." -ForegroundColor Yellow
& "$Root\scripts\launch\stop_bot.ps1" 2>$null

# Activate venv if present
$venv = Join-Path $Root "venv\Scripts\Activate.ps1"
if (Test-Path $venv) { . $venv }

# Ensure REAL_TRADING=True in config for live bots.
# 1. Live BTC bot
$psiBtc = New-Object System.Diagnostics.ProcessStartInfo
$psiBtc.FileName = "python"
$psiBtc.Arguments = "scripts/bot.py --real"
$psiBtc.WorkingDirectory = $Root
$psiBtc.UseShellExecute = $false
$psiBtc.CreateNoWindow = $true
$psiBtc.Environment["TRADING_ASSET"] = "BTC"
$pBtc = [System.Diagnostics.Process]::Start($psiBtc)
Write-Host "Started LIVE BTC bot (PID $($pBtc.Id)) -> logs/trades.csv" -ForegroundColor Green

# 2. Live SOL bot (same size as BTC unless RISK_PER_TRADE_ALT_USDC is set)
$psiSol = New-Object System.Diagnostics.ProcessStartInfo
$psiSol.FileName = "python"
$psiSol.Arguments = "scripts/bot.py --real"
$psiSol.WorkingDirectory = $Root
$psiSol.UseShellExecute = $false
$psiSol.CreateNoWindow = $true
$psiSol.Environment["TRADING_ASSET"] = "SOL"
$pSol = [System.Diagnostics.Process]::Start($psiSol)
Write-Host "Started LIVE SOL bot (PID $($pSol.Id)) -> logs/trades.csv" -ForegroundColor Green

# 3. Paper bots for ETH, XRP, DOGE
$paperAssets = @("ETH", "XRP", "DOGE")
foreach ($a in $paperAssets) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "python"
    $psi.Arguments = "scripts/bot.py --paper"
    $psi.WorkingDirectory = $Root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.Environment["TRADING_ASSET"] = $a
    $p = [System.Diagnostics.Process]::Start($psi)
    Write-Host "Started PAPER $a bot (PID $($p.Id)) -> logs/paper_trades.csv" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "1 live BTC + 1 live SOL + 3 paper (ETH, XRP, DOGE). Stop: stop_bot.ps1 or STOP_BOT.txt" -ForegroundColor Yellow
Write-Host "Real: logs/trades.csv | Paper: logs/paper_trades.csv" -ForegroundColor Yellow
