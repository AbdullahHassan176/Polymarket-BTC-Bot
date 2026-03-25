"""
redeem.py - Auto-redeem winning Polymarket positions to USDC.

Uses lib/ctf_redeem (direct on-chain via Gnosis Safe) when PROXY_WALLET is set.
Falls back to polymarket-apis for EOA-only mode.

BTC 5-min markets are neg-risk: NegRiskAdapter.convertAndRedeemPositions.
Same approach as Polymarket-Bitcoin-Oracle-Latency-Arbitrage-Bot.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))

import config

logger = logging.getLogger(__name__)

_CTF_AVAILABLE: Optional[bool] = None


def _ctf_redeem_available() -> bool:
    global _CTF_AVAILABLE
    if _CTF_AVAILABLE is not None:
        return _CTF_AVAILABLE
    try:
        from ctf_redeem import redeem_winning_position
        _CTF_AVAILABLE = bool(config.PROXY_WALLET)
    except ImportError:
        _CTF_AVAILABLE = False
    return _CTF_AVAILABLE


def redeem_winning_position(
    condition_id: str,
    direction: str,
    num_tokens: float,
    neg_risk: bool = True,
) -> Optional[str]:
    """
    Redeem a winning position: convert outcome tokens to USDC.

    When PROXY_WALLET is set, uses lib/ctf_redeem (on-chain via Gnosis Safe).
    Otherwise uses polymarket-apis (EOA).
    BTC 5-min markets are neg-risk.
    """
    if not getattr(config, "AUTO_REDEEM_ENABLED", False):
        return None
    if not getattr(config, "REAL_TRADING", False):
        return None
    if num_tokens <= 0:
        return None

    if _ctf_redeem_available() and config.PROXY_WALLET:
        try:
            from ctf_redeem import redeem_winning_position as _ctf_redeem
            return _ctf_redeem(
                condition_id=condition_id,
                direction=direction,
                neg_risk=neg_risk,
                size_matched=num_tokens,
            )
        except Exception as e:
            logger.warning("ctf_redeem failed, falling back: %s", e)

    # Fallback: polymarket-apis (EOA)
    try:
        from polymarket_apis import PolymarketWeb3Client
        key = config.POLY_PRIVATE_KEY
        if not key:
            return None
        if not key.startswith("0x"):
            key = "0x" + key
        client = PolymarketWeb3Client(
            private_key=key,
            signature_type=getattr(config, "SIGNATURE_TYPE", 0),
        )
        amounts = [num_tokens, 0.0] if direction.upper() == "YES" else [0.0, num_tokens]
        result = client.redeem_position(
            condition_id=condition_id,
            amounts=amounts,
            neg_risk=neg_risk,
        )
        if result:
            logger.info("AUTO_REDEEM: Redeemed %s %.4f tokens. Tx: %s", direction, num_tokens, result)
            return result
    except ImportError:
        logger.warning("AUTO_REDEEM: polymarket-apis not installed")
    except Exception as e:
        logger.error("AUTO_REDEEM: redeem failed: %s", e)
    return None
