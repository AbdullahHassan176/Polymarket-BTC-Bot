"""Quick report with fewer search queries to complete in ~60s."""
import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Temporarily reduce search list for quick report
import config
original = config.ARBITRAGE_SEARCH_QUERIES
config.ARBITRAGE_SEARCH_QUERIES = ["Amazon hit", "NVIDIA finish", "Google hit", "Tesla finish", "Apple finish"]

from polymarket_client import PolymarketClient
from arbitrage.run_arbitrage_loop import _run_report

if __name__ == "__main__":
    client = PolymarketClient()
    _run_report(client, max_candidates=80)
