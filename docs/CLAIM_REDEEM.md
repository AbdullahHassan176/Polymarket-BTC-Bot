# Auto-Claiming / Redeeming Polymarket Earnings

When you win a Polymarket bet, the outcome tokens resolve to $1 each. To get USDC back into your wallet for reinvestment, you need to **redeem** (claim) those positions.

## ✅ Implemented: polymarket-apis (Option 1)

This bot uses **polymarket-apis** for auto-redeem. When a **REAL** trade wins:

1. After recording the outcome, the bot calls `redeem_winning_position()` in `redeem.py`
2. Uses `PolymarketWeb3Client.redeem_position()` to convert winning tokens to USDC
3. USDC is returned to your wallet for reinvestment

**Config:** `AUTO_REDEEM_ENABLED = True` in `config.py` (default).

**Requirements:**
- `pip install polymarket-apis` (included in requirements.txt)
- Python 3.12+ for polymarket-apis
- POL in your wallet for gas (EOA)

**Disable:** Set `AUTO_REDEEM_ENABLED = False` to claim manually.

---

## Other Options (if polymarket-apis unavailable)

### Manual Redeem on Polymarket

1. Go to [polymarket.com](https://polymarket.com) and connect your wallet.
2. Open **Portfolio** → **Positions**.
3. For resolved winning positions, click **Redeem** or **Claim**.

### Polymarket Relayer (Safe / gasless)

For Safe wallets: run your own [relayer client](https://docs.polymarket.com/developers/builders/relayer-client) to execute redemptions without paying gas.

### Future: py-clob-client

When `py-clob-client` adds an official redeem endpoint, we may switch to it. Until then, polymarket-apis handles auto-redeem.

## Summary

| Method                 | Wallet | Auto? | Notes                        |
|------------------------|--------|-------|------------------------------|
| polymarket-apis        | EOA    | Yes   | POL for gas                  |
| polymarket-apis        | Magic/Safe | Yes | Gasless possible             |
| Manual on Polymarket UI| Any    | No    | Easiest for small volume     |
| Relayer                | Safe   | Yes   | Run your own relayer         |
