#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Otwórz Warsztat Dnia (02 Warsztat)
# @raycast.mode silent
# @raycast.packageName LAB

# Optional parameters:
# @raycast.icon 📖

# Documentation:
# @raycast.description Otwiera dzisiejszą Agendę w 02 Warsztat w aplikacji Obsidian (LAB 3.0, dynamicznie przez core_config.ConfigManager)
# @raycast.author Adam

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN=""
for candidate in /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.12 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        PYTHON_BIN="$candidate"
        break
    fi
done

PYTHONPATH="$SCRIPT_DIR" "$PYTHON_BIN" -m lab_core.scratchpad --open
