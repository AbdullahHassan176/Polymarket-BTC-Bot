"""
research/harness.py — Config snapshot / restore and train-test split for sweeps.

Uses `scripts.config` after path setup.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

_scripts = str(Path(__file__).resolve().parent.parent / "scripts")
if _scripts not in sys.path:
    from research.path_setup import ensure_scripts_on_path

    ensure_scripts_on_path()

import config  # noqa: E402
import pandas as pd  # noqa: E402


def snapshot_strategy_config() -> dict[str, Any]:
    keys = [
        "STRATEGY_MODE",
        "REVERSAL_ENABLED",
        "ENTRY_WINDOW_SECS",
        "ENTRY_MIN_SECS_REMAINING",
        "ATR_THRESHOLD",
        "MAX_ENTRY_PRICE",
        "MIN_ENTRY_PRICE",
        "CONTRARIAN_MAX_PRICE",
        "CONTRARIAN_MIN_PRICE",
        "CONTRARIAN_IBS_BOUNCE",
        "CONTRARIAN_IBS_FADE",
        "FALLBACK_ENABLED",
        "FALLBACK_IBS_LOW",
        "FALLBACK_IBS_HIGH",
        "FALLBACK_PRICE_MIN",
        "FALLBACK_PRICE_MAX",
        "FALLBACK_MIN_EMA_SPREAD_USD",
        "MIN_EMA_SPREAD_USD",
        "IBS_MIN_FOR_UP",
        "IBS_MAX_FOR_DOWN",
        "LATE_ENTRY_ENABLED",
        "LATE_WINDOW_SECS",
        "LATE_MIN_MOVE_PCT",
        "LATE_MAX_PRICE",
    ]
    out: dict[str, Any] = {}
    for k in keys:
        if hasattr(config, k):
            out[k] = copy.copy(getattr(config, k))
    return out


def apply_config_overrides(overrides: dict[str, Any]) -> None:
    for key, val in overrides.items():
        if hasattr(config, key):
            setattr(config, key, val)


def restore_config(snapshot: dict[str, Any]) -> None:
    for key, val in snapshot.items():
        setattr(config, key, val)


def split_train_test_by_time(df: pd.DataFrame, train_frac: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    if df.empty or not (0.0 < train_frac < 1.0):
        return df, pd.DataFrame()
    df = df.sort_values("ts").reset_index(drop=True)
    n = len(df)
    cut = max(int(n * train_frac), config.EMA_SLOW + 50)
    cut = min(cut, n - 100)
    if cut <= 0 or cut >= n:
        return df, pd.DataFrame()
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()
