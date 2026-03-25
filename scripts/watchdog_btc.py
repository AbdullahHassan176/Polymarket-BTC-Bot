"""
Watchdog for BTC 5m bot - keeps the bot running indefinitely with auto-restart.

- Runs scripts/bot.py --paper (or scripts/run_12hr_reversal.py via BOT_SCRIPT env)
- Appends session rows to logs/btc_sessions.csv
- Run via: start_bot.bat  |  Stop via: stop_bot.bat
"""
from __future__ import annotations

import csv
import datetime
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable
# Default: run main bot in paper mode. Override with env BOT_SCRIPT.
BOT_SCRIPT = os.environ.get("BOT_SCRIPT", os.path.join(ROOT, "scripts", "bot.py"))
BOT_ARGS = os.environ.get("BOT_ARGS", "--real")
PID_FILE = os.path.join(ROOT, "btc_bot.pid")
WDOG_PID = os.path.join(ROOT, "watchdog_btc.pid")
WATCH_LOG = os.path.join(ROOT, "logs", "watchdog_btc.log")
STDOUT_LOG = os.path.join(ROOT, "logs", "btc_stdout.txt")
STDERR_LOG = os.path.join(ROOT, "logs", "btc_stderr.txt")
SESS_CSV = os.path.join(ROOT, "logs", "btc_sessions.csv")

os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)

_proc: subprocess.Popen | None = None
_shutdown = False


def _ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str) -> None:
    line = f"{_ts()}  {msg}"
    print(line, flush=True)
    with open(WATCH_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _append_session(session: int, start: datetime.datetime, stop: datetime.datetime, exit_code: int) -> None:
    elapsed = stop - start
    hrs = int(elapsed.total_seconds() // 3600)
    mins = int((elapsed.total_seconds() % 3600) // 60)
    write_header = not os.path.isfile(SESS_CSV) or os.path.getsize(SESS_CSV) == 0
    with open(SESS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["session", "started", "stopped", "uptime_hhmm", "exit_code"])
        w.writerow([session, start.strftime("%Y-%m-%d %H:%M:%S"), stop.strftime("%Y-%m-%d %H:%M:%S"), f"{hrs}h{mins:02d}m", exit_code])


def _write_pid(pid: int) -> None:
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def _clear_pid() -> None:
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def _write_wdog_pid(pid: int) -> None:
    with open(WDOG_PID, "w") as f:
        f.write(str(pid))


def _clear_wdog_pid() -> None:
    try:
        os.remove(WDOG_PID)
    except FileNotFoundError:
        pass


def _handle_signal(sig, _frame):
    global _shutdown
    _shutdown = True
    _log("[WATCHDOG] Signal received - stopping cleanly.")
    if _proc and _proc.poll() is None:
        _proc.terminate()
    _clear_pid()
    _clear_wdog_pid()
    sys.exit(0)


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def main() -> None:
    global _proc
    session = 0
    _write_wdog_pid(os.getpid())
    _log("[WATCHDOG] *** BTC bot watchdog started - auto-restart on exit ***")

    while not _shutdown:
        session += 1
        start = datetime.datetime.now()
        banner = f"SESSION #{session} starting at {start.strftime('%Y-%m-%d %H:%M:%S')}"
        _log(f"[WATCHDOG] {banner}")

        exit_code = -1
        stdout_f = None
        stderr_f = None
        try:
            stdout_f = open(STDOUT_LOG, "a", encoding="utf-8", buffering=1)
            stderr_f = open(STDERR_LOG, "a", encoding="utf-8", buffering=1)
            args = [PYTHON, "-u", BOT_SCRIPT]
            if BOT_ARGS:
                args.extend(BOT_ARGS.split())
            _proc = subprocess.Popen(
                args,
                cwd=ROOT,
                stdout=stdout_f,
                stderr=stderr_f,
            )
            _write_pid(_proc.pid)
            _log(f"[WATCHDOG] Bot PID={_proc.pid}")
            _proc.wait()
            exit_code = _proc.returncode
        except Exception as exc:
            _log(f"[WATCHDOG] Failed to launch: {exc}")
        finally:
            _clear_pid()
            for f in (stdout_f, stderr_f):
                if f:
                    try:
                        f.flush()
                        f.close()
                    except Exception:
                        pass

        stop = datetime.datetime.now()
        elapsed = stop - start
        hrs = int(elapsed.total_seconds() // 3600)
        mins = int((elapsed.total_seconds() % 3600) // 60)
        _log(f"[WATCHDOG] Session ended - uptime {hrs}h{mins:02d}m, exit_code={exit_code}")
        _append_session(session, start, stop, exit_code)

        if _shutdown:
            _clear_wdog_pid()
            break

        if exit_code == 0:
            _log("[WATCHDOG] Bot exited cleanly - restarting in 5s...")
            delay = 5
        else:
            _log(f"[WATCHDOG] Unexpected exit (code={exit_code}) - restarting in 15s...")
            delay = 15
        time.sleep(delay)

    _clear_wdog_pid()
    _log("[WATCHDOG] *** Watchdog stopped ***")


if __name__ == "__main__":
    main()
