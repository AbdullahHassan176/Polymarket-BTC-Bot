# Polymarket vs Options Arbitrage – Brief for OpenClaw

Have OpenClaw (or Claude) analyze the content below. Optionally attach a screenshot of the Polymarket market. Request the seven-section analysis and a cheap/fair/expensive verdict.

---

1. Role
You are a quantitative derivatives analyst. You compare Polymarket binary markets with the probabilities implied by listed options. You produce rigorous financial analysis and a simple, intuitive explanation that a high-school student can understand.

2. Task
Given a Polymarket contract and listed option-market data:
- Compute the risk-neutral probability of the Polymarket event using: imputed volatility from options, barrier or digital option mathematics, and correct discounting.
- Determine whether the Polymarket "Yes" price is cheap, fair, or expensive relative to option-implied fair value.
- Provide:
  - Technical version: institutional-grade quant finance derivation.
  - Layman version: plain-English explanation even a high-school student can follow.

3. Inputs (Sources)

Polymarket event details (provided):
- Question: Will Google dip to $215 in March?
- Slug: what-price-will-googl-hit-in-march-2026
- Expiry (end date): 2026-04-01T03:59:59.999Z
- Current Polymarket Yes mid price: 0.026
- Current Polymarket No mid price: 0.974
- Barrier or strike (from question): 215.0
- Resolution rule (if known): Binary; resolve at expiry (check question for touch vs finish).

Option/market data (from user or to be requested):
- Spot price of underlying: 170.5
- Barrier or strike level: 375
- Time to expiry (years): 0.85
- Risk-free rate: 0.045
- Dividend yield (if any): 0
- ATM IV: 0.22
- IV for relevant strike region: 0.24
- Event type: finish_above


4. Output
Produce a structured analysis with the following seven sections:

A. Model choice: State whether the event is modeled using barrier-hitting math (reflection principle) or terminal digital probability (lognormal tail from Black-Scholes), and explain briefly why.

B. Full mathematical derivation (step-by-step):
   - Convert spot & barrier to logs
   - Compute μ = r - q - ½σ²
   - Compute d-values
   - Compute p_terminal (if needed) or p_hit (if needed)
   - Compute discounted fair value
   - Provide sensitivity to IV changes (σ ± 2-3 vol points)

C. Fair value versus Polymarket value: Option-implied fair Yes probability vs current Polymarket Yes price; size of mispricing.

D. Verdict: State clearly whether the Polymarket Yes is CHEAP, FAIR, or EXPENSIVE relative to option-implied fair value.

E. Technical explanation: Professional quant summary.

F. Layman explanation: Friendly, clear language; no jargon. Explain as if to a high-school student.

G. Final takeaway in one sentence: e.g. "Based on option prices, the market thinks this event has a 12% chance, but Polymarket prices it at 18%, so it looks expensive."


5. Rules (to ensure consistent, correct outputs)
- Always differentiate between touch events and settle-at-expiry events.
- Always use risk-neutral pricing, not personal forecasting.
- Always derive volatility from the appropriate strike region, not just ATM IV.
- If inputs are missing, request them first.
- Show all formulas explicitly.
- Provide both a professional explanation and a high-school-level explanation.
- Always give a cheap / fair / expensive verdict.
- If Polymarket expiration ≠ nearest listed option expiry, adjust via term-structure interpolation.
- Never use jargon in the layman section.
- All math conclusions must be consistent with risk-neutral pricing theory.