# Agent research contract

## Allowed

1. Edit **`research/research_params.json`** (copy from `research_params.example.json`).
2. Run **`python run_research_agent.py --params research/research_params.json`** (appends `logs/experiments.csv`).
3. Run **`python run_param_sweep.py`** or **`python sweep_optuna.py`** (after `pip install -r requirements-research.txt`).
4. Run **`python backtest.py`** with `--synthetic-bars` or `--csv` for reproducible checks.

## Forbidden without human approval

- Enabling **`REAL_TRADING`** or changing live wallet / Safe settings from an agent.
- Committing **`.env`** or secrets.

## Outputs

| File | Purpose |
|------|---------|
| `logs/experiments.csv` | Agent JSON runs |
| `logs/sweep_results.csv` | Grid sweep |
| `logs/attribution_summary.txt` | `summarize_attribution.py` |

Bot code lives under **`scripts/`**; root tools prepend `scripts/` to `sys.path` via `research/path_setup.py`.
