"""
arbitrage/arb_state.py – State persistence for arbitrage loop.

Separate from risk.py (BTC 5m bot) to avoid conflicts.
Tracks open arbitrage positions for resolution and redeem.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

ARB_STATE_FILE = "state_arbitrage.json"


def _load_state() -> dict:
    if not os.path.isfile(ARB_STATE_FILE):
        return {"open_positions": [], "closed_trades": [], "paper_balance_usdc": None}
    try:
        with open(ARB_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load %s: %s. Using fresh state.", ARB_STATE_FILE, exc)
        return {"open_positions": [], "closed_trades": [], "paper_balance_usdc": None}


def init_paper_balance(starting_usdc: float) -> float:
    """Set paper balance (e.g. start of session). Returns new balance."""
    state = _load_state()
    state["paper_balance_usdc"] = round(starting_usdc, 2)
    _save_state(state)
    return state["paper_balance_usdc"]


def get_paper_balance() -> float | None:
    """Current paper balance. None if not initialized."""
    return _load_state().get("paper_balance_usdc")


def add_paper_pnl(delta_usdc: float) -> float:
    """Add PnL to paper balance. Returns new balance."""
    state = _load_state()
    bal = state.get("paper_balance_usdc")
    if bal is None:
        return bal
    state["paper_balance_usdc"] = round(bal + delta_usdc, 2)
    _save_state(state)
    return state["paper_balance_usdc"]


def _save_state(state: dict) -> None:
    try:
        base = os.path.dirname(os.path.abspath(ARB_STATE_FILE)) or "."
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tmp", dir=base, delete=False, encoding="utf-8"
        ) as tmp:
            json.dump(state, tmp, indent=2, default=str)
            tmp_path = tmp.name
        dest = os.path.join(base, os.path.basename(ARB_STATE_FILE))
        os.replace(tmp_path, dest)
    except OSError as exc:
        logger.error("Failed to save %s: %s", ARB_STATE_FILE, exc)


def get_open_positions() -> List[dict]:
    return _load_state().get("open_positions", [])


def add_position(position: dict) -> None:
    state = _load_state()
    state.setdefault("open_positions", []).append(position)
    _save_state(state)


def remove_position(condition_id: str) -> Optional[dict]:
    state = _load_state()
    positions = state.get("open_positions", [])
    for i, p in enumerate(positions):
        if p.get("condition_id") == condition_id:
            removed = positions.pop(i)
            state["closed_trades"] = state.get("closed_trades", []) + [removed]
            _save_state(state)
            return removed
    return None


def update_position(condition_id: str, updates: dict) -> None:
    state = _load_state()
    for p in state.get("open_positions", []):
        if p.get("condition_id") == condition_id:
            p.update(updates)
            _save_state(state)
            return
