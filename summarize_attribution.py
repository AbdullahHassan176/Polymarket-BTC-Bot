"""
summarize_attribution.py — Aggregate CLOSE rows from logs/trades*.csv

  python summarize_attribution.py --glob "logs/trades*.csv"
"""

from __future__ import annotations

import argparse
import glob
import os
from datetime import datetime, timezone

import pandas as pd


def _entry_bucket(price: float) -> str:
    if price < 0.35:
        return "0.00-0.35"
    if price < 0.50:
        return "0.35-0.50"
    if price < 0.65:
        return "0.50-0.65"
    return "0.65-1.00"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--glob", default="logs/trades*.csv")
    p.add_argument("--out", default=os.path.join("logs", "attribution_summary.txt"))
    args = p.parse_args()

    paths = sorted(glob.glob(args.glob))
    if not paths:
        print("No files matched:", args.glob)
        return 1

    frames = []
    for path in paths:
        try:
            frames.append(pd.read_csv(path))
        except Exception as exc:
            print("Skip", path, exc)

    if not frames:
        return 1

    df = pd.concat(frames, ignore_index=True)
    df = df[df.get("action") == "CLOSE"].copy()
    if df.empty:
        print("No CLOSE rows.")
        return 0

    df["pnl_usdc"] = pd.to_numeric(df.get("pnl_usdc"), errors="coerce").fillna(0.0)
    df["entry_price"] = pd.to_numeric(df.get("entry_price"), errors="coerce").fillna(0.0)
    df["signal_reason"] = df.get("signal_reason", "").fillna("").astype(str).str.slice(0, 120)
    df["outcome"] = df.get("outcome", "").fillna("").astype(str)

    def utc_hour(ts: str) -> str:
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t.astimezone(timezone.utc).strftime("%H:00 UTC")
        except (ValueError, TypeError):
            return "unknown"

    df["hour_utc"] = df["timestamp"].map(utc_hour)
    df["entry_bucket"] = df["entry_price"].map(_entry_bucket)

    lines = [
        f"Attribution report (UTC) - {datetime.now(timezone.utc).isoformat()}",
        f"Sources: {', '.join(paths)}",
        f"Close rows: {len(df)}",
        "",
        "=== By signal_reason (truncated) ===",
    ]

    g = df.groupby("signal_reason", dropna=False)["pnl_usdc"].agg(["count", "sum", "mean"])
    g = g.sort_values("sum", ascending=False)
    for reason, row in g.head(40).iterrows():
        lines.append(
            f"  {reason[:100]!r}: n={int(row['count'])} sum=${row['sum']:.2f} avg=${row['mean']:.3f}"
        )

    lines += ["", "=== By outcome ==="]
    for out, sub in df.groupby("outcome"):
        lines.append(f"  {out}: n={len(sub)} sum=${sub['pnl_usdc'].sum():.2f}")

    lines += ["", "=== By hour (UTC) ==="]
    for h, sub in sorted(df.groupby("hour_utc"), key=lambda x: x[0]):
        lines.append(f"  {h}: n={len(sub)} sum=${sub['pnl_usdc'].sum():.2f}")

    lines += ["", "=== By entry_price bucket ==="]
    for b in sorted(df["entry_bucket"].unique()):
        sub = df[df["entry_bucket"] == b]
        lines.append(f"  {b}: n={len(sub)} sum=${sub['pnl_usdc'].sum():.2f}")

    lines += ["", f"TOTAL PnL (close rows): ${df['pnl_usdc'].sum():.2f}"]

    text = "\n".join(lines)
    print(text)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
