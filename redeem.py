"""
redeem.py  -  Auto-redeem winning Polymarket positions to USDC.

Uses polymarket-apis (PolymarketWeb3Client) to redeem outcome tokens
back to USDC after a market resolves. Requires POL for gas (EOA).

Requires: polymarket-apis (pip install polymarket-apis)
Python 3.12+ recommended for polymarket-apis.
"""

import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)

_WEB3_CLIENT = None


def _get_web3_client():
    """Lazy-init PolymarketWeb3Client. Returns None if unavailable."""
    global _WEB3_CLIENT
    if _WEB3_CLIENT is not None:
        return _WEB3_CLIENT
    try:
        from polymarket_apis import PolymarketWeb3Client
        key = config.POLY_PRIVATE_KEY
        if not key:
            logger.debug("AUTO_REDEEM: No POLY_PRIVATE_KEY, skip redeem.")
            return None
        if not key.startswith("0x"):
            key = "0x" + key
        _WEB3_CLIENT = PolymarketWeb3Client(
            private_key=key,
            signature_type=getattr(config, "SIGNATURE_TYPE", 0),
        )
        return _WEB3_CLIENT
    except ImportError as e:
        logger.warning(
            "AUTO_REDEEM: polymarket-apis not installed (pip install polymarket-apis). %s",
            e,
        )
        return None
    except Exception as e:
        logger.warning("AUTO_REDEEM: Could not init PolymarketWeb3Client: %s", e)
        return None


def redeem_winning_position(
    condition_id: str,
    direction: str,
    num_tokens: float,
    neg_risk: bool = False,
) -> Optional[str]:
    """
    Redeem a winning position: convert outcome tokens to USDC.

    Args:
        condition_id: Market condition ID.
        direction:   "YES" or "NO" - which outcome we hold.
        num_tokens:  Number of tokens to redeem.
        neg_risk:    True for negative-risk markets (default False for binary).

    Returns:
        Transaction hash if successful, None otherwise.
    """
    if not getattr(config, "AUTO_REDEEM_ENABLED", False):
        return None
    if not getattr(config, "REAL_TRADING", False):
        return None
    if num_tokens <= 0:
        return None

    client = _get_web3_client()
    if client is None:
        return None

    # amounts: [yes_shares, no_shares]. outcome_index 0=YES, 1=NO.
    amounts = [0.0, 0.0]
    if direction == "YES":
        amounts[0] = num_tokens
    else:
        amounts[1] = num_tokens

    try:
        result = client.redeem_position(
            condition_id=condition_id,
            amounts=amounts,
            neg_risk=neg_risk,
        )
        if result:
            logger.info(
                "AUTO_REDEEM: Redeemed %s %.4f tokens -> USDC. Tx: %s",
                direction, num_tokens, result,
            )
            return result
        logger.warning("AUTO_REDEEM: redeem_position returned None.")
        return None
    except Exception as e:
        logger.error("AUTO_REDEEM: redeem failed: %s", e)
        return None
