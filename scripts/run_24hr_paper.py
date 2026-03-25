"""
Run a 24-hour paper test to gather data for strategy refinement.

Outputs three CSVs:
  - logs/trades_24h.csv         : every trade (OPEN + CLOSE rows)
  - logs/trade_entries_24h.csv  : one row per trade with full signal at entry (join to trades for outcome)
  - logs/signals_evaluated_24h.csv : every signal evaluation (action, reason, traded Y/N, risk_block_reason)

Improvements applied:
  - STRATEGY_MODE = hybrid, ENTRY_WINDOW_SECS = 180
  - CONTRARIAN_USE_MODEL_FAIR_VALUE = True (model filter for contrarian)
  - One trade per window, cheap-entry TP/SL rules, strategy_tier in logs

Run: python run_24hr_paper.py
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import execution

execution.set_trades_csv(os.path.join("logs", "trades_24h.csv"))
execution.set_trade_entries_csv(os.path.join("logs", "trade_entries_24h.csv"))
execution.set_signals_evaluated_csv(os.path.join("logs", "signals_evaluated_24h.csv"))

import config

config.STRATEGY_MODE = "hybrid"
config.ENTRY_WINDOW_SECS = 180
config.CONTRARIAN_USE_MODEL_FAIR_VALUE = True
config.CONTRARIAN_MIN_EDGE = 0.03
config.FALLBACK_TREND_ENABLED = False
config.FALLBACK_PRICE_MAX = 0.40

from bot import setup_logging, run_bot_loop, KILL_SWITCH_FILE
from risk import RiskManager, DEFAULT_STATE

logger = logging.getLogger(__name__)

TWENTY_FOUR_HR_SEC = 24 * 60 * 60  # 86400


def _reset_state() -> None:
    """Reset state.json for fresh 24hr run."""
    state = dict(DEFAULT_STATE)
    state["last_reset_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state["open_positions"] = {}
    state["daily_trades"] = 0
    state["daily_pnl_usdc"] = 0.0
    state["starting_balance_usdc"] = getattr(config, "BANKROLL_START_USDC", 50.0)
    state["last_traded_condition_id"] = ""
    risk = RiskManager()
    risk.state = state
    risk._save()
    logger.info("State reset for 24hr paper test.")


def _stop_after_24hr() -> None:
    """Create STOP_BOT.txt after 24 hours."""
    time.sleep(TWENTY_FOUR_HR_SEC)
    with open(KILL_SWITCH_FILE, "w") as f:
        f.write("")
    logger.info("24 hours elapsed. STOP_BOT.txt created. Bot will exit.")


def main() -> None:
    setup_logging()

    os.makedirs("logs", exist_ok=True)
    if os.path.isfile(KILL_SWITCH_FILE):
        os.remove(KILL_SWITCH_FILE)
    for p in [
        os.path.join("logs", "trades_24h.csv"),
        os.path.join("logs", "trade_entries_24h.csv"),
        os.path.join("logs", "signals_evaluated_24h.csv"),
    ]:
        if os.path.isfile(p):
            os.remove(p)

    _reset_state()

    logger.info("=" * 60)
    logger.info("24-HOUR PAPER TEST: fallback-lite data collection")
    logger.info("  trades_24h.csv          : every OPEN/CLOSE")
    logger.info("  trade_entries_24h.csv   : one row per trade, full signal at entry")
    logger.info("  signals_evaluated_24h.csv : every signal, traded Y/N, risk_block_reason")
    logger.info("  Overrides: CONTRARIAN_MIN_EDGE=0.03, FALLBACK_TREND_ENABLED=False, FALLBACK_PRICE_MAX=0.40")
    logger.info("  Bot log: logs/bot.log")
    logger.info("=" * 60)

    t = threading.Thread(target=_stop_after_24hr, daemon=True)
    t.start()

    run_bot_loop(override_paper=True, override_real=False, run_once=False)


if __name__ == "__main__":
    main()
