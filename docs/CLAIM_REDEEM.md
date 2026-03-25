# Auto-Claiming / Redeeming Polymarket Earnings

When you win a Polymarket bet, outcome tokens resolve to $1 each. Redeem them to USDC for reinvestment.

## ✅ Implemented: lib/ctf_redeem (Proxy / Gnosis Safe)

Same approach as Polymarket-Bitcoin-Oracle-Latency-Arbitrage-Bot:

1. **Auto-redeem**: When a REAL trade wins, the bot calls `redeem_winning_position()` in `scripts/redeem.py`
2. **Proxy mode** (PROXY_WALLET): Uses `lib/ctf_redeem.py` — direct on-chain via NegRiskAdapter.convertAndRedeemPositions. EOA signs; Safe executes.
3. **EOA mode**: Falls back to polymarket-apis

**Config:** `AUTO_REDEEM_ENABLED = True` in `config.py` (default).

**Requirements:**
- `PROXY_WALLET` + `PRIVATE_KEY` (or `POLY_PRIVATE_KEY`) in `.env`
- POL on EOA for gas (execTransaction)
- web3, eth-account (from py-clob-client)

**Manual claim (unclaimed winnings):**

If the bot missed a resolution (restart/crash) or auto-claim failed:

```
.\claim_unclaimed.bat
# or
python scripts/claim_unclaimed.py
```

Uses Data API `GET /positions?user=<proxy>&redeemable=true`, then redeems each via `lib/ctf_redeem`.

---

## Manual Redeem on Polymarket

1. Go to [polymarket.com](https://polymarket.com) → Portfolio → Positions
2. For resolved winning positions, click **Claim** / **Redeem**

## Summary

| Method            | Wallet | Auto? | Notes                               |
|-------------------|--------|-------|-------------------------------------|
| lib/ctf_redeem    | Proxy  | Yes   | On-chain, EOA pays gas              |
| polymarket-apis   | EOA    | Yes   | Fallback when no PROXY_WALLET       |
| claim_unclaimed.py| Proxy  | No    | One-shot manual recovery            |
| polymarket.com UI | Any    | No    | Manual claim                        |
