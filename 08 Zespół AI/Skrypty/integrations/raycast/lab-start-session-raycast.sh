#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Start Sesji LAB
# @raycast.mode silent
# @raycast.packageName LAB

# Optional parameters:
# @raycast.icon 🚀

# Documentation:
# @raycast.description Automatycznie wkleja prompt startowy nowej sesji LAB 3.0 (data + kolejny numer sesji)
# @raycast.author Adam

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN=""
for candidate in /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.12 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        PYTHON_BIN="$candidate"
        break
    fi
done

PYTHONPATH="$SCRIPT_DIR" "$PYTHON_BIN" -m lab_core.prompt --paste
