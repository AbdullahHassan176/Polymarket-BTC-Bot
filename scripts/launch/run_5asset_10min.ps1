# run_5asset_10min.ps1 - Paper trade all 5 assets for 10 minutes, then stop.
$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
Set-Location $Root

# Stop any running bots first
Write-Host "Stopping any running bots..." -ForegroundColor Yellow
& "$Root\scripts\launch\stop_bot.ps1" 2>$null

# Activate venv if present
$venv = Join-Path $Root "venv\Scripts\Activate.ps1"
if (Test-Path $venv) { . $venv }

$assets = @("BTC", "ETH", "XRP", "SOL", "DOGE")
$procs = @()

foreach ($a in $assets) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "python"
    $psi.Arguments = "scripts/bot.py --paper"
    $psi.WorkingDirectory = $Root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.Environment["TRADING_ASSET"] = $a
    $p = [System.Diagnostics.Process]::Start($psi)
    $procs += $p
    Write-Host "Started $a paper bot (PID $($p.Id))" -ForegroundColor Green
}

Write-Host "`nRunning 5 paper bots for 10 minutes..." -ForegroundColor Cyan
Start-Sleep -Seconds 600

Write-Host "`nStopping all bots..." -ForegroundColor Yellow
New-Item -Path "$Root\STOP_BOT.txt" -ItemType File -Force | Out-Null
Start-Sleep -Seconds 5

foreach ($p in $procs) {
    if (-not $p.HasExited) {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped PID $($p.Id)" -ForegroundColor Yellow
    }
}
Remove-Item "$Root\STOP_BOT.txt" -ErrorAction SilentlyContinue

Get-WmiObject Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*scripts\bot*"
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped stray bot PID $($_.ProcessId)" -ForegroundColor Yellow
}

Write-Host "`nDone. All 5 asset paper bots stopped." -ForegroundColor Green
