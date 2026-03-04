"""
polymarket_client.py  -  Polymarket CLOB API client.

Handles:
  - Two-level authentication (L1 EIP-712 wallet signing + L2 HMAC API credentials)
  - Market discovery via the Gamma API (finding active BTC 5-min windows)
  - Orderbook and mid-price fetching (public, no auth required)
  - Order placement via py-clob-client
  - Position and open order queries

Polymarket docs: https://docs.polymarket.com/developers/CLOB/introduction

Two APIs used:
  1. Gamma API  (https://gamma-api.polymarket.com) - market metadata, discovery
  2. CLOB API   (https://clob.polymarket.com)       - orderbook, order placement
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

import config

logger = logging.getLogger(__name__)

# Whether the py-clob-client library is installed.
# In paper mode without credentials, we skip the import gracefully.
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    logger.warning(
        "py-clob-client not installed. Run: pip install -r requirements.txt"
    )


class PolymarketClient:
    """
    Thin wrapper around the Polymarket CLOB API and Gamma markets API.

    Authentication is two-level:
      L1: Your private key signs EIP-712 typed messages to prove wallet ownership.
      L2: Derived API credentials (api_key, api_secret, passphrase) are used for
          HMAC-SHA256 signatures on each trading request.

    Both levels are handled automatically by py-clob-client once initialised.
    Public endpoints (market discovery, orderbook) require no authentication.
    """

    def __init__(self) -> None:
        self.has_credentials: bool = bool(
            config.POLY_PRIVATE_KEY and config.POLY_WALLET_ADDRESS
        )
        self._clob: Optional[object] = None  # ClobClient instance

        if self.has_credentials and CLOB_AVAILABLE:
            self._init_clob_client()
        elif not self.has_credentials:
            logger.warning(
                "No Polymarket credentials found in .env. "
                "Running in simulation mode. Order placement will be skipped."
            )
        elif not CLOB_AVAILABLE:
            logger.warning(
                "py-clob-client not installed. Install with: pip install -r requirements.txt"
            )

    def _init_clob_client(self) -> None:
        """
        Initialise the py-clob-client and derive L2 API credentials from the private key.

        This only runs once at startup. The derived credentials are deterministic -
        the same private key always produces the same api_key/secret/passphrase.
        """
        try:
            self._clob = ClobClient(
                host=config.CLOB_HOST,
                key=config.POLY_PRIVATE_KEY,
                chain_id=config.CHAIN_ID,
                signature_type=config.SIGNATURE_TYPE,  # 0 = EOA / MetaMask
                funder=config.POLY_WALLET_ADDRESS,
            )
            # Derive L2 credentials from the private key (deterministic).
            api_creds = self._clob.create_or_derive_api_creds()
            self._clob.set_api_creds(api_creds)
            logger.info(
                "Polymarket client initialised. Wallet: %s...%s",
                config.POLY_WALLET_ADDRESS[:6],
                config.POLY_WALLET_ADDRESS[-4:],
            )
        except Exception as exc:
            logger.error("Failed to initialise Polymarket CLOB client: %s", exc)
            self._clob = None

    # -----------------------------------------------------------------------
    # MARKET DISCOVERY  (Gamma API - public, no auth)
    # -----------------------------------------------------------------------

    @staticmethod
    def _parse_clob_token_ids(raw: object) -> List[str]:
        """Parse clobTokenIds from API (can be JSON string or list)."""
        if isinstance(raw, list) and len(raw) >= 2:
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and len(parsed) >= 2:
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return []

    def _find_btc_5m_candidates_from_search(self) -> list:
        """Get open BTC 5-min markets from Gamma public-search."""
        try:
            resp = requests.get(
                f"{config.GAMMA_API}/public-search",
                params={"q": config.MARKET_KEYWORD, "limit_per_type": 20},
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.debug("Gamma public-search failed: %s", exc)
            return []
        events = data.get("events", []) if isinstance(data, dict) else []
        return self._collect_open_btc_5m_candidates(events)

    def _find_btc_5m_candidates_from_slug(self) -> list:
        """Get current 5-min window market by slug (btc-updown-5m-{timestamp})."""
        window_ts = (int(time.time()) // 300) * 300
        slug = f"btc-updown-5m-{window_ts}"
        try:
            resp = requests.get(
                f"{config.GAMMA_API}/events",
                params={"slug": slug},
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.debug("Gamma events by slug failed: %s", exc)
            return []
        events = data if isinstance(data, list) else []
        return self._collect_open_btc_5m_candidates(events)

    def _collect_open_btc_5m_candidates(self, events: list) -> list:
        """From Gamma events, collect open BTC 5-min markets with valid token IDs."""
        candidates = []
        for event in events:
            slug = event.get("slug", "")
            if not slug.startswith("btc-updown-5m-"):
                continue
            for m in event.get("markets", []):
                if m.get("closed"):
                    continue
                clob_ids = self._parse_clob_token_ids(m.get("clobTokenIds"))
                if len(clob_ids) < 2:
                    continue
                candidates.append({"_market": m, "_event": event, "clob_ids": clob_ids})
        return candidates

    def find_active_btc_market(self) -> Optional[dict]:
        """
        Find the currently active BTC 5-minute direction market.
        Tries public-search first, then slug-based lookup for the current window.
        """
        candidates = self._find_btc_5m_candidates_from_search()
        if not candidates:
            candidates = self._find_btc_5m_candidates_from_slug()
        if not candidates:
            logger.debug("No open BTC 5-min market (try again when a window is active).")
            return None

        # Pick the candidate whose end date is soonest (current window).
        def parse_end(c: dict) -> datetime:
            m = c["_market"]
            for key in ("endDate", "endDateIso", "end_date"):
                val = m.get(key)
                if val:
                    try:
                        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                    except ValueError:
                        pass
            return datetime.max.replace(tzinfo=timezone.utc)

        candidates.sort(key=parse_end)
        best   = candidates[0]
        market = best["_market"]
        event  = best["_event"]
        clob_ids = best["clob_ids"]
        end_date = parse_end(best)

        question = market.get("question") or event.get("title", "")
        logger.info("Found active 5-min market: %s", question)

        return {
            "question":     question,
            "condition_id": market.get("conditionId", ""),
            "yes_token_id": clob_ids[0],
            "no_token_id":  clob_ids[1],
            "end_date":     end_date,
            "end_date_iso": end_date.isoformat(),
            "slug":         event.get("slug", ""),
        }

    def _get_market_by_slug(self, slug: str) -> Optional[dict]:
        """
        Fetch market dict by event slug (reliable for 5-min btc-updown markets).
        GET /events?slug=... returns a list with one event; event has markets[0].
        """
        if not slug or not slug.startswith("btc-updown-5m-"):
            return None
        try:
            resp = requests.get(
                f"{config.GAMMA_API}/events",
                params={"slug": slug},
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.debug("Gamma events by slug failed: %s", exc)
            return None
        events = data if isinstance(data, list) else []
        if not events:
            return None
        markets = events[0].get("markets", [])
        return markets[0] if markets else None

    def _get_market_by_condition(self, condition_id: str) -> Optional[dict]:
        """
        Fetch a market dict from Gamma API by its conditionId.

        Tries two endpoints: the standard markets endpoint first, then events.
        Returns the raw market dict or None.
        """
        for endpoint in ["/markets", "/events"]:
            try:
                resp = requests.get(
                    f"{config.GAMMA_API}{endpoint}",
                    params={"conditionId": condition_id},
                    timeout=config.REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                results = resp.json()
                if results:
                    return results[0]
            except requests.RequestException:
                continue
        return None

    def _get_market_for_resolution(self, condition_id: str, slug: Optional[str] = None) -> Optional[dict]:
        """Get market dict for resolution checks. Prefer slug for 5-min markets (conditionId often fails)."""
        if slug and slug.startswith("btc-updown-5m-"):
            m = self._get_market_by_slug(slug)
            if m:
                return m
        return self._get_market_by_condition(condition_id)

    def is_market_closed(self, condition_id: str, slug: Optional[str] = None) -> bool:
        """
        Check if a specific market has resolved (closed=true in Gamma API).
        For 5-min BTC markets pass slug=position["slug"] so we fetch by slug (reliable).
        """
        m = self._get_market_for_resolution(condition_id, slug)
        if m:
            return bool(m.get("closed", False))
        if slug:
            logger.warning("Could not fetch market status for slug=%s.", slug[:24])
        else:
            logger.warning("Could not fetch market status for conditionId=%s.", condition_id[:16])
        return False

    def get_market_result(self, condition_id: str, slug: Optional[str] = None) -> Optional[str]:
        """
        Fetch the resolution outcome of a closed market.
        For 5-min BTC markets pass slug=position["slug"] so we fetch by slug (reliable).
        """
        m = self._get_market_for_resolution(condition_id, slug)
        if not m:
            return None
        winners = m.get("winners", [])
        if winners:
            return "YES" if str(winners[0]) == "0" else "NO"
        # outcomePrices can be list or JSON string e.g. "[\"1\", \"0\"]"
        raw = m.get("outcomePrices", [])
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = []
        if raw:
            try:
                return "YES" if float(raw[0]) >= 0.99 else "NO"
            except (ValueError, TypeError):
                pass
        return None

    # -----------------------------------------------------------------------
    # PRICE / ORDERBOOK  (CLOB API - public, no auth)
    # -----------------------------------------------------------------------

    def get_mid_price(self, token_id: str) -> Optional[float]:
        """
        Fetch the current mid-price for a YES or NO token.

        The mid-price is the average of the best bid and best ask.
        This is the "fair value" of the token (probability implied by the market).

        Args:
            token_id: The CLOB token ID for the outcome (YES or NO).

        Returns:
            Mid-price as a float between 0.0 and 1.0, or None on failure.
        """
        try:
            resp = requests.get(
                f"{config.CLOB_HOST}/midpoint",
                params={"token_id": token_id},
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            mid = data.get("mid")
            return float(mid) if mid is not None else None
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("Could not fetch mid price for token %s: %s", token_id[:8], exc)
            return None

    def get_best_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """
        Fetch the best available price to buy or sell a token.

        For BUY: this is the best ask (lowest price we can pay to buy YES).
        For SELL: this is the best bid (highest price we'd receive selling).

        Args:
            token_id: CLOB token ID.
            side:     "BUY" or "SELL".

        Returns:
            Price as a float, or None on failure.
        """
        try:
            resp = requests.get(
                f"{config.CLOB_HOST}/price",
                params={"token_id": token_id, "side": side},
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            price = data.get("price")
            return float(price) if price is not None else None
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("Could not fetch price for token %s: %s", token_id[:8], exc)
            return None

    # -----------------------------------------------------------------------
    # ORDER PLACEMENT  (CLOB API - requires L2 auth)
    # -----------------------------------------------------------------------

    def place_order(
        self,
        token_id: str,
        side: str,
        size_usdc: float,
        price: float,
    ) -> Optional[dict]:
        """
        Place a limit order for a YES or NO token.

        Uses GTC (Good-Til-Cancelled) limit orders at the current best price,
        which should fill immediately in a liquid market.

        Args:
            token_id:  CLOB token ID for the outcome to trade.
            side:      "BUY" (buy YES/NO) or "SELL" (close an existing position).
            size_usdc: Dollar amount to spend (for BUY) or tokens to sell.
            price:     Limit price (0.0 - 1.0). Use get_best_price() to find it.

        Returns:
            Order response dict from Polymarket, or None on failure.
        """
        if not self._clob:
            logger.error("CLOB client not initialised. Cannot place real order.")
            return None

        if not config.REAL_TRADING:
            logger.error("place_order() called but REAL_TRADING=False. Aborting.")
            return None

        try:
            clob_side = BUY if side.upper() == "BUY" else SELL
            # size = number of tokens. At price P, buying size_usdc/P tokens costs size_usdc.
            num_tokens = round(size_usdc / price, 4) if price > 0 else 0
            if num_tokens <= 0:
                logger.error("Computed token size is zero. Skipping order.")
                return None

            logger.info(
                "Placing %s order: %.4f tokens @ %.3f (token=%s...)",
                side, num_tokens, price, token_id[:8],
            )

            resp = self._clob.create_and_post_order(
                OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=num_tokens,
                    side=clob_side,
                ),
                order_type=OrderType.GTC,
            )
            logger.info("Order placed: %s", resp)
            return resp
        except Exception as exc:
            logger.error("Order placement failed: %s", exc)
            return None

    # -----------------------------------------------------------------------
    # POSITIONS / BALANCES  (CLOB API - requires L2 auth)
    # -----------------------------------------------------------------------

    def get_open_orders(self) -> list:
        """
        Return all currently open (unfilled) orders on the CLOB.

        Used to check for stuck orders that never filled.
        """
        if not self._clob:
            return []
        try:
            return self._clob.get_orders() or []
        except Exception as exc:
            logger.warning("Could not fetch open orders: %s", exc)
            return []

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a specific open order by ID.

        Returns True if cancellation succeeded, False otherwise.
        """
        if not self._clob:
            return False
        try:
            self._clob.cancel({"orderId": order_id})
            logger.info("Cancelled order %s.", order_id)
            return True
        except Exception as exc:
            logger.warning("Could not cancel order %s: %s", order_id, exc)
            return False

    def get_usdc_balance(self) -> Optional[float]:
        """
        Return the available USDC balance in the connected wallet.

        Note: This uses the CLOB allowances endpoint which shows funds
        available for trading (already approved for the CLOB contract).
        """
        if not self._clob:
            return None
        try:
            balance_data = self._clob.get_balance_allowance(
                params={"asset_type": "COLLATERAL"}
            )
            raw = balance_data.get("balance", "0")
            # Balance is in USDC with 6 decimal places (1000000 = $1.00).
            return float(raw) / 1_000_000
        except Exception as exc:
            logger.warning("Could not fetch USDC balance: %s", exc)
            return None
