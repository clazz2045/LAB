#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Zapisz e-mail do LAB
# @raycast.mode compact
# @raycast.packageName LAB

# Optional parameters:
# @raycast.icon ✉️

# Documentation:
# @raycast.description Eksportuje zaznaczoną wiadomość z aplikacji Poczta (Apple Mail) do 01 Skrzynka w LAB 3.0 i kopiuje treść do schowka
# @raycast.author Adam

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN=""
for candidate in /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.12 /usr/local/bin/python3 "$(command -v python3 2>/dev/null)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        PYTHON_BIN="$candidate"
        break
    fi
done

PYTHONPATH="$SCRIPT_DIR" "$PYTHON_BIN" -m lab_core.mail
