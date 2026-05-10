# Main guide — fresh PC setup (Polymarket BTC bot)

This doc is the **single onboarding path** for a new Windows machine. Deeper theory lives in `docs/` and `ai.md`.

---

## What we are trying to do

**Goal:** Trade Polymarket’s rolling **“Up or Down — 5 minutes”** markets (BTC first; ETH/XRP/SOL/DOGE supported) using **public 1m price data (OKX)** and Polymarket’s **Gamma + CLOB** APIs. The bot decides YES vs NO each window, sizes risk in `scripts/config.py`, and either **simulates fills (paper)** or **places real USDC orders (live)**.

**We are not promising profit.** Short binaries are high-variance. The stack exists to **research** (backtest / sweeps), **paper-run** with the same rules as live, then **go live** only when you accept the risk.

---

## Prerequisites

| Item | Notes |
|------|--------|
| **Windows 10/11** | Launchers are `.bat` + PowerShell; bot is Python. |
| **Python 3.9+** | From [python.org](https://www.python.org/downloads/) — check “Add python to PATH”. |
| **Git** | To clone the repo. |
| **Internet** | OKX, Gamma, CLOB; live mode also needs Polygon (USDC + gas). |

---

## 1. Get the code

```powershell
git clone https://github.com/AbdullahHassan176/Polymarket-BTC-Bot.git
cd Polymarket-BTC-Bot
```

(Use your fork URL if different.)

---

## 2. Virtual environment and dependencies

From the **repo root** (folder that contains `scripts/`, `backtest.py`, `.env.example`):

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

**Optional — research / sweeps / Optuna** (heavier stack):

```powershell
pip install -r requirements-research.txt
```

---

## 3. Environment file (`.env`)

```powershell
copy .env.example .env
notepad .env
```

- **Paper-only:** You can leave keys blank for some experiments, but the **main bot** still talks to Polymarket/OKX for prices and market discovery — so a normal setup fills **EOA or proxy** fields from `.env.example`.
- **EOA:** `POLY_PRIVATE_KEY`, `POLY_WALLET_ADDRESS` (Polygon USDC + POL for gas).
- **Proxy (Gnosis Safe):** `PRIVATE_KEY` / `POLY_PRIVATE_KEY` for signer + `PROXY_WALLET` for the Safe that holds USDC — see `docs/PROXY_WALLET_SETUP.md`.
- **Never commit `.env`** (it is gitignored).

---

## 4. Trading mode (`scripts/config.py`)

| Setting | Meaning |
|---------|--------|
| `REAL_TRADING = False` | **Paper / simulation** — no CLOB orders (still uses APIs). |
| `REAL_TRADING = True` | **Live** — real money; only after wallet funded and you use a **`--real`** launcher. |

After any config change, **restart** the bot (`restart_bot.bat` or stop + start).

---

## 5. Run the bot (Windows)

Always from **repo root**.

| Action | Command |
|--------|--------|
| **Paper — single BTC + watchdog** (recommended first) | `.\start_paper_btc.bat` |
| **Stop everything** | `.\stop_bot.bat` or create empty `STOP_BOT.txt` in repo root |
| **Tail logs** | `.\watch_bot.bat` |
| **Status** | `.\bot_status.bat` |
| **Multi-process mix** (live BTC+SOL + paper others) | `.\start_bot.bat` — see `scripts/launch/run_btc_live_others_paper.ps1` |
| **Manual paper (no watchdog)** | `python scripts\bot.py --paper` |

**Logs**

- General bot log: `logs\bot.log`
- Paper fills: `logs\paper_trades.csv` (created when trades happen)
- Live fills: `logs\trades.csv`
- State: `state.json` (BTC), `state_ETH.json`, etc. per asset

---

## 6. HTTPS / SSL on a fresh PC

If `bot.log` shows **`SSLCertVerificationError`** for `clob.polymarket.com` or `okx.com`:

1. Update Python and retry.
2. `pip install --upgrade certifi`
3. Corporate networks: install your org root CA or set `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` to that PEM (IT can supply it).

Without working TLS, the bot will **not** see active markets reliably.

---

## 7. Auto-research and research automation

**What “auto-research” means here:** repeatable **offline** experiments — backtests, parameter sweeps, JSON-driven runs, optional Optuna — so you can compare strategies **without** risking USDC. CI runs a **subset** on every push/PR.

| What | Command | Purpose |
|------|---------|--------|
| **Backtest** | `python backtest.py --synthetic-bars 8000` | Replay signal logic on synthetic or CSV history (`docs/BACKTEST.md`). |
| **Grid + walk-forward** | `python run_param_sweep.py --synthetic-bars 8000 --train-frac 0.7` | Many configs; train/test split. |
| **Quick sweep (CI-style)** | `python run_param_sweep.py --quick --synthetic-bars 3000` | Fast smoke. |
| **JSON experiments** | `python run_research_agent.py --params research\research_params.example.json` | Structured runs → `logs/experiments.csv` (copy to `research_params.json` per `research/AGENT_CONTRACT.md`). |
| **Optuna** | `pip install -r requirements-research.txt` then `python sweep_optuna.py --trials 15` | Bayesian search. |
| **Attribution** | `python summarize_attribution.py --glob "logs/trades*.csv"` | Summaries → `logs/attribution_summary.txt`. |
| **Paper report** | `python paper_report.py --csv logs\trades.csv` | Readability over CSV. |

**CI:** `.github/workflows/research.yml` runs backtest + agent + quick sweep on push/PR.

**Rules for AI/automation agents:** See `research/AGENT_CONTRACT.md` (allowed edits, forbidden live changes without human approval).

**Rolling stop (live or paper risk logic):** `ROLLING_STOP_*` in `scripts/config.py`; state field `recent_trade_pnls` in `state.json`. Details: `docs/RESEARCH_AUTOMATION.md`.

**Honest evaluation:** No method guarantees profit — see `docs/RIGOROUS_EVALUATION.md`.

---

## 8. Where everything lives

| Path | Role |
|------|------|
| `scripts/bot.py` | Main loop |
| `scripts/config.py` | Risk, strategy mode, toggles |
| `scripts/launch/*.ps1` | How `.bat` files start processes |
| `backtest.py`, `run_param_sweep.py`, `run_research_agent.py` | Research entrypoints (repo root) |
| `docs/RESEARCH_AUTOMATION.md` | Short index of research commands + CI |
| `ai.md` | Compact project map for developers |

---

## 9. Quick checklist

1. Clone repo  
2. `python -m venv venv` → activate → `pip install -r requirements.txt`  
3. `copy .env.example .env` and fill wallet lines if you run the real API stack  
4. Confirm `REAL_TRADING` matches your intent in `scripts/config.py`  
5. `.\start_paper_btc.bat`  
6. Watch `logs\bot.log` — fix SSL if needed  
7. When stable, run research commands or enable live **only** with capital you can lose  

Welcome aboard — measure first, then size risk.
