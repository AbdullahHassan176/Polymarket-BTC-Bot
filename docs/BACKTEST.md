# Strategy backtest (`backtest.py` at repo root)

Imports signal logic from **`scripts/`** (see `research/path_setup.py`).

## Model

- OKX **1m** history via `scripts.data.fetch_historical_1m_candles`, or `--csv` / `--synthetic-bars`.
- **Synthetic YES/NO** from in-window BTC drift unless `--yes-no-csv` provides `window_start,yes_mid,no_mid`.
- **Outcome**: last close in 5m UTC window vs first open — YES wins if `close >= open`.
- **Context**: passes `secs_remaining` into `strategy.check_signal` for `ENTRY_MIN_SECS_REMAINING` rules.

TP/SL from the live bot are **not** replayed here.

## Commands

```powershell
python backtest.py --synthetic-bars 8000
python backtest.py --days 5
python backtest.py --csv path/to/btc_1m.csv --yes-no-csv window_mids.csv
python backtest.py --synthetic-bars 5000 --ts-after 2024-01-03T00:00:00Z --ts-before 2024-01-05T00:00:00Z
```

See **`docs/RESEARCH_AUTOMATION.md`** for sweeps and CI.
