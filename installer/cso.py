#!/usr/bin/env python3
"""Installed launcher that keeps the app self-contained."""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from skill_orchestrator.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
