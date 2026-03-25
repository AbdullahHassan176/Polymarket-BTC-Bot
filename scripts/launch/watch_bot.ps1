# watch_bot.ps1  -  Live bot output (refreshes every 1s)
# Tails logs/bot.log (all bots write here). Fallback: logs/btc_stdout.txt (watchdog mode).
$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
$BotLog = "$Root\logs\bot.log"
$Stdout = "$Root\logs\btc_stdout.txt"
$Tail = 80
$Refresh = 1

$LogFile = $null
if (Test-Path $BotLog) { $LogFile = $BotLog }
elseif (Test-Path $Stdout) { $LogFile = $Stdout }

if (-not $LogFile) {
    Write-Host "No log found. Start bots first:  .\start_bot.bat" -ForegroundColor Red
    Write-Host "  (looks for logs\bot.log or logs\btc_stdout.txt)" -ForegroundColor Gray
    exit 1
}

try {
    while ($true) {
        Clear-Host
        $name = if ($LogFile -eq $BotLog) { "BTC+SOL live, ETH/XRP/DOGE paper (bot.log)" } else { "BTC bot (stdout)" }
        Write-Host "  $name  |  $(Get-Date -Format 'HH:mm:ss')  |  refresh ${Refresh}s  |  Ctrl+C to exit" -ForegroundColor DarkCyan
        Write-Host ""
        Get-Content $LogFile -Tail $Tail -ErrorAction SilentlyContinue | Write-Host
        Start-Sleep -Seconds $Refresh
    }
} catch { }
