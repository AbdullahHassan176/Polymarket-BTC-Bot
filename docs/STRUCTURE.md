# Repository Structure

Root holds **.bat launchers** plus config. Python scripts in `scripts/`.

```
├── start_bot.bat           # Start bot + watchdog
├── stop_bot.bat            # Stop bot + watchdog
├── restart_bot.bat         # Stop + start
├── bot_status.bat          # Status, trades, PnL, last log
├── watch_bot.bat           # Live output (refresh 1s)
├── dashboard_bot.bat       # Web dashboard (Streamlit, http://localhost:8501)
├── claim_unclaimed.bat     # Claim unclaimed positions
│
├── .env, .gitignore, requirements.txt, README.md, ai.md
│
├── scripts/
│   ├── bot.py              # Main trading loop
│   ├── config.py           # All settings
│   ├── polymarket_client.py, data.py, strategy.py
│   ├── execution.py, risk.py, btc_5m_fair_value.py, redeem.py
│   ├── run_*.py            # Run scripts (reversal, paper, ml profit)
│   ├── analyze_tier_performance.py, check_markets.py
│   ├── watchdog_btc.py     # Auto-restart loop
│   ├── launch/             # PowerShell (invoked by .bat)
│   │   ├── start_bot.ps1, stop_bot.ps1, bot_status.ps1, watch_bot.ps1
│   ├── ml/                 # ML direction model
│   └── arbitrage/          # Arbitrage module
│
├── lib/
│   └── ctf_redeem.py   # On-chain CTF redemption (Gnosis Safe)
├── docs/
│
├── logs/                   # Session logs, trades (gitignored)
├── state.json              # Risk state (gitignored)
```

## Quick commands

| Command | Action |
|---------|--------|
| `.\start_bot.bat` | Start bot + watchdog (paper mode) |
| `.\stop_bot.bat` | Stop bot |
| `.\restart_bot.bat` | Stop + start |
| `.\bot_status.bat` | Status, trades, PnL, win rate, last log |
| `.\watch_bot.bat` | Live output |
| `.\dashboard_bot.bat` | Web dashboard (trades, PnL, balance, win/loss) |
| `.\claim_unclaimed.bat` | Claim unclaimed positions (manual recovery) |

## Run specific strategies

To run reversal (12hr) instead of default bot:
```powershell
$env:BOT_SCRIPT = "d:\...\Polymarket-BTC-Bot\scripts\run_12hr_reversal.py"
$env:BOT_ARGS = ""
.\start_bot.bat
```

Or run manually:
```powershell
cd d:\Experimentation\Polymarket-BTC-Bot
python scripts/run_12hr_reversal.py
python scripts/analyze_tier_performance.py --csv logs/trades_12hr_reversal.csv
python scripts/ml/backfill_training_data.py --windows 600
python scripts/ml/train_direction_model.py --backfill logs/ml_training_backfill.csv
```
