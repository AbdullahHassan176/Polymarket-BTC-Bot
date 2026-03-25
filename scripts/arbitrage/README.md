# Polymarket vs Options Arbitrage

This module helps you find **mispriced Polymarket binary markets** by comparing them to **option-implied risk-neutral probabilities**. Two modes:

1. **Automated loop** – No LLM. Uses Black-Scholes math (Python) to compute fair value, auto-trades when cheap, auto-redeems on win.
2. **Brief for OpenClaw** – Generates a structured prompt for manual or API-driven analysis.

## Automated Loop (No LLM)

```bash
# Paper trade – one cycle
python -m arbitrage.run_arbitrage_loop --paper --once

# Paper trade – continuous (every 5 min)
python -m arbitrage.run_arbitrage_loop --paper --interval 300

# Real trading (REAL_TRADING=True in config)
python -m arbitrage.run_arbitrage_loop --interval 300
```

The loop: discovers price-target markets → fetches spot/vol (yfinance, OKX) → computes Black-Scholes fair value → buys Yes when Polymarket < fair - edge → monitors resolution → auto-redeems on win. Requires `yfinance` for stocks.

## Brief for OpenClaw

1. **Fetch Polymarket event** – By slug (e.g. `google-2026-375`) or search (e.g. "Google 2026").
2. **Gather inputs** – Polymarket question, expiry, Yes/No prices, barrier/strike; optionally option data (spot, IV, rate, etc.) from a JSON file or env.
3. **Build brief** – Structured prompt with Role, Task, Inputs, and required Output (seven sections, cheap/fair/expensive verdict).
4. **Run analysis** – Either:
   - **OpenClaw**: script writes `arbitrage_brief.md`; have OpenClaw run the script (or read the brief) and use its LLM to perform the analysis.
   - **API**: if `ANTHROPIC_API_KEY` is set, script calls Claude directly and prints/saves the response.

## Usage

```bash
# From repo root (so polymarket_client and config can be imported)
cd d:\Experimentation\Clauwde-Polymarket-BTC-Bot
python -m arbitrage.run_arbitrage_check --slug "google-2026-375"
# or
python -m arbitrage.run_arbitrage_check --search "Google 2026"
# Optional: pass option inputs (spot, IV, strike, expiry, rate)
python -m arbitrage.run_arbitrage_check --slug "google-2026-375" --options options_example.json
# Write brief to file for OpenClaw / Claude (default)
python -m arbitrage.run_arbitrage_check --slug "google-2026-375" --out brief.md
# Call Claude API if ANTHROPIC_API_KEY is in .env
python -m arbitrage.run_arbitrage_check --slug "google-2026-375" --api
```

## Option inputs JSON

Example `options_example.json`:

```json
{
  "spot": 170.50,
  "barrier_or_strike": 375,
  "time_to_expiry_years": 0.85,
  "risk_free_rate": 0.045,
  "dividend_yield": 0,
  "atm_iv": 0.22,
  "event_type": "finish_above",
  "iv_at_strike": 0.24,
  "comment": "Optional: IV for the relevant strike region"
}
```

- `event_type`: `"touch"` (barrier-hitting) or `"finish_above"` / `"finish_below"` (digital).
- If you omit option data, the brief still lists required inputs so you (or Claude) can request or fill them.

## Output

- **Brief file** – Markdown with Polymarket context + option inputs + instructions for the seven-section analysis and cheap/fair/expensive verdict.
- **API mode** – Same prompt sent to Claude; response printed and optionally saved to `arbitrage_response.md`.

## Using with OpenClaw

OpenClaw runs on your machine and can execute scripts, read files, and use Claude. To integrate:

- **Manual**: Ask OpenClaw to run `python -m arbitrage.run_arbitrage_check --search "Google 2026"` and then read `arbitrage_brief.md` to perform the quant analysis.
- **Proactive**: Add a cron/skill so OpenClaw periodically checks your target markets and reports cheap/expensive verdicts.
- **API mode**: If you set `ANTHROPIC_API_KEY`, OpenClaw can run the script with `--api` and get the analysis directly.

See [openclaw.ai](https://openclaw.ai/) for setup and skill docs.

## Report mode (no trading)

```bash
python -m arbitrage.run_arbitrage_loop --report
```

Scans all search queries, evaluates each market with Black-Scholes fair value, and prints a table of CHEAP / FAIR / EXPENSIVE plus potential profit if all CHEAP bets win. Saves `logs/arbitrage_report.csv`.

## Paper trading with balance, TP and SL

```bash
# Start with $50, run for 3 hours (10800 sec), check every 2 min. Take profit at 85c, stop loss at 25c.
python -m arbitrage.run_arbitrage_loop --paper --paper-start 50 --duration 10800 --interval 120
```

At the end you get: **Starting balance**, **Ending balance**, **PnL**. Config: `ARBITRAGE_PAPER_STARTING_BALANCE_USDC`, `ARBITRAGE_PAPER_TAKE_PROFIT` (0.85), `ARBITRAGE_PAPER_STOP_LOSS` (0.25).

## Deploying to live trading

1. **Install deps**: `pip install -r requirements.txt` (includes `py-clob-client` for order placement).
2. **.env**: Set `POLY_PRIVATE_KEY` and `POLY_WALLET_ADDRESS`; fund wallet with USDC.e and POL on Polygon.
3. **Config**: In `config.py` set `REAL_TRADING=True`, `AUTO_REDEEM_ENABLED=True`; tune `ARBITRAGE_RISK_PER_TRADE_USDC`, `ARBITRAGE_MIN_EDGE`, `ARBITRAGE_MAX_OPEN_POSITIONS`.
4. **Run report first**: `python -m arbitrage.run_arbitrage_loop --report` to confirm CHEAP/FAIR/EXPENSIVE and edge sizes.
5. **Paper test**: `python -m arbitrage.run_arbitrage_loop --paper --once` then `--paper` (continuous) to verify loop and resolution handling.
6. **Live**: `python -m arbitrage.run_arbitrage_loop --interval 300` (no `--paper`). Monitor `state_arbitrage.json` and logs; wins are auto-redeemed if `AUTO_REDEEM_ENABLED=True`.

**Risks**: Fair value uses historical vol and spot from yfinance/OKX; real options IV can differ. Only risk capital you can afford to lose.

## See also

- `docs/ARBITRAGE_STRATEGY.md` – Full strategy, rules, and event types.
- Transcript: *I Built a ChatGPT Prompt That Finds Mispriced Polymarket Bets* (YouTube).
