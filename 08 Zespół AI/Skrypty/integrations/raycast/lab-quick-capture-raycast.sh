#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Dopisz do 01 Skrzynki / 02 Warsztatu
# @raycast.mode compact
# @raycast.packageName LAB

# Optional parameters:
# @raycast.icon 📝
# @raycast.argument1 { "type": "text", "placeholder": "Wpisz myśl / notatkę...", "optional": false }

# Documentation:
# @raycast.description Dopisywanie myśli w locie do 01 Skrzynki / 02 Warsztatu w LAB 3.0 (dynamicznie przez core_config.ConfigManager)
# @raycast.author Adam

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN=""
for candidate in /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.12 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        PYTHON_BIN="$candidate"
        break
    fi
done

PYTHONPATH="$SCRIPT_DIR" "$PYTHON_BIN" -m lab_core.scratchpad --capture "$1"
