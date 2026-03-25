# monitor_live.ps1  -  Live trading monitor. Refreshes every 5s.
# Shows: live DOGE trades, paper stats, running processes, recent log lines.
$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path

# Only count trades from today's session (after bot launch time)
$SessionStart = (Get-Date).ToUniversalTime().Date  # midnight UTC today

function Parse-Pnl($val) {
    # Handle both "1.23" and "1,23" decimal formats
    try { return [double]($val -replace ',','.') } catch { return 0.0 }
}

function Get-LatestTrades($csv_path, $n=8) {
    if (-not (Test-Path $csv_path)) { return @() }
    try {
        $rows = Import-Csv $csv_path | Where-Object { $_.action -eq "CLOSE" } |
            Where-Object {
                try { [datetime]::Parse($_.timestamp).ToUniversalTime() -ge $SessionStart } catch { $false }
            }
        return $rows | Select-Object -Last $n
    } catch { return @() }
}

function Get-PnlSummary($csv_path) {
    if (-not (Test-Path $csv_path)) { return $null }
    try {
        $rows = Import-Csv $csv_path | Where-Object { $_.action -eq "CLOSE" } |
            Where-Object {
                try { [datetime]::Parse($_.timestamp).ToUniversalTime() -ge $SessionStart } catch { $false }
            }
        if ($rows.Count -eq 0) { return $null }
        $net  = ($rows | ForEach-Object { Parse-Pnl $_.pnl_usdc } | Measure-Object -Sum).Sum
        $wins = ($rows | Where-Object { (Parse-Pnl $_.pnl_usdc) -gt 0 }).Count
        $tot  = $rows.Count
        return @{ net=$net; wins=$wins; total=$tot; wr=[math]::Round($wins/$tot*100) }
    } catch { return $null }
}

$LiveTradesCSV  = "$Root\logs\trades.csv"
$PaperDir       = (Get-ChildItem "$Root\logs" -Directory | Where-Object { $_.Name -match "^paper_run_2" } | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$PaperTradesCSV = if ($PaperDir) { "$PaperDir\paper_trades.csv" } else { $null }

while ($true) {
    Clear-Host
    $now   = Get-Date -Format "HH:mm:ss"
    $procs = (Get-Process python -ErrorAction SilentlyContinue).Count
    Write-Host "  LIVE MONITOR  |  $now  |  Python procs: $procs  |  Ctrl+C to stop" -ForegroundColor DarkCyan
    Write-Host ""

    # --- DOGE LIVE trades
    Write-Host "  [DOGE LIVE]" -ForegroundColor Green
    $live        = Get-LatestTrades $LiveTradesCSV 8
    $liveSummary = Get-PnlSummary   $LiveTradesCSV
    if ($liveSummary) {
        $col    = if ($liveSummary.net -ge 0) { "Green" } else { "Red" }
        $netStr = if ($liveSummary.net -ge 0) { ("+{0:0.00}" -f $liveSummary.net) } else { ("{0:0.00}" -f $liveSummary.net) }
        Write-Host ("    Net PnL: `${0}   Win: {1}%  ({2}/{3} trades)" -f $netStr, $liveSummary.wr, $liveSummary.wins, $liveSummary.total) -ForegroundColor $col
    } else {
        Write-Host "    No live trades yet today." -ForegroundColor Gray
    }
    foreach ($r in $live) {
        $pnl    = Parse-Pnl $r.pnl_usdc
        $col    = if ($pnl -gt 0) { "Green" } else { "Red" }
        $ts     = try { ([datetime]::Parse($r.timestamp)).ToString("HH:mm:ss") } catch { "??:??:??" }
        $pnlStr = if ($pnl -ge 0) { ("+{0:0.00}" -f $pnl) } else { ("{0:0.00}" -f $pnl) }
        Write-Host ("    {0}  {1,-22}  {2,8}  entry={3}" -f $ts, $r.outcome, $pnlStr, $r.entry_price) -ForegroundColor $col
    }

    Write-Host ""

    # --- Paper assets summary
    Write-Host "  [PAPER: BTC/ETH/XRP/SOL]" -ForegroundColor Cyan
    if ($PaperTradesCSV) {
        $paperSummary = Get-PnlSummary $PaperTradesCSV
        if ($paperSummary) {
            $col    = if ($paperSummary.net -ge 0) { "Cyan" } else { "DarkYellow" }
            $netStr = if ($paperSummary.net -ge 0) { ("+{0:0.00}" -f $paperSummary.net) } else { ("{0:0.00}" -f $paperSummary.net) }
            Write-Host ("    Net PnL: `${0}   Win: {1}%  ({2}/{3} trades)" -f $netStr, $paperSummary.wr, $paperSummary.wins, $paperSummary.total) -ForegroundColor $col
        } else {
            Write-Host "    No paper trades yet." -ForegroundColor Gray
        }
        $paperRecent = Get-LatestTrades $PaperTradesCSV 4
        foreach ($r in $paperRecent) {
            $pnl = Parse-Pnl $r.pnl_usdc
            $col = if ($pnl -gt 0) { "Cyan" } else { "DarkYellow" }
            $q   = $r.question
            $a   = if ($q -match "Bitcoin") { "BTC" } elseif ($q -match "Ethereum") { "ETH" } elseif ($q -match "XRP") { "XRP" } elseif ($q -match "Solana") { "SOL" } else { "???" }
            $ts     = try { ([datetime]::Parse($r.timestamp)).ToString("HH:mm:ss") } catch { "??:??:??" }
            $pnlStr = if ($pnl -ge 0) { ("+{0:0.00}" -f $pnl) } else { ("{0:0.00}" -f $pnl) }
            Write-Host ("    {0}  {1}  {2,-22}  {3,8}" -f $ts, $a, $r.outcome, $pnlStr) -ForegroundColor $col
        }
    } else {
        Write-Host "    No paper run dir found." -ForegroundColor Gray
    }

    Write-Host ""
    Write-Host "  (refreshing every 5s - Ctrl+C to exit)" -ForegroundColor DarkGray
    Start-Sleep -Seconds 5
}
