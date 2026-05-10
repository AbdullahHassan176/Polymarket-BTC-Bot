"""
run_research_agent.py — JSON overrides + walk-forward; appends logs/experiments.csv

  python run_research_agent.py --params research/research_params.example.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

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

EXPERIMENT_FIELDS = [
    "timestamp",
    "params_file",
    "config_overrides_json",
    "train_trades",
    "train_pnl",
    "train_win_rate",
    "test_trades",
    "test_pnl",
    "test_win_rate",
]


def _win_rate(trades: int, wins: int) -> float:
    if trades <= 0:
        return 0.0
    return round(100.0 * wins / trades, 2)


def _run_walk_forward(df, bt: dict, overrides: dict) -> dict:
    snap = snapshot_strategy_config()
    try:
        apply_config_overrides(overrides)
        train_df, test_df = split_train_test_by_time(df, float(bt.get("train_frac", 0.7)))
        if train_df.empty or test_df.empty:
            return {"error": "empty_split"}

        ev = int(bt.get("eval_sec", 60))
        sk = float(bt.get("synth_k", 45.0))
        sz = float(bt.get("size_usdc", 1.0))

        tr = backtest.run_backtest(train_df, eval_sec=ev, size_usdc=sz, synth_k=sk)
        te = backtest.run_backtest(test_df, eval_sec=ev, size_usdc=sz, synth_k=sk)
        return {
            "train_trades": tr.trades,
            "train_pnl": tr.total_pnl_usdc,
            "train_win_rate": _win_rate(tr.trades, tr.wins),
            "test_trades": te.trades,
            "test_pnl": te.total_pnl_usdc,
            "test_win_rate": _win_rate(te.trades, te.wins),
        }
    finally:
        restore_config(snap)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--params", required=True)
    p.add_argument("--out", default=os.path.join("logs", "experiments.csv"))
    args = p.parse_args()

    with open(args.params, encoding="utf-8") as f:
        spec = json.load(f)

    overrides = spec.get("config_overrides") or {}
    bt = spec.get("backtest") or {}

    if bt.get("synthetic_bars", 0) > 0:
        df = backtest.deterministic_synthetic_1m(int(bt["synthetic_bars"]))
    elif bt.get("csv"):
        df = pd.read_csv(bt["csv"])
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    else:
        days = float(bt.get("days", 7))
        df = data.fetch_historical_1m_candles(days)

    if df.empty:
        print("No candle data.", file=sys.stderr)
        return 1

    metrics = _run_walk_forward(df, bt, overrides)
    if metrics.get("error"):
        print(metrics["error"], file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    exists = os.path.isfile(args.out)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "params_file": os.path.abspath(args.params),
        "config_overrides_json": json.dumps(overrides, sort_keys=True),
        **metrics,
    }
    with open(args.out, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EXPERIMENT_FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow(row)

    print(json.dumps(row, indent=2))
    print(f"Appended: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
