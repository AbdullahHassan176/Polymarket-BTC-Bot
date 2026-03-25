"""
Track per-window min/max YES/NO prices and compute dynamic lowest tier from last N windows.

Used when REVERSAL_USE_DYNAMIC_LOWEST is True: the entry "lowest" tier is derived from
the actual price paths of the last N (e.g. 3) 5-minute windows instead of a fixed config.

When a window ends, a row is appended to logs/reversal_window_paths.csv (date_utc, condition_id,
asset, min_yes, min_no, max_yes, max_no) so you can analyze how often the market had a full
reversal (e.g. min <= 15c and max >= 50c) per day. Use analyze_reversal_frequency.py --paths.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import Optional

import config

_script_dir = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_script_dir) if os.path.basename(_script_dir) == "scripts" else os.getcwd()
MINS_FILE = os.path.join(_ROOT, "logs", "reversal_window_mins.json")
PATHS_CSV = os.path.join(_ROOT, "logs", "reversal_window_paths.csv")
PATH_COLUMNS = ("date_utc", "condition_id", "asset", "min_yes", "min_no", "max_yes", "max_no")


def _load_all() -> dict:
    """Load full state from disk. Keys: asset -> { last_3, current }."""
    if not os.path.isfile(MINS_FILE):
        return {}
    try:
        with open(MINS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_all(data: dict) -> None:
    os.makedirs(os.path.dirname(MINS_FILE), exist_ok=True)
    with open(MINS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _append_window_path(asset: str, condition_id: str, min_yes: float, min_no: float, max_yes: float, max_no: float) -> None:
    """Append one row per completed window for reversal frequency analysis."""
    try:
        os.makedirs(os.path.dirname(PATHS_CSV), exist_ok=True)
        file_exists = os.path.isfile(PATHS_CSV)
        with open(PATHS_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=PATH_COLUMNS, extrasaction="ignore")
            if not file_exists:
                w.writeheader()
            w.writerow({
                "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "condition_id": condition_id,
                "asset": asset,
                "min_yes": round(min_yes, 4),
                "min_no": round(min_no, 4),
                "max_yes": round(max_yes, 4),
                "max_no": round(max_no, 4),
            })
    except OSError:
        pass


def _round_down_to_tier(value: float, tiers: list[float]) -> float:
    """Largest tier such that tier <= value. If value < min(tiers), return min(tiers)."""
    if not tiers:
        return value
    allowed = sorted(tiers)
    for t in reversed(allowed):
        if t <= value:
            return t
    return allowed[0]


def update_window_mins(asset: str, condition_id: str, yes_price: float, no_price: float) -> Optional[float]:
    """
    Update min YES/NO for the current window; when market switches, push to last_3.
    Returns the dynamic lowest tier (rounded down from observed mins) or None if not enough data.

    - current: { cid, min_yes, min_no } for the window we're in
    - last_3: list of [min_yes, min_no] for the last N completed windows (newest last)
    """
    n_windows = getattr(config, "REVERSAL_DYNAMIC_LOWEST_WINDOWS", 3)
    tiers = getattr(config, "REVERSAL_DYNAMIC_LOWEST_TIERS", [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20])
    min_entry = getattr(config, "REVERSAL_MIN_ENTRY_PRICE", 0.08)

    data = _load_all()
    entry = data.setdefault(asset, {"last_3": [], "current": None})

    current = entry.get("current")
    last_3 = list(entry.get("last_3") or [])

    if current is not None and current.get("cid") != condition_id:
        # New window: push previous window's mins to last_3 and log path for frequency analysis
        prev_min_yes = current["min_yes"]
        prev_min_no = current["min_no"]
        prev_max_yes = current.get("max_yes", prev_min_yes)
        prev_max_no = current.get("max_no", prev_min_no)
        _append_window_path(asset, current["cid"], prev_min_yes, prev_min_no, prev_max_yes, prev_max_no)
        last_3.append([prev_min_yes, prev_min_no])
        if len(last_3) > n_windows:
            last_3 = last_3[-n_windows:]
        entry["last_3"] = last_3
        current = None

    if current is None:
        current = {
            "cid": condition_id,
            "min_yes": yes_price,
            "min_no": no_price,
            "max_yes": yes_price,
            "max_no": no_price,
        }
    else:
        current["min_yes"] = min(current["min_yes"], yes_price)
        current["min_no"] = min(current["min_no"], no_price)
        current["max_yes"] = max(current.get("max_yes", yes_price), yes_price)
        current["max_no"] = max(current.get("max_no", no_price), no_price)
    entry["current"] = current

    data[asset] = entry
    _save_all(data)

    # Compute dynamic lowest from last_3 (use at least 1 window if we have it)
    if not last_3:
        return None
    use = last_3[-n_windows:] if len(last_3) >= n_windows else last_3
    flat = []
    for pair in use:
        flat.extend(pair)
    dynamic_low = min(flat)
    tier = _round_down_to_tier(dynamic_low, tiers)
    # Cap by min_entry so we don't go below allowed floor
    dynamic_lowest_tier = max(min_entry, tier)
    return round(dynamic_lowest_tier, 2)
