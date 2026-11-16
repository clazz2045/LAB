#!/bin/bash
# LAB-Installer.command - Launcher Kreatora WWW LAB (macOS)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Obsługa Gatekeepera (macOS): zdjęcie kwarantanny z launchera i wsadu po pierwszym uruchomieniu
if command -v xattr >/dev/null 2>&1; then
  if xattr -p com.apple.quarantine "$0" >/dev/null 2>&1; then
    xattr -d com.apple.quarantine "$0" >/dev/null 2>&1 || true
  fi
  if [ -d "$SCRIPT_DIR/.lab-payload" ]; then
    xattr -dr com.apple.quarantine "$SCRIPT_DIR/.lab-payload" >/dev/null 2>&1 || true
  fi
fi
chmod +x "$0" >/dev/null 2>&1 || true

# Wykryj tryb uruchomienia: root repozytorium lub paczka LAB-Installer.zip (.lab-payload/)
INIT_SCRIPT=""
if [ -f "$SCRIPT_DIR/lab-init.py" ]; then
  INIT_SCRIPT="$SCRIPT_DIR/lab-init.py"
elif [ -f "$SCRIPT_DIR/.lab-payload/lab-init.py" ]; then
  INIT_SCRIPT="$SCRIPT_DIR/.lab-payload/lab-init.py"
else
  echo "BLAD: Nie znaleziono pliku lab-init.py ani w katalogu glownym, ani w .lab-payload/."
  read -r -p "Nacisnij Enter, aby zamknac..."
  exit 1
fi

# Wykryj stub Xcode Command Line Tools (macOS python3 bez zainstalowanego środowiska)
PYTHON_BIN=""
for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
  if [ -n "$cand" ] && [ -x "$cand" ]; then
    if "$cand" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1; then
      PYTHON_BIN="$cand"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "BLAD: Wymagany Python >= 3.11 (modul tomllib)."
  echo "Zainstaluj Python 3.11+ ze strony https://www.python.org/downloads/ lub przez Homebrew: brew install python"
  read -r -p "Nacisnij Enter, aby zamknac..."
  exit 1
fi

"$PYTHON_BIN" "$INIT_SCRIPT" --web
