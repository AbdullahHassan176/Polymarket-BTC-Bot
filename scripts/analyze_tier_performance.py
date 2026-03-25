"""
Analyze per-tier trading performance and produce keep/drop recommendations.

Usage:
  python analyze_tier_performance.py
  python analyze_tier_performance.py --csv logs/trades_24h.csv
  python analyze_tier_performance.py --csv logs/trades_12hr_hybrid.csv --out logs/tier_report_12h.md
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List


WIN_OUTCOMES = {"TP", "WIN"}
LOSS_OUTCOMES = {"SL", "LOSS"}


@dataclass
class TierStats:
    tier: str
    n: int
    wins: int
    losses: int
    win_rate_pct: float
    total_pnl: float
    expectancy: float
    avg_win: float
    avg_loss: float
    gross_profit: float
    gross_loss_abs: float
    profit_factor: float
    recommendation: str
    rationale: str


def _safe_float(raw: str) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _read_closed_rows(csv_path: Path) -> List[dict]:
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    open_tier_by_cid: Dict[str, str] = {}
    for r in rows:
        if (r.get("action", "") or "").upper() != "OPEN":
            continue
        cid = (r.get("condition_id", "") or "").strip()
        tier = (r.get("strategy_tier", "") or "").strip()
        if cid and tier:
            open_tier_by_cid[cid] = tier

    closed: List[dict] = []
    for r in rows:
        if (r.get("action", "") or "").upper() != "CLOSE":
            continue
        tier = (r.get("strategy_tier", "") or "").strip()
        if not tier:
            cid = (r.get("condition_id", "") or "").strip()
            if cid and cid in open_tier_by_cid:
                r["strategy_tier"] = open_tier_by_cid[cid]
        closed.append(r)
    return closed


def _group_by_tier(rows: Iterable[dict]) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = {}
    for r in rows:
        tier = (r.get("strategy_tier", "") or "unknown").strip() or "unknown"
        grouped.setdefault(tier, []).append(r)
    return grouped


def _recommend(
    n: int,
    expectancy: float,
    profit_factor: float,
    min_sample: int,
    pf_threshold: float,
) -> tuple[str, str]:
    if n < min_sample:
        return (
            "HOLDOUT",
            f"Sample too small (n={n} < {min_sample}); gather more data before keep/drop.",
        )
    if expectancy > 0 and profit_factor >= pf_threshold:
        return (
            "KEEP",
            f"Expectancy positive and PF {profit_factor:.2f} >= {pf_threshold:.2f}.",
        )
    reasons = []
    if expectancy <= 0:
        reasons.append(f"expectancy {expectancy:.4f} <= 0")
    if profit_factor < pf_threshold:
        reasons.append(f"PF {profit_factor:.2f} < {pf_threshold:.2f}")
    return ("DROP", "; ".join(reasons))


def compute_tier_stats(
    rows: Iterable[dict],
    min_sample: int,
    pf_threshold: float,
) -> List[TierStats]:
    grouped = _group_by_tier(rows)
    stats: List[TierStats] = []
    for tier, grp in grouped.items():
        pnls = [_safe_float(r.get("pnl_usdc", "")) for r in grp]
        outcomes = [(r.get("outcome", "") or "").upper() for r in grp]
        n = len(grp)
        wins = sum(1 for o in outcomes if o in WIN_OUTCOMES)
        losses = sum(1 for o in outcomes if o in LOSS_OUTCOMES)
        total_pnl = sum(pnls)
        expectancy = total_pnl / n if n else 0.0

        win_pnls = [p for p in pnls if p > 0]
        loss_pnls = [p for p in pnls if p < 0]
        avg_win = (sum(win_pnls) / len(win_pnls)) if win_pnls else 0.0
        avg_loss = (sum(loss_pnls) / len(loss_pnls)) if loss_pnls else 0.0
        gross_profit = sum(win_pnls)
        gross_loss_abs = abs(sum(loss_pnls))
        if gross_loss_abs == 0:
            profit_factor = math.inf if gross_profit > 0 else 0.0
        else:
            profit_factor = gross_profit / gross_loss_abs
        win_rate_pct = (100.0 * wins / n) if n else 0.0

        rec, rationale = _recommend(
            n=n,
            expectancy=expectancy,
            profit_factor=profit_factor,
            min_sample=min_sample,
            pf_threshold=pf_threshold,
        )
        stats.append(
            TierStats(
                tier=tier,
                n=n,
                wins=wins,
                losses=losses,
                win_rate_pct=win_rate_pct,
                total_pnl=total_pnl,
                expectancy=expectancy,
                avg_win=avg_win,
                avg_loss=avg_loss,
                gross_profit=gross_profit,
                gross_loss_abs=gross_loss_abs,
                profit_factor=profit_factor,
                recommendation=rec,
                rationale=rationale,
            )
        )
    stats.sort(key=lambda s: s.total_pnl, reverse=True)
    return stats


def _pf_text(pf: float) -> str:
    if math.isinf(pf):
        return "inf"
    return f"{pf:.2f}"


def render_markdown(stats: List[TierStats], total_closed: int, total_pnl: float) -> str:
    lines: List[str] = []
    lines.append("# Tier Performance Report")
    lines.append("")
    lines.append(f"- Closed trades: **{total_closed}**")
    lines.append(f"- Net PnL: **{total_pnl:.4f} USDC**")
    lines.append("")
    lines.append(
        "| Tier | n | Wins | Losses | Win% | Expectancy | PF | Net PnL | Recommendation |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for s in stats:
        lines.append(
            f"| {s.tier} | {s.n} | {s.wins} | {s.losses} | {s.win_rate_pct:.2f}% | "
            f"{s.expectancy:.4f} | {_pf_text(s.profit_factor)} | {s.total_pnl:.4f} | "
            f"{s.recommendation} |"
        )
    lines.append("")
    lines.append("## Recommendation Rationale")
    lines.append("")
    for s in stats:
        lines.append(f"- **{s.tier} ({s.recommendation})**: {s.rationale}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze per-tier expectancy/profit-factor and suggest keep/drop."
    )
    parser.add_argument(
        "--csv",
        default="logs/trades_24h.csv",
        help="Path to trades CSV with OPEN/CLOSE rows (default: logs/trades_24h.csv).",
    )
    parser.add_argument(
        "--min-sample",
        type=int,
        default=30,
        help="Minimum close count for KEEP/DROP decisions (default: 30).",
    )
    parser.add_argument(
        "--pf-threshold",
        type=float,
        default=1.2,
        help="Profit-factor threshold for KEEP (default: 1.2).",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output markdown path. If omitted, prints to stdout.",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        raise SystemExit(f"CSV not found: {csv_path}")

    closed = _read_closed_rows(csv_path)
    if not closed:
        raise SystemExit(f"No CLOSE rows found in: {csv_path}")

    stats = compute_tier_stats(
        rows=closed,
        min_sample=args.min_sample,
        pf_threshold=args.pf_threshold,
    )
    total_pnl = sum(_safe_float(r.get("pnl_usdc", "")) for r in closed)
    report = render_markdown(stats=stats, total_closed=len(closed), total_pnl=total_pnl)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"Wrote report: {out_path}")
    else:
        print(report)


if __name__ == "__main__":
    main()
