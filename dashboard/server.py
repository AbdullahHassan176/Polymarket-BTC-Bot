"""
Dashboard API server — serves BTC 5-min bot status, trades, and analytics.

Run from repo root: python dashboard/server.py
Then open http://localhost:8765
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRADES_CSV = ROOT / "logs" / "trades.csv"
SESSIONS_CSV = ROOT / "logs" / "btc_sessions.csv"
STATE_FILE = ROOT / "state.json"
BOT_PID = ROOT / "btc_bot.pid"
WDOG_PID = ROOT / "watchdog_btc.pid"


def _is_process_running(pid_path: Path) -> bool:
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return False
    if sys.platform == "win32":
        try:
            import subprocess
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return r.returncode == 0 and str(pid) in (r.stdout or "")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, PermissionError):
        return False


def _first_open_position(state: dict) -> dict | None:
    """Return first open position (for backward compat). open_positions is {cid: pos}."""
    positions = state.get("open_positions") or {}
    if isinstance(positions, dict):
        for pos in positions.values():
            if isinstance(pos, dict) and pos.get("open", True) is not False:
                return pos
    return state.get("open_position")


def _ts_cmp(ts: str, since: str | None, until: str | None) -> bool:
    if not ts or (not since and not until):
        return True
    t = (ts or "").strip().replace("T", " ")[:19]
    if since:
        s = since.replace("T", " ").strip()[:19]
        if t < s:
            return False
    if until:
        u = until.replace("T", " ").strip()[:19]
        if t > u:
            return False
    return True


def _normalize_trade(row: dict) -> dict:
    """Map BTC bot trade row to Oracle-style format for dashboard."""
    outcome = (row.get("outcome") or "").strip().upper()
    if outcome in ("WIN", "TP"):
        won = "win"
    elif outcome == "LOSS":
        won = "loss"
    else:
        won = "?"
    pnl = row.get("pnl_usdc")
    try:
        profit = float(str(pnl or "0").replace("$", "").replace(",", "").strip()) if pnl else 0.0
    except (TypeError, ValueError):
        profit = 0.0
    try:
        bet = float(str(row.get("size_usdc") or "0").replace(",", "").strip())
    except (TypeError, ValueError):
        bet = 0.0
    return {
        "timestamp": row.get("timestamp"),
        "asset": "BTC",
        "direction": (row.get("direction") or "").upper(),
        "outcome": outcome or "—",
        "bet_usdc": bet,
        "profit": round(profit, 2),
        "won": won,
        "strategy_tier": row.get("strategy_tier", ""),
        "mode": row.get("mode", ""),
    }


# Dashboard shows only LIVE (REAL) trades. Set env DASHBOARD_LIVE_ONLY=0 to show all (paper+real).
LIVE_ONLY = os.environ.get("DASHBOARD_LIVE_ONLY", "1").strip() in ("1", "true", "yes")


def load_trades(since: str | None = None, until: str | None = None, live_only: bool = LIVE_ONLY) -> list[dict]:
    if not TRADES_CSV.exists():
        return []
    rows = []
    with open(TRADES_CSV, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            ts = row.get("timestamp") or ""
            if not ts:
                continue
            if live_only:
                mode = (row.get("mode") or "").strip().upper()
                if mode != "REAL":
                    continue
            if _ts_cmp(ts, since, until):
                rows.append(_normalize_trade(row))
    return rows


def get_bot_status() -> dict:
    wdog_ok = _is_process_running(WDOG_PID)
    bot_ok = _is_process_running(BOT_PID)
    active = wdog_ok and bot_ok
    strategy = "BTC 5-min reversal/momentum/contrarian (Polymarket)"
    return {
        "watchdog_running": wdog_ok,
        "bot_running": bot_ok,
        "active_bot": "Running" if active else "Stopped",
        "strategy": strategy,
    }


def get_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_analytics(trades: list[dict]) -> dict:
    resolved = [r for r in trades if (r.get("won") or "").lower() in ("win", "loss")]
    pending = [r for r in trades if (r.get("won") or r.get("outcome") or "").lower() in ("?", "pending", "")]

    def _f(v):
        try:
            s = str(v or "0").replace("$", "").replace(",", "").strip()
            return float(s) if s else 0.0
        except (TypeError, ValueError):
            return 0.0

    total_pnl = sum(_f(r.get("profit")) for r in resolved)
    total_wagered = sum(_f(r.get("bet_usdc")) for r in resolved)
    wins = sum(1 for r in resolved if (r.get("won") or "").lower() == "win")
    losses = len(resolved) - wins
    win_rate = (wins / len(resolved) * 100) if resolved else 0

    # Single asset (BTC) for this bot
    btc_pnl = total_pnl
    btc_wins = wins
    btc_losses = losses
    btc_wagered = total_wagered
    per_asset = []
    if resolved:
        per_asset = [{
            "asset": "BTC",
            "wins": btc_wins,
            "losses": btc_losses,
            "win_rate": round((btc_wins / len(resolved) * 100), 1) if resolved else 0,
            "pnl": round(btc_pnl, 2),
            "wagered": round(btc_wagered, 2),
        }]

    def _safe_num(x):
        try:
            f = float(x)
            return 0.0 if (math.isnan(f) or math.isinf(f)) else f
        except (TypeError, ValueError):
            return 0.0

    return {
        "total_pnl": round(_safe_num(total_pnl), 2),
        "total_wagered": round(_safe_num(total_wagered), 2),
        "wins": wins,
        "losses": losses,
        "win_rate": round(_safe_num(win_rate), 1),
        "resolved_count": len(resolved),
        "pending_count": len(pending),
        "per_asset": per_asset,
    }


def _trades_since_date(trades: list[dict], date_str: str | None) -> list[dict]:
    """Filter trades to those on or after date_str (YYYY-MM-DD)."""
    if not date_str or not date_str.strip():
        return trades
    cut = (date_str or "").strip()[:10]
    return [t for t in trades if (t.get("timestamp") or "")[:10] >= cut]


def get_current_run() -> dict | None:
    """When bot is running, return current run info from state.json + live-only trades."""
    status = get_bot_status()
    if not (status.get("watchdog_running") and status.get("bot_running")):
        return None
    state = get_state()
    all_trades = load_trades()  # live_only=True by default
    date_str = (state.get("last_reset_date") or "").strip()
    trades = _trades_since_date(all_trades, date_str) if date_str else all_trades
    analytics = get_analytics(trades)
    # Use analytics from live trades so dashboard matches live-only data
    start_bal = float(state.get("starting_balance_usdc") or 0)
    cum_pnl = analytics.get("total_pnl", 0)
    return {
        "in_progress": True,
        "run_id": "current",
        "started": state.get("last_reset_date"),
        "stopped": None,
        "state": {**state, "cumulative_pnl_usdc": cum_pnl, "daily_pnl_usdc": cum_pnl, "daily_trades": analytics.get("resolved_count", 0)},
        "trades": trades,
        "analytics": analytics,
        "daily_trades": analytics.get("resolved_count", 0),
        "daily_pnl_usdc": cum_pnl,
        "cumulative_pnl_usdc": cum_pnl,
        "starting_balance_usdc": start_bal,
        "open_position": _first_open_position(state),
        "open_positions": state.get("open_positions") or {},
        "last_signal": state.get("last_signal"),
    }


# Show ZERO completed sessions from file — only the "current run" when bot is running.
# This avoids showing hundreds of 0h00m watchdog restarts as separate sessions.
MAX_COMPLETED_SESSIONS = 0


def get_sessions(since: str | None = None, until: str | None = None) -> list[dict]:
    sessions = []
    if not SESSIONS_CSV.exists():
        return []
    all_trades = load_trades()  # live only
    with open(SESSIONS_CSV, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    rows.sort(key=lambda r: r.get("started", ""), reverse=True)
    for r in rows[:MAX_COMPLETED_SESSIONS]:
        started = (r.get("started") or "").strip().replace("T", " ")
        stopped = (r.get("stopped") or "").strip().replace("T", " ")
        session_id = f"session-{r.get('session', '')}"
        if not started:
            continue
        # Trades in this session: started <= ts <= stopped
        sess_trades = []
        for t in all_trades:
            ts = (t.get("timestamp") or "").replace("T", " ").strip()[:19]
            if not ts:
                continue
            if ts >= started and (not stopped or ts <= stopped):
                sess_trades.append(t)
        resolved = [t for t in sess_trades if (t.get("won") or "").lower() in ("win", "loss")]
        wins = sum(1 for t in resolved if (t.get("won") or "").lower() == "win")
        pnl = sum(float(str(t.get("profit") or "0").replace(",", "")) for t in resolved)
        wagered = sum(float(str(t.get("bet_usdc") or "0").replace(",", "")) for t in resolved)
        if _ts_cmp(started, since, until):
            sessions.append({
                "run_id": session_id,
                "bot_type": "BTC 5m",
                "started": r.get("started", started),
                "stopped": r.get("stopped", stopped),
                "trades": len(resolved),
                "wins": wins,
                "losses": len(resolved) - wins,
                "pending": len([t for t in sess_trades if (t.get("won") or "").lower() not in ("win", "loss")]),
                "pnl": round(pnl, 2),
                "wagered": round(wagered, 2),
            })
    return sessions


def main() -> None:
    try:
        import aiohttp
        from aiohttp import web
    except ImportError:
        print("Install aiohttp: pip install aiohttp")
        sys.exit(1)

    INDEX_HTML = Path(__file__).parent / "index.html"
    SESSIONS_HTML = Path(__file__).parent / "sessions.html"

    async def serve_index(_request: web.Request) -> web.Response:
        html = INDEX_HTML.read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

    async def api_status(_request: web.Request) -> web.Response:
        return web.json_response(get_bot_status())

    def _filter_params(request: web.Request) -> tuple[str | None, str | None]:
        q = request.rel_url.query
        since = (q.get("since") or "").strip() or None
        until = (q.get("until") or "").strip() or None
        return (since, until)

    async def api_trades(request: web.Request) -> web.Response:
        since, until = _filter_params(request)
        trades = load_trades(since=since, until=until)
        return web.json_response({"trades": trades})

    async def api_analytics(request: web.Request) -> web.Response:
        since, until = _filter_params(request)
        trades = load_trades(since=since, until=until)
        return web.json_response(get_analytics(trades))

    async def api_all(request: web.Request) -> web.Response:
        try:
            since, until = _filter_params(request)
            trades = load_trades(since=since, until=until)
            status = get_bot_status()
            state = get_state()
            analytics = get_analytics(trades)
            resp = web.json_response({
                "status": status,
                "state": state,
                "trades": trades,
                "analytics": analytics,
            })
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return resp
        except Exception as e:
            import traceback
            traceback.print_exc()
            return web.json_response(
                {"error": str(e), "detail": traceback.format_exc()},
                status=500,
            )

    async def serve_sessions(_r: web.Request) -> web.Response:
        html = SESSIONS_HTML.read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

    async def api_sessions(request: web.Request) -> web.Response:
        try:
            since, until = _filter_params(request)
            current = get_current_run()
            resp = web.json_response({
                "sessions": get_sessions(since=since, until=until),
                "current_run": current,
            })
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return resp
        except Exception as e:
            import traceback
            traceback.print_exc()
            return web.json_response({"error": str(e)}, status=500)

    def make_app():
        a = web.Application()
        a.router.add_get("/", serve_index)
        a.router.add_get("/sessions", serve_sessions)
        a.router.add_get("/api/status", api_status)
        a.router.add_get("/api/trades", api_trades)
        a.router.add_get("/api/analytics", api_analytics)
        a.router.add_get("/api/all", api_all)
        a.router.add_get("/api/sessions", api_sessions)
        return a

    port = 8765
    for _ in range(10):
        app = make_app()
        try:
            print(f"Dashboard: http://localhost:{port}")
            web.run_app(app, port=port, print=None)
            break
        except OSError as e:
            if "10048" in str(e) or "address already in use" in str(e).lower():
                port += 1
                if port > 8775:
                    print("All ports 8765-8775 in use. Stop existing dashboard first.")
                    raise
                print(f"Port in use, trying {port}...")
            else:
                raise


if __name__ == "__main__":
    main()
