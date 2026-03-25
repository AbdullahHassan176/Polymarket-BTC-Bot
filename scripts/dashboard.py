"""
Launch the web dashboard. Use dashboard/server.py directly or run this script.

The proper dashboard (aiohttp + HTML/Chart.js) lives in dashboard/.
Run: python dashboard/server.py  |  Or: .\\dashboard_bot.bat
Then open http://localhost:8765
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "dashboard", "server.py")

if not os.path.isfile(SERVER):
    print("Dashboard not found. Run from repo root: python dashboard/server.py")
    sys.exit(1)

os.chdir(ROOT)
subprocess.run([sys.executable, SERVER], cwd=ROOT)
