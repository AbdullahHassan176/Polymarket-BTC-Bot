"""
run_param_sweep.py — Grid search + walk-forward on backtest (uses scripts/).

  python run_param_sweep.py --synthetic-bars 8000 --train-frac 0.7
  python run_param_sweep.py --quick --synthetic-bars 3000
"""

from __future__ import annotations

import argparse
import csv
import itertools
import os
import sys
from datetime import datetime, timezone
from typing import Any

from research.path_setup import ensure_scripts_on_path

ensure_scripts_on_path()

import backtest  # noqa: E402
import data  # noqa: E402
from research.harness import (  # noqa: E402
    apply_config_overrides,
    restore_config,
    snapshot_strategy_config,
    split_train_test_by_time,
)

SWEEP_FIELDS = [
    "run_id",
    "STRATEGY_MODE",
    "eval_sec",
    "synth_k",
    "train_windows",
    "train_trades",
    "train_pnl",
    "train_win_rate",
    "test_windows",
    "test_trades",
    "test_pnl",
    "test_win_rate",
]


def _load_df(args: argparse.Namespace):
    if args.csv:
        import pandas as pd

        df = pd.read_csv(args.csv)
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        return df
    if args.synthetic_bars > 0:
        return backtest.deterministic_synthetic_1m(args.synthetic_bars)
    return data.fetch_historical_1m_candles(args.days)


def _win_rate(r: backtest.BacktestResult) -> float:
    if r.trades <= 0:
        return 0.0
    return 100.0 * r.wins / r.trades


def _run_combo(
    df_train,
    df_test,
    combo: dict[str, Any],
) -> dict[str, Any]:
    snap = snapshot_strategy_config()
    try:
        apply_config_overrides(combo)
        tr = backtest.run_backtest(
            df_train,
            eval_sec=combo.get("eval_sec", 60),
            size_usdc=1.0,
            synth_k=combo.get("synth_k", 45.0),
        )
        te = backtest.run_backtest(
            df_test,
            eval_sec=combo.get("eval_sec", 60),
            size_usdc=1.0,
            synth_k=combo.get("synth_k", 45.0),
        )
        return {
            "train_windows": tr.windows,
            "train_trades": tr.trades,
            "train_pnl": tr.total_pnl_usdc,
            "train_win_rate": round(_win_rate(tr), 2),
            "test_windows": te.windows,
            "test_trades": te.trades,
            "test_pnl": te.total_pnl_usdc,
            "test_win_rate": round(_win_rate(te), 2),
        }
    finally:
        restore_config(snap)


def default_grid(quick: bool = False) -> list[dict[str, Any]]:
    if quick:
        return [
            {"STRATEGY_MODE": "hybrid", "eval_sec": 60, "synth_k": 45.0},
            {"STRATEGY_MODE": "momentum", "eval_sec": 60, "synth_k": 45.0},
        ]
    modes = ["hybrid", "momentum", "contrarian"]
    eval_secs = [45, 60, 90]
    synth_ks = [35.0, 45.0, 55.0]
    rows = []
    for mode, es, sk in itertools.product(modes, eval_secs, synth_ks):
        rows.append({"STRATEGY_MODE": mode, "eval_sec": es, "synth_k": sk})
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=float, default=7.0)
    p.add_argument("--synthetic-bars", type=int, default=0)
    p.add_argument("--csv", type=str, default="")
    p.add_argument("--train-frac", type=float, default=0.7)
    p.add_argument("--out", type=str, default=os.path.join("logs", "sweep_results.csv"))
    p.add_argument("--quick", action="store_true")
    args = p.parse_args()

    df = _load_df(args)
    if df.empty:
        print("No data.", file=sys.stderr)
        return 1

    train_df, test_df = split_train_test_by_time(df, args.train_frac)
    if train_df.empty or test_df.empty:
        print("Train/test split empty; need more data.", file=sys.stderr)
        return 1

    print(f"Rows: total={len(df)} train={len(train_df)} test={len(test_df)}")
    grid = default_grid(quick=args.quick)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    file_exists = os.path.isfile(args.out)

    with open(args.out, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SWEEP_FIELDS, extrasaction="ignore")
        if not file_exists:
            w.writeheader()

        for i, combo in enumerate(grid):
            metrics = _run_combo(train_df, test_df, combo)
            row = {
                "run_id": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{i}",
                "STRATEGY_MODE": combo["STRATEGY_MODE"],
                "eval_sec": combo["eval_sec"],
                "synth_k": combo["synth_k"],
                **metrics,
            }
            w.writerow(row)
            print(
                f"[{i+1}/{len(grid)}] {combo['STRATEGY_MODE']} eval={combo['eval_sec']} "
                f"k={combo['synth_k']} | test_pnl={metrics['test_pnl']:.2f} "
                f"test_wr={metrics['test_win_rate']:.1f}%"
            )

    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
