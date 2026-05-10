"""Prepend repo `scripts/` to sys.path so root-level tools import bot modules."""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_scripts_on_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    scripts = root / "scripts"
    if scripts.is_dir():
        s = str(scripts)
        if s not in sys.path:
            sys.path.insert(0, s)
    return root
