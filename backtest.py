"""
backtest.py — Walk-forward-friendly strategy backtest (root entrypoint).

Imports bot logic from `scripts/`. Polymarket YES/NO mids default to synthetic
mapping from BTC drift; optional --yes-no-csv. See docs/BACKTEST.md.

Run:
  python backtest.py --synthetic-bars 8000
  python backtest.py --days 5
"""

from __future__ import annotations

import argparse
import logging
import random
import statistics
import sys
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from research.path_setup import ensure_scripts_on_path

ensure_scripts_on_path()

import config  # noqa: E402
import data  # noqa: E402
import strategy  # noqa: E402

WINDOW_SEC = 300


def deterministic_synthetic_1m(n: int, seed: int = 42) -> pd.DataFrame:
    rng = random.Random(seed)
    price = 70_000.0
    rows = []
    base = pd.Timestamp("2024-01-01", tz="UTC")
    for i in range(n):
        change_pct = rng.gauss(0.00015, 0.0025)
        price *= 1.0 + change_pct
        cr = price * rng.uniform(0.0008, 0.004)
        o = price
        c = price * (1.0 + rng.gauss(0, 0.0008))
        h = max(o, c) + rng.uniform(0, cr * 0.5)
        l = min(o, c) - rng.uniform(0, cr * 0.5)
        rows.append({
            "ts": base + pd.Timedelta(minutes=i),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "vol": round(rng.uniform(20, 200), 4),
        })
        price = c
    return pd.DataFrame(rows)


def synth_yes_no(window_open_btc: float, current_btc: float, k: float = 45.0) -> tuple[float, float]:
    if window_open_btc <= 0:
        return 0.5, 0.5
    r = (current_btc - window_open_btc) / window_open_btc
    yes = 0.5 + k * r
    yes = max(0.04, min(0.96, yes))
    no = 1.0 - yes
    return yes, no


def _align_5m_utc_floor(ts: pd.Timestamp) -> pd.Timestamp:
    epoch = int(ts.timestamp())
    aligned = (epoch // WINDOW_SEC) * WINDOW_SEC
    return pd.Timestamp(aligned, unit="s", tz="UTC")


def _align_5m_utc_ceil(ts: pd.Timestamp) -> pd.Timestamp:
    t = _align_5m_utc_floor(ts)
    if t < ts:
        t += pd.Timedelta(seconds=WINDOW_SEC)
    return t


def _window_outcome(
    df: pd.DataFrame, window_start: pd.Timestamp
) -> tuple[Optional[float], Optional[float], bool]:
    window_end = window_start + pd.Timedelta(seconds=WINDOW_SEC)
    mask = (df["ts"] >= window_start) & (df["ts"] < window_end)
    w = df.loc[mask]
    if w.empty or len(w) < 2:
        return None, None, False
    w_open = float(w.iloc[0]["open"])
    w_close = float(w.iloc[-1]["close"])
    yes_wins = w_close >= w_open
    return w_open, w_close, yes_wins


@dataclass
class BacktestResult:
    windows: int
    trades: int
    skips: int
    wins: int
    losses: int
    total_pnl_usdc: float
    pnls: list[float]

    def summary(self) -> str:
        wr = (100.0 * self.wins / self.trades) if self.trades else 0.0
        lines = [
            f"Windows evaluated: {self.windows}",
            f"Trades:            {self.trades}  (skips: {self.skips})",
            f"Wins / losses:     {self.wins} / {self.losses}  (win rate {wr:.1f}%)",
            f"Total PnL:         ${self.total_pnl_usdc:.2f} USDC (fixed size per trade)",
        ]
        if len(self.pnls) >= 2:
            lines.append(f"Std dev PnL/trade: ${statistics.stdev(self.pnls):.3f}")
        return "\n".join(lines)


def slice_df_by_time(
    df: pd.DataFrame,
    ts_after: Optional[str] = None,
    ts_before: Optional[str] = None,
) -> pd.DataFrame:
    if df.empty or "ts" not in df.columns:
        return df
    out = df.copy()
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    if ts_after is not None:
        ta = pd.Timestamp(ts_after)
        if ta.tzinfo is None:
            ta = ta.tz_localize("UTC")
        out = out[out["ts"] >= ta]
    if ts_before is not None:
        tb = pd.Timestamp(ts_before)
        if tb.tzinfo is None:
            tb = tb.tz_localize("UTC")
        out = out[out["ts"] < tb]
    return out.sort_values("ts").reset_index(drop=True)


def _yes_no_lookup_map(yes_no_df: Optional[pd.DataFrame]) -> Optional[dict]:
    if yes_no_df is None or yes_no_df.empty:
        return None
    need = {"window_start", "yes_mid", "no_mid"}
    if not need.issubset(set(yes_no_df.columns)):
        return None
    m: dict = {}
    for _, r in yes_no_df.iterrows():
        ws = pd.to_datetime(r["window_start"], utc=True)
        m[ws] = (float(r["yes_mid"]), float(r["no_mid"]))
    return m


def run_backtest(
    df: pd.DataFrame,
    eval_sec: int = 60,
    size_usdc: float = 1.0,
    synth_k: float = 45.0,
    ts_after: Optional[str] = None,
    ts_before: Optional[str] = None,
    yes_no_df: Optional[pd.DataFrame] = None,
) -> BacktestResult:
    if df.empty or "ts" not in df.columns:
        return BacktestResult(0, 0, 0, 0, 0, 0.0, [])

    df = slice_df_by_time(df, ts_after, ts_before)
    if df.empty:
        return BacktestResult(0, 0, 0, 0, 0, 0.0, [])

    yn_map = _yes_no_lookup_map(yes_no_df)

    df = df.sort_values("ts").reset_index(drop=True)
    t_min, t_max = df["ts"].min(), df["ts"].max()
    first_ws = _align_5m_utc_ceil(t_min + pd.Timedelta(minutes=config.EMA_SLOW + 30))
    last_ws = t_max - pd.Timedelta(seconds=WINDOW_SEC + 60)
    if first_ws >= last_ws:
        return BacktestResult(0, 0, 0, 0, 0, 0.0, [])

    windows = 0
    trades = 0
    skips = 0
    wins = 0
    losses = 0
    pnls: list[float] = []
    total_pnl = 0.0

    ws = first_ws
    late_cutoff = WINDOW_SEC - getattr(config, "LATE_WINDOW_SECS", 90)

    while ws + pd.Timedelta(seconds=WINDOW_SEC + 10) <= t_max:
        windows += 1
        eval_ts = ws + pd.Timedelta(seconds=eval_sec)
        secs_remaining = max(0.0, (ws + pd.Timedelta(seconds=WINDOW_SEC) - eval_ts).total_seconds())
        hist = df[df["ts"] <= eval_ts]
        if len(hist) < config.EMA_SLOW + 5:
            ws += pd.Timedelta(seconds=WINDOW_SEC)
            continue

        hist_ind = data.compute_indicators(hist)
        indicators = data.get_latest_indicators(hist_ind)
        if indicators is None:
            ws += pd.Timedelta(seconds=WINDOW_SEC)
            continue

        w_open, _, yes_wins = _window_outcome(df, ws)
        if w_open is None:
            ws += pd.Timedelta(seconds=WINDOW_SEC)
            continue

        current_btc = float(hist_ind.iloc[-1]["close"])
        if yn_map is not None and ws in yn_map:
            yes_price, no_price = yn_map[ws]
        else:
            yes_price, no_price = synth_yes_no(w_open, current_btc, k=synth_k)

        in_late = eval_sec >= late_cutoff
        ctx = {
            "in_late_window": in_late,
            "window_start_btc": w_open,
            "current_btc": current_btc,
            "secs_remaining": secs_remaining,
        }
        action, _dbg = strategy.check_signal(indicators, yes_price, no_price, ctx)

        if action == strategy.SKIP:
            skips += 1
            ws += pd.Timedelta(seconds=WINDOW_SEC)
            continue

        trades += 1
        direction = "YES" if action == strategy.BUY_YES else "NO"
        entry = yes_price if direction == "YES" else no_price
        if entry <= 0:
            trades -= 1
            skips += 1
            ws += pd.Timedelta(seconds=WINDOW_SEC)
            continue

        tokens = round(size_usdc / entry, 6)
        won = (yes_wins and direction == "YES") or (not yes_wins and direction == "NO")
        if won:
            pnl = tokens * 1.0 - size_usdc
            wins += 1
        else:
            pnl = -size_usdc
            losses += 1

        pnls.append(pnl)
        total_pnl += pnl

        ws += pd.Timedelta(seconds=WINDOW_SEC)

    return BacktestResult(
        windows=windows,
        trades=trades,
        skips=skips,
        wins=wins,
        losses=losses,
        total_pnl_usdc=round(total_pnl, 4),
        pnls=pnls,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Polymarket bot strategy backtest (scripts/)")
    parser.add_argument("--days", type=float, default=7.0)
    parser.add_argument("--eval-sec", type=int, default=60)
    parser.add_argument("--size-usdc", type=float, default=1.0)
    parser.add_argument("--synth-k", type=float, default=45.0)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--csv", type=str, default="")
    parser.add_argument("--synthetic-bars", type=int, default=0)
    parser.add_argument("--ts-after", type=str, default="")
    parser.add_argument("--ts-before", type=str, default="")
    parser.add_argument("--yes-no-csv", type=str, default="")
    parser.add_argument(
        "--strategy-mode",
        type=str,
        default="",
        help="Override scripts.config.STRATEGY_MODE for this run (default: hybrid if config is ml_v2)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if not args.verbose:
        logging.getLogger("strategy").setLevel(logging.ERROR)

    if args.csv:
        print(f"Loading candles from {args.csv}...")
        df = pd.read_csv(args.csv)
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    elif args.synthetic_bars > 0:
        print(f"Using {args.synthetic_bars} deterministic synthetic 1m bars...")
        df = deterministic_synthetic_1m(args.synthetic_bars)
    else:
        print(f"Loading ~{args.days} days of OKX 1m candles...")
        df = data.fetch_historical_1m_candles(args.days)
    if df.empty:
        print(
            "No candle data. Try: --synthetic-bars 10000  OR  --csv path.csv  OR  check OKX/SSL.",
            file=sys.stderr,
        )
        return 1

    yn_df = None
    if args.yes_no_csv:
        yn_df = pd.read_csv(args.yes_no_csv)
        print(f"Loaded YES/NO sidecar: {args.yes_no_csv} ({len(yn_df)} rows)")

    print(f"Loaded {len(df)} rows from {df['ts'].min()} to {df['ts'].max()}")

    saved_mode = getattr(config, "STRATEGY_MODE", "hybrid")
    if args.strategy_mode:
        config.STRATEGY_MODE = args.strategy_mode
    elif saved_mode in ("ml_v2",):
        config.STRATEGY_MODE = "hybrid"
        print("Note: STRATEGY_MODE was ml_v2; using hybrid for this backtest (use --strategy-mode to pick another).")

    print(
        f"STRATEGY_MODE={config.STRATEGY_MODE} eval_sec={args.eval_sec} "
        f"size_usdc={args.size_usdc} synth_k={args.synth_k}"
    )

    try:
        result = run_backtest(
            df,
            eval_sec=args.eval_sec,
            size_usdc=args.size_usdc,
            synth_k=args.synth_k,
            ts_after=args.ts_after or None,
            ts_before=args.ts_before or None,
            yes_no_df=yn_df,
        )
    finally:
        config.STRATEGY_MODE = saved_mode

    print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
