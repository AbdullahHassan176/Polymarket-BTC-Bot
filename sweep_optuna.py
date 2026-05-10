"""
sweep_optuna.py — Optional Bayesian search (install: pip install -r requirements-research.txt)

  python sweep_optuna.py --trials 15 --synthetic-bars 8000
"""

from __future__ import annotations

import sys

from research.path_setup import ensure_scripts_on_path

ensure_scripts_on_path()

import backtest  # noqa: E402
from research.harness import (  # noqa: E402
    apply_config_overrides,
    restore_config,
    snapshot_strategy_config,
    split_train_test_by_time,
)

try:
    import optuna
except ImportError:
    print("Install: pip install -r requirements-research.txt", file=sys.stderr)
    raise SystemExit(1) from None


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--trials", type=int, default=20)
    p.add_argument("--synthetic-bars", type=int, default=8000)
    p.add_argument("--train-frac", type=float, default=0.7)
    p.add_argument("--study-name", default="polymarket_btc_backtest")
    args = p.parse_args()

    df = backtest.deterministic_synthetic_1m(args.synthetic_bars)
    train_df, test_df = split_train_test_by_time(df, args.train_frac)
    if train_df.empty or test_df.empty:
        print("Bad split.", file=sys.stderr)
        return 1

    def objective(trial: optuna.Trial) -> float:
        ev = trial.suggest_int("eval_sec", 30, 120, step=15)
        sk = trial.suggest_float("synth_k", 25.0, 70.0)
        flo = trial.suggest_float("FALLBACK_IBS_LOW", 0.12, 0.28)
        fhi = trial.suggest_float("FALLBACK_IBS_HIGH", 0.72, 0.92)
        snap = snapshot_strategy_config()
        try:
            apply_config_overrides(
                {
                    "STRATEGY_MODE": "hybrid",
                    "FALLBACK_IBS_LOW": flo,
                    "FALLBACK_IBS_HIGH": fhi,
                }
            )
            te = backtest.run_backtest(test_df, eval_sec=ev, size_usdc=1.0, synth_k=sk)
            return float(te.total_pnl_usdc)
        finally:
            restore_config(snap)

    study = optuna.create_study(direction="maximize", study_name=args.study_name)
    study.optimize(objective, n_trials=args.trials, show_progress_bar=False)

    print("Best value (test total PnL):", study.best_value)
    print("Best params:", study.best_params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
