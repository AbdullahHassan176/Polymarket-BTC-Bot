#!/usr/bin/env python3
"""
Paper trading with per-second price path logging for all assets.

Set env:
  PAPER_RUN_DIR   = logs/paper_run_<timestamp> (created by launch script)
  LOG_PRICE_PATH  = 1
  TRADING_ASSET   = BTC | ETH | XRP | SOL | DOGE

This script forces paper mode, 1s loop, and price path + signals + bets to PAPER_RUN_DIR.
Run via: scripts/launch/run_paper_all_price_logging.ps1
"""
from __future__ import annotations

import os
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_script_dir) if os.path.basename(_script_dir) == "scripts" else os.getcwd()
if _root not in sys.path:
    sys.path.insert(0, _root)

os.chdir(_root)

import config
import execution
import bot

# Paper only
config.REAL_TRADING = False
config.LOOP_INTERVAL_SECONDS = 1
config.LOG_PRICE_PATH = True

paper_run_dir = os.getenv("PAPER_RUN_DIR", "").strip()
if paper_run_dir:
    paper_run_dir = os.path.normpath(paper_run_dir)
    config.PAPER_RUN_DIR = paper_run_dir
    execution.set_paper_run_dir(paper_run_dir)

asset = os.getenv("TRADING_ASSET", "BTC").strip() or "BTC"
config.TRADING_ASSET = asset

if __name__ == "__main__":
    bot.run_bot_loop(override_paper=True, interval=1)
