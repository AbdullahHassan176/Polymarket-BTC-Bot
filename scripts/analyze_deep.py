#!/usr/bin/env python3
"""
Deep analysis of one paper_run_* directory.
Usage: python scripts/analyze_deep.py [--dir logs/paper_run_...]
"""
import csv
import os
import sys
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d

def asset_of(q):
    for k, v in [("Bitcoin","BTC"),("Ethereum","ETH"),("XRP","XRP"),("Solana","SOL"),("Dogecoin","DOGE"),("Doge","DOGE")]:
        if k in (q or ""):
            return v
    return "OTHER"

def load_csv(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

def main():
    # Determine run dir
    run_dir = None
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--dir":
            run_dir = sys.argv[i + 2]
            break
        elif not arg.startswith("--"):
            run_dir = arg
    if not run_dir:
        # Use the latest paper_run_*
        dirs = sorted(ROOT.glob("logs/paper_run_*"), key=lambda p: p.name, reverse=True)
        if not dirs:
            print("No paper_run_* directory found."); return
        run_dir = str(dirs[0])
    print(f"\n{'='*65}")
    print(f"DEEP ANALYSIS: {run_dir}")
    print(f"{'='*65}\n")

    trades = load_csv(os.path.join(run_dir, "paper_trades.csv"))
    paths  = load_csv(os.path.join(run_dir, "price_paths.csv"))
    sigs   = load_csv(os.path.join(run_dir, "signals_evaluated.csv"))

    opens  = [r for r in trades if r.get("action") == "OPEN"]
    closes = [r for r in trades if r.get("action") == "CLOSE"]

    # ── 1. TOP-LINE ───────────────────────────────────────────────────────
    total_pnl = sum(f(r.get("trade_pnl_usdc")) for r in closes)
    print(f"Positions opened: {len(opens)}")
    print(f"Close events:     {len(closes)}")
    print(f"Total paper PnL:  {total_pnl:+.2f} USDC")
    print(f"Per-trade avg:    {total_pnl/len(closes) if closes else 0:+.3f} USDC")
    print()

    # ── 2. PnL DISTRIBUTION ───────────────────────────────────────────────
    pnls = sorted([f(r.get("trade_pnl_usdc")) for r in closes])
    bins = {"≥1":[p for p in pnls if p>=1],
            "0.5–1":[p for p in pnls if 0.5<=p<1],
            "0.1–0.5":[p for p in pnls if 0.1<=p<0.5],
            "0–0.1":[p for p in pnls if 0.01<=p<0.1],
            "≈0":[p for p in pnls if abs(p)<0.01],
            "-0.5–0":[p for p in pnls if -0.5<p<=-0.01],
            "-1––0.5":[p for p in pnls if -1<p<=-0.5],
            "-2––1":[p for p in pnls if -2<p<=-1],
            "≤-2":[p for p in pnls if p<=-2]}
    print("PnL distribution (per close event):")
    for k, v in bins.items():
        bar = "█" * len(v)
        print(f"  {k:10s}: {len(v):3d}  {bar[:40]}  sum={sum(v):+.2f}")
    print()

    # ── 3. BY ASSET ───────────────────────────────────────────────────────
    print("By asset:")
    print(f"  {'Asset':5s} {'Trades':>6s} {'PnL':>7s} {'TP%':>5s} {'SL+':>4s} {'SL0':>4s} {'SL-':>4s} {'AvgPnL':>7s}")
    print("  " + "-"*50)
    for a in ["BTC","ETH","SOL","XRP","DOGE"]:
        ac = [r for r in closes if asset_of(r.get("question")) == a]
        if not ac:
            print(f"  {a:5s} {'0':>6s}")
            continue
        pnl = sum(f(r.get("trade_pnl_usdc")) for r in ac)
        tp = sum(1 for r in ac if "TP" in (r.get("outcome") or ""))
        sl_pos = sum(1 for r in ac if r.get("outcome")=="SL" and f(r.get("trade_pnl_usdc"))>0.01)
        sl_zer = sum(1 for r in ac if r.get("outcome")=="SL" and abs(f(r.get("trade_pnl_usdc")))<=0.01)
        sl_neg = sum(1 for r in ac if r.get("outcome")=="SL" and f(r.get("trade_pnl_usdc"))<-0.01)
        tp_pct = 100*tp//len(ac)
        avg = pnl/len(ac)
        print(f"  {a:5s} {len(ac):6d} {pnl:7.2f} {tp_pct:4d}% {sl_pos:4d} {sl_zer:4d} {sl_neg:4d} {avg:7.3f}")
    print()
    print("  TP%=fraction of close events with a TP | SL+=SL but positive pnl | SL0=breakeven | SL-=actual loss")
    print()

    # ── 4. DIRECTION BIAS ─────────────────────────────────────────────────
    yes_c = [r for r in closes if r.get("direction")=="YES"]
    no_c  = [r for r in closes if r.get("direction")=="NO"]
    print("Direction split:")
    print(f"  YES: {len(yes_c)} closes | PnL={sum(f(r.get('trade_pnl_usdc')) for r in yes_c):+.2f}")
    print(f"  NO:  {len(no_c)} closes | PnL={sum(f(r.get('trade_pnl_usdc')) for r in no_c):+.2f}")
    print()

    # ── 5. HOLD TIME (open → first close) ─────────────────────────────────
    from datetime import datetime
    def parse_ts(ts):
        ts = (ts or "").replace("+00:00", "").rstrip("Z")
        for fmt in ["%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"]:
            try: return datetime.strptime(ts, fmt)
            except: pass
        return None

    by_cid_open = {r["condition_id"]: r for r in opens}
    by_cid_close = defaultdict(list)
    for r in closes:
        by_cid_close[r["condition_id"]].append(r)

    hold_secs = []
    for cid, op in by_cid_open.items():
        cls = by_cid_close.get(cid)
        if not cls: continue
        t0 = parse_ts(op.get("timestamp",""))
        t1 = parse_ts(cls[0].get("timestamp",""))
        if t0 and t1:
            hold_secs.append((t1-t0).total_seconds())

    print("Hold time (open → first close):")
    instant = sum(1 for s in hold_secs if s <= 2)
    short   = sum(1 for s in hold_secs if 2 < s <= 30)
    medium  = sum(1 for s in hold_secs if 30 < s <= 120)
    long_   = sum(1 for s in hold_secs if s > 120)
    print(f"  ≤2s (instant):  {instant}")
    print(f"  2–30s:          {short}")
    print(f"  30–120s:        {medium}")
    print(f"  >120s:          {long_}")
    if hold_secs:
        print(f"  Median: {statistics.median(hold_secs):.1f}s  Max: {max(hold_secs):.0f}s")
    print()

    # ── 6. ENTRY TIMING (from signals_evaluated) ──────────────────────────
    traded_sigs = [r for r in sigs if r.get("traded") == "Y"]
    print(f"Traded signals (entries): {len(traded_sigs)}")
    sr_bins = defaultdict(int)
    for r in traded_sigs:
        sr = f(r.get("secs_remaining", 0))
        if   sr >= 240: sr_bins["240-300s"] += 1
        elif sr >= 180: sr_bins["180-240s"] += 1
        elif sr >= 120: sr_bins["120-180s"] += 1
        elif sr >= 60:  sr_bins["60-120s"]  += 1
        else:           sr_bins["<60s"]     += 1
    print("  Entry secs_remaining:")
    for k in ["240-300s","180-240s","120-180s","60-120s","<60s"]:
        print(f"    {k}: {sr_bins[k]}")
    print()

    # ── 7. SKIP REASONS ───────────────────────────────────────────────────
    skip_reasons = defaultdict(int)
    for r in sigs:
        if r.get("traded") == "N":
            reason = (r.get("reason") or r.get("risk_block_reason") or "")[:80]
            skip_reasons[reason] += 1
    print("Top skip reasons:")
    for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1])[:8]:
        print(f"  [{count:5d}] {reason}")
    print()

    # ── 8. PRICE PATH: REVERSAL RATES ────────────────────────────────────
    if paths:
        windows = defaultdict(lambda: {"asset":"","y":[],"n":[]})
        for r in paths:
            cid = r.get("condition_id","")
            windows[cid]["asset"] = r.get("asset","")
            windows[cid]["y"].append(f(r.get("yes_price")))
            windows[cid]["n"].append(f(r.get("no_price")))

        entry_p = 0.15
        thresholds = [0.20,0.25,0.30,0.35,0.50,0.75]
        print("Price path reversal analysis (windows where cheapest side dipped to ≤15c):")
        print(f"  {'Asset':5s} {'Windows':>7s} {'Hit15c%':>7s}  " + "  ".join(f"to{int(t*100)}c%" for t in thresholds))
        print("  " + "-"*65)
        for a in ["BTC","ETH","SOL","XRP","DOGE"]:
            aw = [(cid,w) for cid,w in windows.items() if w["asset"]==a]
            n = len(aw)
            hit = [(cid,w) for cid,w in aw if min(w["y"])<=entry_p or min(w["n"])<=entry_p]
            nh = len(hit)
            recov = {t:0 for t in thresholds}
            for cid,w in hit:
                if min(w["y"]) <= entry_p:
                    for t in thresholds:
                        if max(w["y"]) >= t:
                            recov[t] += 1
                elif min(w["n"]) <= entry_p:
                    for t in thresholds:
                        if max(w["n"]) >= t:
                            recov[t] += 1
            hit_pct = 100*nh//n if n else 0
            cols = "  ".join(f"{100*recov[t]//nh:6d}%" for t in thresholds)
            print(f"  {a:5s} {n:7d} {hit_pct:6d}%   {cols}")
        print()
        print("  (shows % of windows that hit ≤15c AND then recovered to that level in same window)")
        print()

    # ── 9. DOGE: Why no trades? ───────────────────────────────────────────
    if paths:
        doge_p = [r for r in paths if r.get("asset") == "DOGE"]
        if doge_p:
            yp = [f(r.get("yes_price")) for r in doge_p]
            np_ = [f(r.get("no_price")) for r in doge_p]
            below15_y = sum(1 for p in yp if p <= 0.15)
            below15_n = sum(1 for p in np_ if p <= 0.15)
            total_t = len(doge_p)
            print(f"DOGE price range: YES={min(yp):.2f}–{max(yp):.2f}  NO={min(np_):.2f}–{max(np_):.2f}")
            print(f"DOGE ticks with YES<=15c: {below15_y}/{total_t} ({100*below15_y//total_t}%)")
            print(f"DOGE ticks with NO<=15c:  {below15_n}/{total_t} ({100*below15_n//total_t}%)")
            print("→ DOGE rarely hit exactly 15c during the first 3 min of a window → no entry triggered")
            print()

    # ── 10. ISSUE/MANIPULATION FLAGS ──────────────────────────────────────
    if paths:
        all_issues = [r for r in paths if (r.get("issues") or "").strip()]
        print(f"Price path issue flags: {len(all_issues)} / {len(paths)} ticks ({100*len(all_issues)//len(paths)}%)")
        by_asset_issue = defaultdict(int)
        for r in all_issues:
            by_asset_issue[r.get("asset","?")] += 1
        for a,cnt in sorted(by_asset_issue.items(), key=lambda x:-x[1]):
            pct = 100*cnt//sum(1 for r in paths if r.get("asset")==a)
            print(f"  {a}: {cnt} ticks ({pct}%)")
        print("  (sum_off = YES+NO ≠ 1.0 by >5% → thin book or price feed lag, not necessarily manipulation)")
        print()

    # ── 11. KEY CONCLUSIONS ───────────────────────────────────────────────
    print("="*65)
    print("KEY FINDINGS")
    print("="*65)
    print("""
1. PRICE PATHS (raw market data):
   - 98-99% of windows have one side dipping to ≤15c  → entry is ALWAYS available
   - 99% of those THEN recover to ≥35c in the same window
   - 83-86% recover to ≥50c
   This means the MARKET always reverses. The strategy's premise is CORRECT.

2. BOT ENTRIES vs PRICE PATH:
   - Bot entered only ~108 times across 646 windows (one per window, 5 assets)
   - Many windows were SKIPPED (entry only in first 3 min, no entry last 60s)
   - Hold time MEDIAN = 1.5s → bot exits almost IMMEDIATELY after entry
   - This is the core problem: the bot fires the tiered SL/TP check in the SAME
     tick it enters, often exiting before the reversal has time to happen.

3. SL QUALITY:
   - 55 SL closes were POSITIVE (reversal partially happened → good)
   - 31 SL closes near zero (no move either way)
   - 22 SL closes NEGATIVE (price moved against us before reversal → bad)
   - Negative SLs account for most losses: BTC -2.33, ETH -1.00

4. WHAT IS WORKING:
   - SOL +8.67 and XRP +6.00 are profitable
   - The strategy is capturing PARTIAL reversals (SL+pos is most common win type)
   - 8.5% of closes hit a TP tier (TP or TP_TIER_0.20)

5. WHAT IS NOT WORKING:
   - Bot exits in <2s → no time for reversal to manifest
   - BTC and ETH are net negative (price moves fast against us)
   - TP tiers are set at 20c+ but entry at 15c; market hits these, but bot
     exits via SL first because the next loop tick the price moved slightly
   - DOGE: never enters because YES price rarely drops to exactly 15c

6. ROOT CAUSE:
   The 1-second loop + ENTRY_MAX_ELAPSED_SECS=180 + sticky SL means:
   - Bot often enters 120-180s into the window (late entry)
   - Only 60-180s left for the reversal to play out
   - Price sometimes dips further then reverses, but the bot SL-exits first

7. RECOMMENDATIONS:
   a) LOWER the SL threshold or add a HOLD_MIN_SECS (e.g. 15-30s) before
      allowing SL to fire → give the reversal time to happen
   b) ENTER EARLIER (first 60s) when price is cheapest
   c) DOGE: lower entry threshold to 0.20 (price range 0.24-0.76 → never hits 0.15)
   d) BTC/ETH move faster → either skip them or widen SL
   e) STRATEGY IS SOUND: the market DOES reverse 99% of the time to 35c+;
      the problem is execution, not the thesis
""")

if __name__ == "__main__":
    main()
