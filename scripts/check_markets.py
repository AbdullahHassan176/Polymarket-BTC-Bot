"""Diagnostic: inspect public search results for BTC Up or Down events."""
import requests, json

resp = requests.get(
    "https://gamma-api.polymarket.com/public-search",
    params={"q": "Bitcoin Up or Down", "limit_per_type": 10},
    timeout=10,
)
data = resp.json()
events = data.get("events", [])
print(f"Found {len(events)} events:")
for e in events:
    title = e.get("title", e.get("question", ""))
    slug  = e.get("slug", "")
    print(f"  {title[:80]} | slug={slug}")
    # Show the nested markets if any
    inner = e.get("markets", [])
    for m in inner[:3]:
        print(f"    market: {m.get('question','')[:70]} | closed={m.get('closed')} | active={m.get('active')}")
        ids = m.get("clobTokenIds", [])
        if ids:
            print(f"    YES token: {ids[0][:20]}...")
