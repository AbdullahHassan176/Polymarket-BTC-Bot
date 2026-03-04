"""
risk.py  -  Risk management and state persistence for the Polymarket bot.

Tracks daily USDC P&L, trade counts, and open positions in state.json.
Uses atomic writes so a crash never corrupts the state file.

Differences from the OKX bot:
  - Risk is in USDC (not BTC-converted USD)
  - Max open positions is 1 (one 5-minute window at a time)
  - "Profit/loss" is binary: WIN = tokens * $1.00 - cost, LOSS = -cost
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

STATE_FILE = "state.json"

DEFAULT_STATE: dict = {
    "open_position":       None,    # None or a position dict
    "daily_trades":        0,      # Bets placed today
    "daily_pnl_usdc":      0.0,    # Running P&L in USDC today (can be negative)
    "cumulative_pnl_usdc":  0.0,    # All-time P&L (for compounding; not reset at midnight)
    "last_reset_date":     "",     # UTC date of last daily counter reset
    "btc_spot":            0.0,    # Latest BTC spot price (for dashboard)
    "bot_running":         False,  # True while bot loop is active
    "last_signal":         {},     # Latest signal debug info
}


class RiskManager:
    """
    Central risk and state class for the Polymarket bot.

    All state mutations go through this class to keep state.json consistent.
    """

    def __init__(self, state_file: str = None) -> None:
        self._state_file = state_file or STATE_FILE
        self.state = self._load()

    # -----------------------------------------------------------------------
    # STATE PERSISTENCE
    # -----------------------------------------------------------------------

    def _load(self) -> dict:
        """Load state from disk, or initialise defaults if file doesn't exist."""
        if not os.path.isfile(self._state_file):
            logger.info("No %s found - starting fresh.", self._state_file)
            state = dict(DEFAULT_STATE)
            state["last_reset_date"] = _today_utc()
            self._save(state)
            return state
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            return {**DEFAULT_STATE, **loaded}
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load %s: %s - using defaults.", self._state_file, exc)
            return dict(DEFAULT_STATE)

    def _save(self, state: dict = None) -> None:
        """Atomically write state to disk (temp file + rename)."""
        state = state if state is not None else self.state
        try:
            dir_name = os.path.dirname(os.path.abspath(self._state_file)) or "."
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".tmp", dir=dir_name, delete=False, encoding="utf-8"
            ) as tmp:
                json.dump(state, tmp, indent=2, default=str)
                tmp_path = tmp.name
            os.replace(tmp_path, self._state_file)
        except OSError as exc:
            logger.error("Failed to save %s: %s", self._state_file, exc)

    def reload(self) -> None:
        """Reload state from disk - call at the top of each bot loop iteration."""
        self.state = self._load()

    # -----------------------------------------------------------------------
    # DAILY RESET
    # -----------------------------------------------------------------------

    def reset_if_new_day(self) -> None:
        """Reset daily counters at UTC midnight."""
        today = _today_utc()
        if self.state.get("last_reset_date") != today:
            logger.info("New trading day (%s). Resetting daily counters.", today)
            self.state["daily_trades"]    = 0
            self.state["daily_pnl_usdc"]  = 0.0
            self.state["last_reset_date"] = today
            self._save()

    # -----------------------------------------------------------------------
    # TRADE GATE
    # -----------------------------------------------------------------------

    def can_trade(self) -> tuple:
        """
        Check all risk conditions before entering a new bet.

        Returns:
            (True, "ok") if allowed.
            (False, reason_str) if blocked.
        """
        # No two open positions simultaneously.
        if self.state.get("open_position") is not None:
            return False, "already have an open position (waiting for resolution)"

        # Daily trade count.
        trades_today = self.state.get("daily_trades", 0)
        if trades_today >= config.MAX_TRADES_PER_DAY:
            return False, (
                f"daily trade limit reached ({trades_today}/{config.MAX_TRADES_PER_DAY})"
            )

        # Daily loss limit.
        pnl_today = self.state.get("daily_pnl_usdc", 0.0)
        if pnl_today <= -config.MAX_DAILY_LOSS_USDC:
            return False, (
                f"daily loss limit hit (${pnl_today:.2f} loss, limit=${config.MAX_DAILY_LOSS_USDC})"
            )

        return True, "ok"

    # -----------------------------------------------------------------------
    # POSITION SIZING (compounding)
    # -----------------------------------------------------------------------

    def get_trade_size_usdc(self) -> float:
        """
        Return USDC amount to risk on the next trade.
        If COMPOUNDING_ENABLED: size = (BANKROLL_START + cumulative_pnl) * RISK_PCT_PER_TRADE,
        clamped to [MIN_TRADE_USDC, RISK_PER_TRADE_USDC]. Otherwise use RISK_PER_TRADE_USDC.
        """
        if not getattr(config, "COMPOUNDING_ENABLED", False):
            return round(config.RISK_PER_TRADE_USDC, 2)
        bankroll = getattr(config, "BANKROLL_START_USDC", 50.0)
        cumulative = self.state.get("cumulative_pnl_usdc", 0.0)
        equity = bankroll + cumulative
        pct = getattr(config, "RISK_PCT_PER_TRADE", 0.10)
        min_size = getattr(config, "MIN_TRADE_USDC", 1.0)
        max_size = config.RISK_PER_TRADE_USDC
        size = equity * pct
        size = max(min_size, min(max_size, size))
        return round(size, 2)

    # -----------------------------------------------------------------------
    # STATE UPDATES
    # -----------------------------------------------------------------------

    def record_trade_opened(self, position: dict) -> None:
        """Store an open position and increment daily trade counter."""
        self.state["open_position"] = position
        self.state["daily_trades"]  = self.state.get("daily_trades", 0) + 1
        self._save()
        logger.info(
            "Bet opened. Daily trades: %d/%d",
            self.state["daily_trades"], config.MAX_TRADES_PER_DAY,
        )

    def record_trade_closed(self, pnl_usdc: float) -> None:
        """Clear open position and update daily + cumulative P&L."""
        self.state["open_position"] = None
        self.state["daily_pnl_usdc"] = round(
            self.state.get("daily_pnl_usdc", 0.0) + pnl_usdc, 4
        )
        self.state["cumulative_pnl_usdc"] = round(
            self.state.get("cumulative_pnl_usdc", 0.0) + pnl_usdc, 4
        )
        self._save()
        logger.info(
            "Bet closed. Daily P&L: $%.4f USDC | Cumulative: $%.4f",
            self.state["daily_pnl_usdc"],
            self.state["cumulative_pnl_usdc"],
        )

    def update_open_position(self, position: dict) -> None:
        """Overwrite the open position (e.g. after updating PnL estimate)."""
        self.state["open_position"] = position
        self._save()

    def update_btc_spot(self, price: float) -> None:
        """Store latest BTC spot price for dashboard display."""
        self.state["btc_spot"] = round(price, 2)
        self._save()

    def update_last_signal(self, signal_dict: dict) -> None:
        """Store latest signal debug info for dashboard display."""
        self.state["last_signal"] = signal_dict
        self._save()

    def set_bot_running(self, running: bool) -> None:
        """Update the bot_running flag so the dashboard knows bot is active."""
        self.state["bot_running"] = running
        self._save()

    # -----------------------------------------------------------------------
    # ACCESSORS
    # -----------------------------------------------------------------------

    def get_open_position(self) -> Optional[dict]:
        return self.state.get("open_position")

    def get_daily_pnl(self) -> float:
        return self.state.get("daily_pnl_usdc", 0.0)

    def get_daily_trades(self) -> int:
        return self.state.get("daily_trades", 0)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _today_utc() -> str:
    """Return today's UTC date as 'YYYY-MM-DD'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
