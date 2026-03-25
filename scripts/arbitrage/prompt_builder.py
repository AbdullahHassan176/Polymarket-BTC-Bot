"""
arbitrage/prompt_builder.py – Build the structured prompt for Polymarket vs options arbitrage.

Follows the spec: Role, Task, Inputs, Output (seven sections), and Rules.
Used by run_arbitrage_check to generate a brief for OpenClaw or Claude API.
"""

from typing import Any, Optional


def build_arbitrage_prompt(
    polymarket_context: dict,
    options_inputs: Optional[dict] = None,
) -> str:
    """
    Build the full prompt text for the quant analyst (OpenClaw / Claude).

    polymarket_context: dict with at least:
      - question, slug, end_date_iso, yes_mid, no_mid,
      - barrier_or_strike (if derivable from question), outcome (e.g. "YES = hit $375")
    options_inputs: optional dict with spot, barrier_or_strike, time_to_expiry_years,
      risk_free_rate, dividend_yield, atm_iv, event_type, iv_at_strike, etc.
    """
    pm = polymarket_context
    opt = options_inputs or {}

    # ----- Role -----
    role = """1. Role
You are a quantitative derivatives analyst. You compare Polymarket binary markets with the probabilities implied by listed options. You produce rigorous financial analysis and a simple, intuitive explanation that a high-school student can understand."""

    # ----- Task -----
    task = """2. Task
Given a Polymarket contract and listed option-market data:
- Compute the risk-neutral probability of the Polymarket event using: imputed volatility from options, barrier or digital option mathematics, and correct discounting.
- Determine whether the Polymarket "Yes" price is cheap, fair, or expensive relative to option-implied fair value.
- Provide:
  - Technical version: institutional-grade quant finance derivation.
  - Layman version: plain-English explanation even a high-school student can follow."""

    # ----- Inputs -----
    inputs_section = """3. Inputs (Sources)

Polymarket event details (provided):
"""
    inputs_section += f"- Question: {pm.get('question', 'N/A')}\n"
    inputs_section += f"- Slug: {pm.get('slug', 'N/A')}\n"
    inputs_section += f"- Expiry (end date): {pm.get('end_date_iso', 'N/A')}\n"
    inputs_section += f"- Current Polymarket Yes mid price: {pm.get('yes_mid')}\n"
    inputs_section += f"- Current Polymarket No mid price: {pm.get('no_mid')}\n"
    if pm.get("barrier_or_strike") is not None:
        inputs_section += f"- Barrier or strike (from question): {pm['barrier_or_strike']}\n"
    inputs_section += f"- Resolution rule (if known): {pm.get('resolution_rule', 'Binary: Yes/No at expiry.')}\n"

    inputs_section += "\nOption/market data (from user or to be requested):\n"
    if opt:
        inputs_section += f"- Spot price of underlying: {opt.get('spot', 'N/A')}\n"
        inputs_section += f"- Barrier or strike level: {opt.get('barrier_or_strike', 'N/A')}\n"
        inputs_section += f"- Time to expiry (years): {opt.get('time_to_expiry_years', 'N/A')}\n"
        inputs_section += f"- Risk-free rate: {opt.get('risk_free_rate', 'N/A')}\n"
        inputs_section += f"- Dividend yield (if any): {opt.get('dividend_yield', 0)}\n"
        inputs_section += f"- ATM IV: {opt.get('atm_iv', 'N/A')}\n"
        inputs_section += f"- IV for relevant strike region: {opt.get('iv_at_strike', 'N/A')}\n"
        inputs_section += f"- Event type: {opt.get('event_type', 'finish_above or touch')}\n"
    else:
        inputs_section += """- Spot price of the underlying: [REQUEST IF NOT PROVIDED]
- Option chain or: ATM IV, IV for relevant strike region, IV skew, IV term structure
- Risk-free rate: [REQUEST IF NOT PROVIDED]
- Dividend yield (if any): [OPTIONAL]
- Derived: Time to expiry (in years), Barrier or strike level
- Choice of event type: Touch event → barrier-hitting probability; Finish above/below → digital probability
"""

    # ----- Output -----
    output_section = """4. Output
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
"""

    # ----- Rules -----
    rules = """5. Rules (to ensure consistent, correct outputs)
- Always differentiate between touch events and settle-at-expiry events.
- Always use risk-neutral pricing, not personal forecasting.
- Always derive volatility from the appropriate strike region, not just ATM IV.
- If inputs are missing, request them first.
- Show all formulas explicitly.
- Provide both a professional explanation and a high-school-level explanation.
- Always give a cheap / fair / expensive verdict.
- If Polymarket expiration ≠ nearest listed option expiry, adjust via term-structure interpolation.
- Never use jargon in the layman section.
- All math conclusions must be consistent with risk-neutral pricing theory."""

    parts = [role, task, inputs_section, output_section, rules]
    return "\n\n".join(parts)


def format_polymarket_context(
    event: dict,
    market: dict,
    yes_mid: Optional[float],
    no_mid: Optional[float],
) -> dict:
    """
    Build polymarket_context dict from Gamma event + market + CLOB mid prices.
    Extracts barrier/strike from question if possible (e.g. "$375" in "Will Google hit $375 by...").
    """
    import re
    question = (market.get("question") or event.get("title", ""))
    end_date = None
    for key in ("endDate", "endDateIso", "end_date"):
        v = market.get(key)
        if v:
            end_date = str(v)
            break
    if not end_date and event.get("endDate"):
        end_date = str(event.get("endDate"))

    # Try to extract a number that looks like strike/barrier (e.g. $375, 375, 70k, 70000)
    barrier = None
    if question:
        # $375 or $70,000 or 70k
        m = re.search(r"\$?\s*([\d,]+(?:\.[\d]+)?)\s*k?", question, re.IGNORECASE)
        if m:
            s = m.group(1).replace(",", "")
            if "k" in question.lower() and m.group(0).lower().endswith("k"):
                barrier = float(s) * 1000
            else:
                try:
                    barrier = float(s)
                except ValueError:
                    pass

    return {
        "question": question,
        "slug": event.get("slug", ""),
        "end_date_iso": end_date or "N/A",
        "yes_mid": yes_mid,
        "no_mid": no_mid,
        "barrier_or_strike": barrier,
        "resolution_rule": "Binary; resolve at expiry (check question for touch vs finish).",
    }
