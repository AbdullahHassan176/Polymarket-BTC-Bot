# Research automation

| Step | Command |
|------|---------|
| Backtest | `python backtest.py --synthetic-bars 8000` |
| Grid + walk-forward | `python run_param_sweep.py --synthetic-bars 8000 --train-frac 0.7` |
| Quick sweep (CI) | `python run_param_sweep.py --quick --synthetic-bars 3000` |
| JSON experiment | `python run_research_agent.py --params research/research_params.example.json` |
| Optuna | `pip install -r requirements-research.txt` then `python sweep_optuna.py --trials 15` |
| Attribution | `python summarize_attribution.py --glob "logs/trades*.csv"` |
| Paper summary | `python paper_report.py --csv logs/trades.csv` |

## CI

`.github/workflows/research.yml` runs backtest + `run_research_agent.py` + quick sweep on push/PR (Python paths only).

## Rolling stop (live / paper)

`scripts/config.py`: `ROLLING_STOP_ENABLED`, `ROLLING_WINDOW_TRADES`, `ROLLING_STOP_MIN_TRADES`, `ROLLING_STOP_MAX_LOSS_USDC`. State: `recent_trade_pnls` in `state.json`.

## Agent rules

`research/AGENT_CONTRACT.md`
