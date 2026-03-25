# Proxy Wallet Setup (Gnosis Safe)

Use this mode when your Polymarket account uses a **Gnosis Safe proxy** (like Polymarket-Bitcoin-Oracle-Latency-Arbitrage-Bot). The EOA signs orders; the Safe holds USDC.

## .env

```env
PRIVATE_KEY=your_eoa_private_key_no_0x
PROXY_WALLET=0xYourGnosisSafeAddress

# Optional: explicit API creds from Polymarket Profile -> API Keys
POLYMARKET_API_KEY=...
POLYMARKET_API_SECRET=...
POLYMARKET_API_PASSPHRASE=...
```

## Finding Your Proxy

If you created a Polymarket account, you likely have a Gnosis Safe. Query:

```
https://safe-transaction-polygon.safe.global/api/v1/owners/{YOUR_EOA_ADDRESS}/safes/
```

The returned address is your `PROXY_WALLET`.

## Requirements

- **USDC** in the proxy Safe (for trading)
- **POL** on the EOA signer (for gas)
- API creds: either set explicitly or derived from `PRIVATE_KEY` (run once to derive, then paste into .env)

## Switching Back to EOA

Clear `PROXY_WALLET` and use `POLY_PRIVATE_KEY` + `POLY_WALLET_ADDRESS` instead. The bot auto-detects mode from `.env`.
