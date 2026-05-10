# Rigorous evaluation (paper + research)

There is **no** recipe—Karpathy’s or anyone else’s—that **guarantees** profit on Polymarket (or any market). Short-horizon binaries are high-variance; past backtest or paper PnL does not obligate the future.

What *is* borrowed from strong ML/engineering practice (train skeptically, measure honestly):

1. **Separate concerns** — Treat in-sample tuning as hypothesis generation; confirm with **walk-forward / held-out periods** (`backtest.py`, `run_param_sweep.py`, `docs/BACKTEST.md`).
2. **Paper mirrors live rules** — Same `scripts/config.py` signals and sizes you would use live; `REAL_TRADING=False` and `python scripts/bot.py --paper` (or `start_paper_btc.bat`) so fills are simulated, not guaranteed to match live liquidity.
3. **One change at a time** — When comparing runs, change one knob or you cannot attribute results.
4. **Report failure** — If paper or walk-forward is negative or flat, the honest conclusion is “no edge yet,” not “run it live anyway.”

**Paper trading does not pay real USDC**; it only estimates strategy behavior. Going live still risks full loss.
