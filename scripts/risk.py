"""
risk.py  -  Risk management and state persistence for the Polymarket bot.

Tracks daily USDC P&L, trade counts, and open positions in state.json.
Uses atomic writes so a crash never corrupts the state file.

Supports multiple concurrent positions (one per market) so the bot can enter
new windows while waiting for previous trades to resolve.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

import config

logger = logging.getLogger(__name__)

# Resolve relative to project root
_script_dir = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_script_dir) if os.path.basename(_script_dir) == "scripts" else os.getcwd()
_asset = getattr(__import__("config", fromlist=["TRADING_ASSET"]), "TRADING_ASSET", "BTC")
STATE_FILE = os.path.join(_ROOT, "state.json" if _asset == "BTC" else f"state_{_asset}.json")

DEFAULT_STATE: dict = {
    "open_positions":            {},     # {condition_id: position dict} - supports multiple
    "daily_trades":              0,      # Bets placed today
    "daily_pnl_usdc":            0.0,    # Running P&L in USDC today (can be negative)
    "cumulative_pnl_usdc":       0.0,    # All-time P&L (for compounding; not reset at midnight)
    "starting_balance_usdc":     0.0,    # Starting capital at session start (for tracking)
    "last_traded_condition_id":  "",     # One trade per window: skip re-entry in same window
    "last_reset_date":           "",     # UTC date of last daily counter reset
    "btc_spot":                  0.0,    # Latest BTC spot price (for dashboard)
    "bot_running":               False,  # True while bot loop is active
    "last_signal":               {},     # Latest signal debug info
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
            state["starting_balance_usdc"] = getattr(config, "BANKROLL_START_USDC", 50.0)
            self._save(state)
            return state
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            state = {**DEFAULT_STATE, **loaded}
            if state.get("starting_balance_usdc", 0) == 0:
                state["starting_balance_usdc"] = getattr(config, "BANKROLL_START_USDC", 50.0)
            # Migrate legacy open_position -> open_positions
            legacy = state.pop("open_position", None)
            if "open_positions" not in state or not isinstance(state.get("open_positions"), dict):
                state["open_positions"] = {}
            if legacy and isinstance(legacy, dict) and legacy.get("open", True):
                cid = legacy.get("condition_id", "")
                if cid:
                    state["open_positions"][cid] = legacy
                    logger.info("Migrated legacy open_position -> open_positions")
            return state
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

    def can_trade(
        self, market: Optional[dict] = None, is_add_slice: bool = False
    ) -> tuple:
        """
        Check all risk conditions before entering a new bet.

        Args:
            market: Optional active market dict. If provided and ONE_TRADE_PER_WINDOW,
                    blocks re-entry in the same 5-min window.

        Returns:
            (True, "ok") if allowed.
            (False, reason_str) if blocked.
        """
        positions = self._get_open_positions_dict()
        max_concurrent = getattr(config, "MAX_CONCURRENT_POSITIONS", 3)

        if market:
            cid = market.get("condition_id", "")
            # Already have open position in this market (pyramid add_slice is allowed)
            if cid and cid in positions and not is_add_slice:
                return False, "already have an open position in this window"

            # One trade per window: do not re-enter same 5-min market after TP/SL
            if getattr(config, "ONE_TRADE_PER_WINDOW", False) and cid:
                if self.state.get("last_traded_condition_id") == cid:
                    return False, "already traded this window (one trade per window)"

        if len(positions) >= max_concurrent:
            return False, (
                f"max concurrent positions ({len(positions)}/{max_concurrent})"
            )

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

    def get_trade_size_usdc(self, half_kelly: float = None) -> float:
        """
        Return USDC amount to risk on the next trade.
        If COMPOUNDING_ENABLED: size = equity * RISK_PCT_PER_TRADE,
        clamped to [MIN_TRADE_USDC, RISK_PER_TRADE_USDC]. Otherwise use RISK_PER_TRADE_USDC.
        For non-BTC assets, cap can be lower if RISK_PER_TRADE_ALT_USDC is set.

        If half_kelly is provided (ml_v2 strategy), Kelly sizing overrides the fixed pct:
        size = equity * min(half_kelly, ML_V2_KELLY_MAX_FRACTION).
        The dollar cap becomes equity * ML_V2_KELLY_MAX_FRACTION (not RISK_PER_TRADE_USDC).
        """
        bankroll = getattr(config, "BANKROLL_START_USDC", 50.0)
        cumulative = self.state.get("cumulative_pnl_usdc", 0.0)
        equity = bankroll + cumulative
        min_size = getattr(config, "MIN_TRADE_USDC", 1.0)

        if half_kelly is not None and half_kelly > 0:
            kelly_max = getattr(config, "ML_V2_KELLY_MAX_FRACTION", 0.10)
            frac = min(half_kelly, kelly_max)
            size = equity * frac
            size = max(min_size, size)
            return round(size, 2)

        max_cap = config.RISK_PER_TRADE_USDC
        alt_cap = getattr(config, "RISK_PER_TRADE_ALT_USDC", 0.0)
        if alt_cap > 0 and getattr(config, "TRADING_ASSET", "BTC") != "BTC":
            max_cap = min(max_cap, alt_cap)
        if not getattr(config, "COMPOUNDING_ENABLED", False):
            return round(max_cap, 2)
        pct = getattr(config, "RISK_PCT_PER_TRADE", 0.10)
        size = equity * pct
        size = max(min_size, min(max_cap, size))
        return round(size, 2)

    # -----------------------------------------------------------------------
    # STATE UPDATES
    # -----------------------------------------------------------------------

    def record_trade_opened(self, position: dict) -> None:
        """Store an open position and increment daily trade counter."""
        cid = position.get("condition_id", "")
        if cid:
            self._ensure_open_positions()
            self.state["open_positions"][cid] = position
        self.state["daily_trades"] = self.state.get("daily_trades", 0) + 1
        if getattr(config, "ONE_TRADE_PER_WINDOW", False):
            self.state["last_traded_condition_id"] = cid
        self._save()
        starting = self.state.get("starting_balance_usdc", 0) or config.BANKROLL_START_USDC
        cumulative = self.state.get("cumulative_pnl_usdc", 0.0)
        current = starting + cumulative
        logger.info(
            "Bet opened. Daily trades: %d/%d | Current balance: $%.2f (started $%.2f, overall profit: $%.2f)",
            self.state["daily_trades"], config.MAX_TRADES_PER_DAY,
            current, starting, cumulative,
        )

    def record_partial_pnl(
        self, pnl_usdc: float, mode: Optional[str] = None
    ) -> None:
        """Add partial PnL (tiered TP) without closing position.
        Only updates daily/cumulative PnL when mode!='PAPER'. PAPER trades are excluded."""
        if (mode or "").strip().upper() != "PAPER":
            self.state["daily_pnl_usdc"] = round(
                self.state.get("daily_pnl_usdc", 0.0) + pnl_usdc, 4
            )
            self.state["cumulative_pnl_usdc"] = round(
                self.state.get("cumulative_pnl_usdc", 0.0) + pnl_usdc, 4
            )
        self._save()

    def record_trade_closed(
        self,
        pnl_usdc: float,
        condition_id: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> None:
        """Clear open position and update daily + cumulative P&L.
        Only updates daily/cumulative PnL when mode!='PAPER'. PAPER trades are excluded."""
        if condition_id:
            self._ensure_open_positions()
            self.state["open_positions"].pop(condition_id, None)
        if (mode or "").strip().upper() != "PAPER":
            self.state["daily_pnl_usdc"] = round(
                self.state.get("daily_pnl_usdc", 0.0) + pnl_usdc, 4
            )
            self.state["cumulative_pnl_usdc"] = round(
                self.state.get("cumulative_pnl_usdc", 0.0) + pnl_usdc, 4
            )
        self._save()
        starting = self.state.get("starting_balance_usdc", 0) or config.BANKROLL_START_USDC
        cumulative = self.state["cumulative_pnl_usdc"]
        current = starting + cumulative
        logger.info(
            "Bet closed. Trade PnL: $%.2f | Daily PnL: $%.2f | Overall profit: $%.2f | Current balance: $%.2f",
            pnl_usdc, self.state["daily_pnl_usdc"], cumulative, current,
        )

    def update_open_position(self, position: dict) -> None:
        """Overwrite the open position (e.g. after tiered TP partial sell)."""
        cid = position.get("condition_id", "")
        if cid:
            self._ensure_open_positions()
            self.state["open_positions"][cid] = position
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
    # HELPERS (internal)
    # -----------------------------------------------------------------------

    def _ensure_open_positions(self) -> None:
        """Ensure open_positions exists and is a dict."""
        if "open_positions" not in self.state or not isinstance(
            self.state["open_positions"], dict
        ):
            self.state["open_positions"] = {}

    def _get_open_positions_dict(self) -> Dict[str, dict]:
        """Return dict of open positions keyed by condition_id (excludes open==False)."""
        self._ensure_open_positions()
        raw = self.state["open_positions"]
        return {
            cid: pos
            for cid, pos in raw.items()
            if isinstance(pos, dict) and pos.get("open", True) is not False
        }

    # -----------------------------------------------------------------------
    # ACCESSORS
    # -----------------------------------------------------------------------

    def get_open_positions(self) -> List[dict]:
        """Return list of all open position dicts."""
        return list(self._get_open_positions_dict().values())

    def get_open_position(self, condition_id: Optional[str] = None) -> Optional[dict]:
        """
        Return one open position. If condition_id given, return that market's position.
        If condition_id is None, return the first open position (for backward compat).
        """
        positions = self._get_open_positions_dict()
        if condition_id:
            return positions.get(condition_id)
        return next(iter(positions.values()), None) if positions else None

    def has_position_in_market(self, condition_id: str) -> bool:
        """Return True if we have an open position in the given market."""
        return condition_id in self._get_open_positions_dict()

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
