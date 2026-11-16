#!/usr/bin/env python3
"""stop_guard.py - Fasada CLI dla lab_core.stop_guard (Antigravity Stop Hook)."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

from lab_core.stop_guard import main

if __name__ == "__main__":
    sys.exit(main())
