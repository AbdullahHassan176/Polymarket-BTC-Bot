"""
arbitrage/run_arbitrage_loop.py – Fully automated arbitrage loop.

Discovers Polymarket price-target markets, computes option-implied fair value,
buys Yes when mispriced (cheap), monitors for resolution, auto-redeems on win.

No LLM required – uses deterministic Black-Scholes math.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import config
from polymarket_client import PolymarketClient
from arbitrage.fair_value import discounted_fair_value
from arbitrage.market_data import (
    get_spot_and_vol,
    parse_barrier_and_direction,
)
from arbitrage.arb_state import (
    add_position,
    add_paper_pnl,
    get_open_positions,
    get_paper_balance,
    init_paper_balance,
    remove_position,
    update_position,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _time_to_expiry_years(end_iso: str) -> float:
    """Parse end date and return years from now."""
    if not end_iso:
        return 0.0
    try:
        end = datetime.fromisoformat(str(end_iso).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = (end - now).total_seconds()
        return max(0.0, delta / (365.25 * 24 * 3600))
    except (ValueError, TypeError):
        return 0.0


def _collect_arbitrage_candidates(client: PolymarketClient, use_gamma_prices: bool = False) -> list:
    """Fetch open markets from search queries that look like price-target events.
    If use_gamma_prices=True, use outcomePrices from Gamma (faster, no CLOB calls).
    """
    seen = set()
    candidates = []
    for q in getattr(config, "ARBITRAGE_SEARCH_QUERIES", ["Google", "NVIDIA", "stock hit"]):
        events = client.search_events(q, limit_per_type=10)
        for event in events:
            for m in event.get("markets", []):
                if m.get("closed"):
                    continue
                cids = PolymarketClient._parse_clob_token_ids(m.get("clobTokenIds"))
                if len(cids) < 2:
                    continue
                cid = m.get("conditionId", "")
                if cid in seen:
                    continue
                seen.add(cid)
                end = m.get("endDate") or m.get("endDateIso") or event.get("endDate") or ""
                question = m.get("question") or event.get("title", "")
                barrier, event_type = parse_barrier_and_direction(question)
                if barrier is None:
                    continue
                if use_gamma_prices:
                    raw = m.get("outcomePrices")
                    if isinstance(raw, list) and len(raw) >= 1:
                        try:
                            yes_mid = float(raw[0])
                        except (ValueError, TypeError):
                            yes_mid = None
                    elif isinstance(raw, str):
                        try:
                            import json
                            arr = json.loads(raw)
                            yes_mid = float(arr[0]) if arr else None
                        except (json.JSONDecodeError, ValueError, TypeError):
                            yes_mid = None
                    else:
                        yes_mid = None
                else:
                    yes_mid = client.get_mid_price(cids[0])
                if yes_mid is None:
                    continue
                candidates.append({
                    "event": event,
                    "market": m,
                    "question": question,
                    "slug": event.get("slug", ""),
                    "condition_id": cid,
                    "yes_token_id": cids[0],
                    "no_token_id": cids[1],
                    "end_iso": str(end),
                    "yes_mid": yes_mid,
                    "barrier": barrier,
                    "event_type": event_type,
                })
    return candidates


def _evaluate_candidate(cand: dict, spot_vol_cache: dict | None = None) -> dict | None:
    """
    Compute fair value and verdict. Returns None if we can't price, else
    {cand, fair_value, verdict: "cheap"|"fair"|"expensive", edge}.
    spot_vol_cache: optional dict keyed by ticker -> (spot, sigma) to avoid repeated fetches.
    """
    spot_vol_cache = spot_vol_cache or {}
    ticker = None
    for k, t in (("google", "GOOGL"), ("amazon", "AMZN"), ("nvidia", "NVDA"), ("tesla", "TSLA"), ("apple", "AAPL"),
                 ("meta", "META"), ("microsoft", "MSFT"), ("netflix", "NFLX"), ("palantir", "PLTR"), ("opendoor", "OPEN")):
        if k in cand["question"].lower():
            ticker = t
            break
    if not ticker:
        from arbitrage.market_data import _infer_ticker_from_question
        ticker = _infer_ticker_from_question(cand["question"])
    if ticker and ticker in spot_vol_cache:
        spot, sigma = spot_vol_cache[ticker]
    else:
        spot, sigma, ticker = get_spot_and_vol(
            cand["question"],
            default_iv=getattr(config, "ARBITRAGE_DEFAULT_IV", 0.25),
            use_historical_vol=True,
        )
        if ticker and spot is not None and sigma is not None:
            spot_vol_cache[ticker] = (spot, sigma)
    if spot is None or sigma is None:
        return None

    T = _time_to_expiry_years(cand["end_iso"])
    if T <= 0:
        return None

    r = getattr(config, "ARBITRAGE_RISK_FREE_RATE", 0.045)
    fair = discounted_fair_value(
        spot=spot,
        strike=cand["barrier"],
        time_years=T,
        risk_free_rate=r,
        sigma=sigma,
        dividend_yield=0.0,
        event_type=cand["event_type"],
    )

    pm_yes = cand["yes_mid"]
    edge = fair - pm_yes
    min_edge = getattr(config, "ARBITRAGE_MIN_EDGE", 0.05)

    if edge >= min_edge:
        verdict = "cheap"
    elif edge <= -min_edge:
        verdict = "expensive"
    else:
        verdict = "fair"

    return {
        "cand": cand,
        "fair_value": fair,
        "polymarket_yes": pm_yes,
        "edge": edge,
        "verdict": verdict,
        "spot": spot,
        "sigma": sigma,
        "ticker": ticker,
    }


def _potential_profit_usdc(size_usdc: float, yes_price: float) -> float:
    """If we buy Yes at yes_price with size_usdc and win: tokens pay $1 each. Profit = (size_usdc/yes_price)*1 - size_usdc."""
    if yes_price <= 0:
        return 0.0
    return (size_usdc / yes_price) * 1.0 - size_usdc


def _run_report(client: PolymarketClient, max_candidates: int = 150) -> None:
    """Discover all candidates, evaluate, print and save report of mispricings and potential profit."""
    size = getattr(config, "ARBITRAGE_RISK_PER_TRADE_USDC", 10.0)

    candidates = _collect_arbitrage_candidates(client, use_gamma_prices=True)
    if max_candidates and len(candidates) > max_candidates:
        candidates = candidates[:max_candidates]
        logger.info("Capped to %d candidates for report.", max_candidates)
    logger.info("Collected %d candidate markets. Evaluating...", len(candidates))

    # Cache spot/vol by ticker to avoid repeated yfinance calls
    _spot_vol_cache = {}
    results = []
    for cand in candidates:
        ev = _evaluate_candidate(cand, spot_vol_cache=_spot_vol_cache)
        if ev is None:
            continue
        c = ev["cand"]
        profit_if_win = _potential_profit_usdc(size, c["yes_mid"]) if ev["verdict"] == "cheap" else 0.0
        results.append({
            "question": c["question"],
            "ticker": ev.get("ticker", ""),
            "barrier": c["barrier"],
            "spot": ev.get("spot"),
            "sigma_pct": (ev.get("sigma") or 0) * 100,
            "pm_yes_pct": ev["polymarket_yes"] * 100,
            "fair_pct": ev["fair_value"] * 100,
            "edge_pct": ev["edge"] * 100,
            "verdict": ev["verdict"],
            "potential_profit": profit_if_win,
            "condition_id": c.get("condition_id", ""),
        })

    # Sort: cheap first, then by edge descending
    results.sort(key=lambda r: (0 if r["verdict"] == "cheap" else 1 if r["verdict"] == "fair" else 2, -r["edge_pct"]))

    # Print table
    print("\n" + "=" * 120)
    print("ARBITRAGE SCAN REPORT - Polymarket vs option-implied fair value")
    print("=" * 120)
    fmt = "%-55s %5s %7s %8s %8s %8s %8s %8s %10s"
    print(fmt % ("Question (truncated)", "Ticker", "PM%", "Fair%", "Edge%", "Verdict", "Spot", "Vol%", "Profit$"))
    print("-" * 120)
    cheap_count = fair_count = exp_count = 0
    total_potential_profit = 0.0
    for r in results:
        q = (r["question"][:52] + "..") if len(r["question"]) > 55 else r["question"]
        if r["verdict"] == "cheap":
            cheap_count += 1
            total_potential_profit += r["potential_profit"]
        elif r["verdict"] == "fair":
            fair_count += 1
        else:
            exp_count += 1
        print(fmt % (
            q,
            r["ticker"],
            "%.1f" % r["pm_yes_pct"],
            "%.1f" % r["fair_pct"],
            "%.1f" % r["edge_pct"],
            r["verdict"],
            "%.0f" % (r["spot"] or 0),
            "%.0f" % r["sigma_pct"],
            "%.2f" % r["potential_profit"] if r["potential_profit"] else "",
        ))
    print("-" * 120)
    print("Summary: CHEAP=%d (buy Yes)  FAIR=%d  EXPENSIVE=%d" % (cheap_count, fair_count, exp_count))
    print("Potential profit if all CHEAP bets win (at $%.0f per bet): $%.2f" % (size, total_potential_profit))
    print("=" * 120 + "\n")

    # Save CSV
    csv_path = _REPO_ROOT / "logs" / "arbitrage_report.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("question,ticker,barrier,spot,sigma_pct,pm_yes_pct,fair_pct,edge_pct,verdict,potential_profit_usdc,condition_id\n")
        for r in results:
            q_esc = r["question"].replace('"', '""')
            f.write('"%s",%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' % (
                q_esc, r["ticker"], r["barrier"], r["spot"] or "", r["sigma_pct"],
                r["pm_yes_pct"], r["fair_pct"], r["edge_pct"], r["verdict"],
                r["potential_profit"], r["condition_id"],
            ))
    logger.info("Report saved to %s", csv_path)


def _run_one_cycle(client: PolymarketClient, paper: bool) -> None:
    """One iteration: discover, evaluate, trade if cheap, check resolutions and TP/SL (paper)."""
    tp = getattr(config, "ARBITRAGE_PAPER_TAKE_PROFIT", 0.85)
    sl = getattr(config, "ARBITRAGE_PAPER_STOP_LOSS", 0.25)

    # 0. Paper: check take profit / stop loss on open positions (simulate selling at current mid)
    if paper:
        for pos in list(get_open_positions()):
            if pos.get("mode") != "PAPER":
                continue
            cid = pos.get("condition_id")
            mid = client.get_mid_price(pos.get("yes_token_id", ""))
            if mid is None:
                continue
            size = pos.get("size_usdc", 0)
            tokens = pos.get("num_tokens", 0)
            if mid >= tp:
                # Take profit: sell at mid
                pnl = tokens * mid - size
                remove_position(cid)
                add_paper_pnl(pnl)
                logger.info("PAPER TP: %s @ mid=%.2f | PnL: $%.2f | Balance: $%.2f",
                    pos.get("question", "")[:45], mid, pnl, get_paper_balance() or 0)
            elif mid <= sl:
                # Stop loss: sell at mid
                pnl = tokens * mid - size
                remove_position(cid)
                add_paper_pnl(pnl)
                logger.info("PAPER SL: %s @ mid=%.2f | PnL: $%.2f | Balance: $%.2f",
                    pos.get("question", "")[:45], mid, pnl, get_paper_balance() or 0)

    # 1. Check open positions for resolution
    for pos in get_open_positions():
        cid = pos.get("condition_id")
        slug = pos.get("slug", "")
        if client.is_market_closed(cid, slug if slug else None):
            result = client.get_market_result(cid, slug if slug else None)
            if result:
                direction = pos.get("direction", "YES")
                won = result == direction
                pnl = (pos.get("num_tokens", 0) * 1.0 - pos.get("size_usdc", 0)) if won else -pos.get("size_usdc", 0)
                if paper and pos.get("mode") == "PAPER":
                    add_paper_pnl(pnl)
                    logger.info("PAPER resolved: %s (we bet %s). %s. PnL: $%.2f | Balance: $%.2f",
                        result, direction, "WIN" if won else "LOSS", pnl, get_paper_balance() or 0)
                else:
                    logger.info("Position resolved: %s (we bet %s). Outcome: %s. PnL: $%.2f", result, direction, "WIN" if won else "LOSS", pnl)
                remove_position(cid)
                if not paper and getattr(config, "AUTO_REDEEM_ENABLED", False):
                    from redeem import redeem_winning_position
                    redeem_winning_position(cid, direction, pos.get("num_tokens", 0))

    # 2. Skip new trades if at max positions or insufficient paper balance
    open_pos = get_open_positions()
    max_pos = getattr(config, "ARBITRAGE_MAX_OPEN_POSITIONS", 3)
    if len(open_pos) >= max_pos:
        return
    size = getattr(config, "ARBITRAGE_RISK_PER_TRADE_USDC", 10.0)
    if paper:
        bal = get_paper_balance()
        if bal is not None and (bal < size or bal <= 0):
            logger.debug("PAPER balance $%.2f insufficient for $%.2f trade.", bal, size)
            return

    # 3. Discover and evaluate candidates (use Gamma prices in paper mode for speed)
    candidates = _collect_arbitrage_candidates(client, use_gamma_prices=paper)
    for cand in candidates:
        ev = _evaluate_candidate(cand)
        if ev is None:
            continue
        c = ev["cand"]
        logger.info(
            "Arb check: %s | fair=%.2f%% pm=%.2f%% edge=%.2f%% %s",
            c["question"][:50],
            ev["fair_value"] * 100,
            ev["polymarket_yes"] * 100,
            ev["edge"] * 100,
            ev["verdict"],
        )
        if ev["verdict"] != "cheap":
            continue

        # 4. Place order (skip if we already have a position or market is closed)
        cid = c["condition_id"]
        if any(p.get("condition_id") == cid for p in open_pos):
            logger.debug("Already have position in %s, skip.", cid[:20])
            continue
        if client.is_market_closed(cid, c.get("slug")):
            logger.debug("Market already closed, skip.")
            continue
        size = getattr(config, "ARBITRAGE_RISK_PER_TRADE_USDC", 10.0)
        price = c["yes_mid"]
        token_id = c["yes_token_id"]

        if paper:
            if bal is not None:
                add_paper_pnl(-size)
            logger.info(
                "PAPER ARB BUY YES: %s @ %.3f $%.2f (fair=%.2f%%) | Balance: $%.2f",
                c["question"][:60],
                price,
                size,
                ev["fair_value"] * 100,
                get_paper_balance() or 0,
            )
            pos = {
                "open": True,
                "mode": "PAPER",
                "question": c["question"],
                "condition_id": c["condition_id"],
                "slug": c["slug"],
                "direction": "YES",
                "entry_price": price,
                "size_usdc": size,
                "num_tokens": round(size / price, 4),
                "yes_token_id": c["yes_token_id"],
                "no_token_id": c["no_token_id"],
                "end_iso": c["end_iso"],
            }
            add_position(pos)
            return  # One trade per cycle
        else:
            if not config.REAL_TRADING:
                logger.warning("REAL_TRADING=False. Skipping real order.")
                return
            resp = client.place_order(token_id=token_id, side="BUY", size_usdc=size, price=price)
            if resp:
                pos = {
                    "open": True,
                    "mode": "REAL",
                    "question": c["question"],
                    "condition_id": c["condition_id"],
                    "slug": c["slug"],
                    "direction": "YES",
                    "entry_price": price,
                    "size_usdc": size,
                    "num_tokens": round(size / price, 4),
                    "yes_token_id": c["yes_token_id"],
                    "no_token_id": c["no_token_id"],
                    "end_iso": c["end_iso"],
                }
                add_position(pos)
                logger.info("REAL ARB BUY YES placed: %s", c["question"][:60])
                return


def main() -> None:
    import argparse
    import time
    p = argparse.ArgumentParser(description="Automated Polymarket arbitrage loop")
    p.add_argument("--paper", action="store_true", help="Paper trade only")
    p.add_argument("--paper-start", type=float, default=None, help="Paper starting balance USDC (e.g. 50). Default from config.")
    p.add_argument("--once", action="store_true", help="Run one cycle then exit")
    p.add_argument("--report", action="store_true", help="Scan all markets and print mispricing report (no trading)")
    p.add_argument("--interval", type=int, default=300, help="Seconds between cycles (default 300)")
    p.add_argument("--duration", type=int, default=0, help="Run for N seconds then exit (e.g. 10800 = 3 hours)")
    args = p.parse_args()

    client = PolymarketClient()
    logger.info("Arbitrage loop starting (paper=%s). Searches: %s", args.paper, getattr(config, "ARBITRAGE_SEARCH_QUERIES", []))

    if args.report:
        _run_report(client)
        return

    if args.paper and (args.paper_start is not None or get_paper_balance() is None):
        start_usdc = args.paper_start if args.paper_start is not None else getattr(config, "ARBITRAGE_PAPER_STARTING_BALANCE_USDC", 50.0)
        init_paper_balance(start_usdc)
        logger.info("PAPER balance initialized: $%.2f (TP=%.0f%% SL=%.0f%%)",
            start_usdc,
            getattr(config, "ARBITRAGE_PAPER_TAKE_PROFIT", 0.85) * 100,
            getattr(config, "ARBITRAGE_PAPER_STOP_LOSS", 0.25) * 100,
        )

    if args.once:
        _run_one_cycle(client, args.paper)
        if args.paper:
            logger.info("PAPER session balance: $%.2f", get_paper_balance() or 0)
        return

    deadline = (time.time() + args.duration) if args.duration > 0 else None
    while True:
        try:
            if deadline and time.time() >= deadline:
                logger.info("Duration reached. Stopping.")
                break
            _run_one_cycle(client, args.paper)
        except Exception as exc:
            logger.exception("Cycle error: %s", exc)
        if deadline and time.time() + args.interval >= deadline:
            time.sleep(max(0, deadline - time.time()))
            logger.info("Duration reached. Stopping.")
            break
        time.sleep(args.interval)

    if args.paper and (args.duration or args.once):
        bal = get_paper_balance()
        start = getattr(config, "ARBITRAGE_PAPER_STARTING_BALANCE_USDC", 50.0)
        logger.info("PAPER session ended. Starting: $%.2f | Ending: $%.2f | PnL: $%.2f",
            start, bal or 0, (bal or 0) - start)
        print("\n--- Paper trading summary ---")
        print("Starting balance: $%.2f" % start)
        print("Ending balance:   $%.2f" % (bal or 0))
        print("PnL:              $%.2f" % ((bal or 0) - start))


if __name__ == "__main__":
    main()
