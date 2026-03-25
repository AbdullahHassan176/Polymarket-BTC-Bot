#!/usr/bin/env python3
"""
One-shot script to claim all pending redeemable positions via direct on-chain
redemption (lib/ctf_redeem). Same approach as Polymarket-Bitcoin-Oracle-Latency-Arbitrage-Bot.

Uses Data API GET /positions?user=<proxy>&redeemable=true to find positions,
then redeems each via ctf_redeem (Gnosis Safe + EOA signer).

Usage (from project root):
  python scripts/claim_unclaimed.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "lib"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import requests

PROXY = os.getenv("PROXY_WALLET", "").strip()
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"


def get_neg_risk(condition_id: str) -> bool:
    """Check Gamma for negRisk. BTC 5-min markets are neg-risk."""
    try:
        r = requests.get(
            f"{GAMMA_API}/markets",
            params={"condition_id": condition_id},
            timeout=8,
        )
        if r.status_code != 200:
            return True
        data = r.json()
        if isinstance(data, list) and data:
            return bool(data[0].get("negRisk", True))
        return True
    except Exception:
        return True


def main() -> None:
    if not PROXY:
        print("PROXY_WALLET not set in .env")
        sys.exit(1)

    print(f"Wallet: {PROXY}")
    print("Fetching redeemable positions...")
    redeemable = []

    r = requests.get(
        f"{DATA_API}/positions",
        params={"user": PROXY, "redeemable": "true", "sizeThreshold": "0"},
        timeout=15,
    )
    if r.status_code == 200:
        raw = r.json()
        positions = raw if isinstance(raw, list) else raw.get("data", raw.get("positions", [])) or []
        if not isinstance(positions, list):
            positions = [positions] if positions else []
        redeemable = [p for p in positions if p.get("conditionId") or p.get("condition_id")]

    if not redeemable:
        r2 = requests.get(
            f"{GAMMA_API}/positions",
            params={"user": PROXY, "sizeThreshold": "0"},
            timeout=15,
        )
        if r2.status_code == 200:
            raw2 = r2.json()
            pos2 = raw2 if isinstance(raw2, list) else raw2.get("data", raw2.get("positions", [])) or []
            if not isinstance(pos2, list):
                pos2 = [pos2] if pos2 else []
            redeemable = [p for p in pos2 if p.get("redeemable") or (p.get("conditionId") and float(p.get("size") or 0) > 0)]

    if not redeemable:
        r3 = requests.get(
            f"{DATA_API}/positions",
            params={"user": PROXY, "sizeThreshold": "0", "limit": "50"},
            timeout=15,
        )
        if r3.status_code == 200:
            raw3 = r3.json()
            pos3 = raw3 if isinstance(raw3, list) else raw3.get("data", raw3.get("positions", [])) or []
            if not isinstance(pos3, list):
                pos3 = [pos3] if pos3 else []
            redeemable = [p for p in pos3 if p.get("redeemable")]

    print(f"Found {len(redeemable)} settled position(s).")

    # Filter to genuine winners only: currentValue > 0 (or cashPnl > 0 as fallback).
    # The Data API marks ALL settled positions as redeemable=True even for losers
    # (where currentValue=0). Attempting to redeem $0 positions wastes POL on gas.
    winners = []
    for p in redeemable:
        current_val = float(p.get("currentValue", 0) or 0)
        cash_pnl    = float(p.get("cashPnl", 0) or 0)
        if current_val > 0 or cash_pnl > 0:
            winners.append(p)

    if not winners:
        print("No winning positions to claim (all settled positions have currentValue=0 — losers).")
        return

    print(f"Genuine winners to claim: {len(winners)}")

    try:
        from ctf_redeem import redeem_winning_position
    except ImportError as e:
        print(f"ctf_redeem not available: {e}")
        sys.exit(1)

    success_count = 0
    fail_count = 0
    skip_count = 0

    for p in winners:
        cond = (p.get("conditionId") or p.get("condition_id") or "").strip()
        if not cond:
            continue
        if not cond.startswith("0x"):
            cond = "0x" + cond
        size = float(p.get("size", 0) or 0)
        outcome = (p.get("outcome") or p.get("outcomeName") or "").strip().lower()
        outcome_index = p.get("outcomeIndex", 0)
        direction = "up" if ("up" in outcome or "yes" in outcome or outcome_index == 0) else "down"
        neg_risk = get_neg_risk(cond)
        asset_raw = p.get("asset", "")

        # Parse token ID from the asset field (large uint256 from the CLOB).
        # Used to read exact on-chain balance instead of relying on API-reported size.
        token_id: int | None = None
        if asset_raw and str(asset_raw).isdigit():
            token_id = int(asset_raw)

        print(f"Redeeming  {asset_raw}  size={size:.4f}  dir={direction}  neg_risk={neg_risk}  cond={cond[:22]}...")

        try:
            tx_hash = redeem_winning_position(
                condition_id=cond,
                direction=direction,
                neg_risk=neg_risk,
                size_matched=size,
                token_id=token_id,
            )
            if tx_hash == "SKIP_ZERO_BALANCE":
                print("  SKIP  (zero on-chain balance — already redeemed or losing position)")
                skip_count += 1
            elif tx_hash:
                print(f"  SUCCESS  tx={tx_hash[:42]}...")
                success_count += 1
            else:
                print("  FAILED  Claim manually on polymarket.com")
                fail_count += 1
            time.sleep(2)
        except KeyboardInterrupt:
            print("  Interrupted.")
            raise
        except Exception as ex:
            print(f"  ERROR  {ex}")
            fail_count += 1
        print()

    print(f"Done. SUCCESS={success_count}  FAILED={fail_count}  SKIPPED={skip_count}")


if __name__ == "__main__":
    main()
