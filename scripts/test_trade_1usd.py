#!/usr/bin/env python3
"""
One-off $5 test trade on the active BTC 5-min market.

Polymarket minimum order size is ~5 tokens/USDC. Bypasses strategy - places real BUY on YES.
Requires REAL_TRADING=True and valid .env credentials.

Usage (from project root):
  python scripts/test_trade_1usd.py
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

import config
from polymarket_client import PolymarketClient


def main() -> None:
    if not config.REAL_TRADING:
        logger.error("REAL_TRADING=False in config.py. Set to True for test trade.")
        sys.exit(1)

    client = PolymarketClient()
    if not client.has_credentials:
        logger.error("No Polymarket credentials. Check .env (PRIVATE_KEY, PROXY_WALLET).")
        sys.exit(1)

    market = client.find_active_btc_market()
    if not market:
        logger.error("No active BTC 5-min market. Try again during a window.")
        sys.exit(1)

    token_id = market["yes_token_id"]
    direction = "YES"

    price = client.get_best_price(token_id, side="BUY")
    if price is None:
        price = client.get_mid_price(token_id)
    if price is None or price <= 0:
        logger.error("Could not get price for token. Aborting.")
        sys.exit(1)

    logger.info("Market: %s", market["question"][:60])
    logger.info("Placing $5 BUY %s @ %.3f (real, min size)", direction, price)

    resp = client.place_order(
        token_id=token_id,
        side="BUY",
        size_usdc=5.0,
        price=price,
    )

    if resp:
        logger.info("SUCCESS: Order placed. Check Polymarket portfolio.")
    else:
        logger.error("Order failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
