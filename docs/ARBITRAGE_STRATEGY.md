# Polymarket vs Options Arbitrage Strategy

## Overview

This document describes a **separate strategy** from the main bot (which trades BTC 5-minute direction using momentum/contrarian signals). Here we compare **Polymarket binary prices** with **option-implied risk-neutral probabilities** to find mispricings and arbitrage opportunities.

- **Polymarket**: crowd expectations, often thin liquidity; retail-dominated.
- **Listed options**: institutional pricing, risk-neutral probabilities from IV and term structure.
- **Arbitrage**: when the two disagree, you can trade the cheap side on Polymarket and hedge with options (or take a view that the option-implied fair value is more accurate).

## Role (Quantitative Analyst)

Act as a **quantitative derivatives analyst** who:

1. Compares Polymarket binary markets with probabilities implied by listed options.
2. Produces rigorous financial analysis and a **simple, intuitive explanation** (e.g. high-school level).

## Task

Given a **Polymarket contract** and **listed option-market data**:

1. **Compute** the risk-neutral probability of the Polymarket event using:
   - Implied volatility from options
   - Barrier or digital option mathematics (depending on event type)
   - Correct discounting
2. **Determine** whether the Polymarket "Yes" price is **cheap**, **fair**, or **expensive** relative to option-implied fair value.
3. **Output**:
   - Technical version: institutional-grade quant derivation.
   - Layman version: plain-English explanation.

## Inputs Required

| Source | What |
|--------|------|
| **Polymarket** | Event details: question, resolution rule, expiry, current Yes/No prices, barrier/strike if applicable. |
| **Market data** | Spot price of the underlying; option chain or ATM IV, IV for relevant strike region, IV skew, IV term structure. |
| **Rates** | Risk-free rate; dividend yield (if any). |
| **Derived** | Time to expiry (years); barrier or strike level. |

## Event Types (Critical)

- **Touch event** → use **barrier-hitting probability** (reflection principle).
- **Finish above/below at expiry** → use **digital probability** (lognormal tail from Black–Scholes).

Always differentiate between these; the math differs.

## Output Structure (Seven Sections)

1. **Model choice** – Barrier-hitting vs terminal digital; brief justification.
2. **Full mathematical derivation** – Step-by-step: logs, μ = r − q − ½σ², d-values, p_terminal or p_hit, discounted fair value; sensitivity to IV (±2–3 vol points).
3. **Fair value vs Polymarket value** – Option-implied fair Yes% vs current Polymarket Yes price; mispricing size.
4. **Verdict** – Cheap / Fair / Expensive (and why).
5. **Technical explanation** – Professional quant summary.
6. **Layman explanation** – No jargon; high-school level.
7. **Final takeaway in one sentence** – e.g. *"Based on option prices, the market thinks this event has a 12% chance, but Polymarket prices it at 18%, so it looks expensive."*

## Rules for Consistency

- Always differentiate **touch** vs **settle-at-expiry** events.
- Always use **risk-neutral pricing**, not personal forecasting.
- Derive volatility from the **appropriate strike region**, not just ATM IV.
- If Polymarket expiry ≠ nearest listed option expiry, adjust via **term-structure interpolation**.
- If inputs are missing, **request them first**.
- Always give a **cheap / fair / expensive** verdict.
- All math must be consistent with **risk-neutral pricing theory**.

## How This Fits the Repo

- **Current bot** (`bot.py`, `strategy.py`, etc.): trades **BTC 5-minute** markets only; no options data; no arbitrage.
- **Arbitrage workflow**: New module under `arbitrage/` (and optional OpenClaw / Claude integration) that:
  - Fetches or accepts Polymarket event details (e.g. finance/stocks).
  - Accepts or prompts for option inputs (spot, IV, strike/barrier, expiry, rate).
  - Builds the structured prompt and either generates a **brief for OpenClaw** or calls the **Anthropic API** if configured.
  - Uses the same Polymarket client (Gamma for market discovery, CLOB for prices) where applicable.

See `arbitrage/README.md` and the script `run_arbitrage_check.py` for usage.

## Reference

- YouTube: *I Built a ChatGPT Prompt That Finds Mispriced Polymarket Bets* (transcript in repo).
- Spec: Role, Task, Inputs, Output, and Rules as in the attached project brief (Polymarket prices, event types, math derivation).
