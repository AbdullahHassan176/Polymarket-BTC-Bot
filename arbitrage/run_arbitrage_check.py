"""
Run an arbitrage check: fetch Polymarket event, build structured brief for OpenClaw,
optionally call Anthropic API.

Usage (from repo root):
  python -m arbitrage.run_arbitrage_check --slug "google-2026-375"
  python -m arbitrage.run_arbitrage_check --search "Google 2026" --out brief.md
  python -m arbitrage.run_arbitrage_check --slug "google-2026-375" --options options.json --api
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Ensure repo root is on path when run as python -m arbitrage.run_arbitrage_check
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import config
from polymarket_client import PolymarketClient
from .prompt_builder import build_arbitrage_prompt, format_polymarket_context

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_first_market_with_tokens(event: dict, client: PolymarketClient, open_only: bool = False):
    """From Gamma event, pick first market (optionally open only) with valid CLOB token IDs."""
    for m in event.get("markets", []):
        if open_only and m.get("closed"):
            continue
        ids = PolymarketClient._parse_clob_token_ids(m.get("clobTokenIds"))
        if len(ids) >= 2:
            return m, ids[0], ids[1]
    return None, None, None


def _load_options(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Polymarket vs options arbitrage brief for OpenClaw / Claude."
    )
    parser.add_argument("--slug", type=str, help="Polymarket event slug (e.g. google-2026-375)")
    parser.add_argument("--search", type=str, help="Search query (e.g. 'Google 2026')")
    parser.add_argument("--options", type=str, help="Path to JSON file with option inputs (spot, IV, strike, etc.)")
    parser.add_argument("--out", type=str, default="arbitrage_brief.md", help="Output path for brief (default: arbitrage_brief.md)")
    parser.add_argument("--api", action="store_true", help="Call Anthropic API (requires ANTHROPIC_API_KEY in .env)")
    parser.add_argument("--save-response", type=str, default="", help="If --api, save response to this file (e.g. arbitrage_response.md)")
    args = parser.parse_args()

    if not args.slug and not args.search:
        parser.error("Provide either --slug or --search")

    client = PolymarketClient()
    event = None
    market = None
    yes_token_id = no_token_id = None

    if args.slug:
        event = client.get_event_by_slug(args.slug)
        if event:
            market, yes_token_id, no_token_id = _get_first_market_with_tokens(event, client, open_only=False)
    if (not event or not market) and args.search:
        events = client.search_events(args.search, limit_per_type=10)
        for ev in events:
            event = ev
            market, yes_token_id, no_token_id = _get_first_market_with_tokens(ev, client, open_only=False)
            if market:
                break

    if not event or not market:
        logger.error("No event/market found for slug=%s search=%s", args.slug, args.search)
        sys.exit(1)

    if market.get("closed"):
        logger.warning("Market is closed; prices may be stale or N/A. Brief will still be generated.")

    yes_mid = client.get_mid_price(yes_token_id) if yes_token_id else None
    no_mid = client.get_mid_price(no_token_id) if no_token_id else None
    polymarket_context = format_polymarket_context(event, market, yes_mid, no_mid)

    options_inputs = None
    if args.options and os.path.isfile(args.options):
        options_inputs = _load_options(args.options)
        logger.info("Loaded option inputs from %s", args.options)

    prompt = build_arbitrage_prompt(polymarket_context, options_inputs)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = _REPO_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Polymarket vs Options Arbitrage – Brief for OpenClaw\n\n")
        f.write("Have OpenClaw (or Claude) analyze the content below. Optionally attach a screenshot of the Polymarket market. ")
        f.write("Request the seven-section analysis and a cheap/fair/expensive verdict.\n\n---\n\n")
        f.write(prompt)
    logger.info("Wrote brief to %s", out_path)

    if args.api:
        api_key = config.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY not set. Add to .env or config for --api.")
            sys.exit(1)
        try:
            import anthropic
        except ImportError:
            logger.error("anthropic package not installed. Run: pip install anthropic")
            sys.exit(1)
        client_api = anthropic.Anthropic(api_key=api_key)
        message = client_api.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for b in message.content:
            if hasattr(b, "text"):
                text += b.text
        print("\n--- Claude response ---\n")
        print(text)
        if args.save_response:
            resp_path = Path(args.save_response)
            if not resp_path.is_absolute():
                resp_path = _REPO_ROOT / resp_path
            resp_path.write_text(text, encoding="utf-8")
            logger.info("Saved response to %s", resp_path)


if __name__ == "__main__":
    main()
