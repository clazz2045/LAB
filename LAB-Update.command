#!/bin/bash
# LAB-Update.command - Kanoniczny aktualizator silnika LAB 3.0 in-place (macOS)
# Aktualizuje wyłącznie pokój '08 Zespół AI/Skrypty/' oraz konfigurację agentów (.codex/config.toml, .agents/hooks.json).
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RELEASE_ZIP_URL="https://github.com/clazz2045/LAB/releases/latest/download/LAB-Installer.zip"
VAULT_ROOT=""
PAYLOAD_ARG=""
NON_INTERACTIVE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --vault-root|-v)
      VAULT_ROOT="$2"
      shift 2
      ;;
    --payload|-p)
      PAYLOAD_ARG="$2"
      shift 2
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      shift
      ;;
    *)
      if [ -z "$VAULT_ROOT" ] && [ -d "$1" ]; then
        VAULT_ROOT="$1"
      fi
      shift
      ;;
  esac
done

# Obsługa Gatekeepera (macOS): zdjęcie kwarantanny z launchera i ewentualnego wsadu
if command -v xattr >/dev/null 2>&1; then
  if xattr -p com.apple.quarantine "$0" >/dev/null 2>&1; then
    xattr -d com.apple.quarantine "$0" >/dev/null 2>&1 || true
  fi
  if [ -d "$SCRIPT_DIR/.lab-payload" ]; then
    xattr -dr com.apple.quarantine "$SCRIPT_DIR/.lab-payload" >/dev/null 2>&1 || true
  fi
fi
chmod +x "$0" >/dev/null 2>&1 || true

# Wykryj Python >= 3.11 (z obsługą wbudowanego modułu tomllib)
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
  if [ "$NON_INTERACTIVE" -eq 0 ] && [ -t 0 ]; then
    read -r -p "Nacisnij Enter, aby zamknac..."
  fi
  exit 1
fi

# Wykryj docelowy skarbiec LAB (lab.config.toml)
if [ -z "$VAULT_ROOT" ]; then
  if [ -f "$SCRIPT_DIR/lab.config.toml" ]; then
    VAULT_ROOT="$SCRIPT_DIR"
  elif [ -f "$SCRIPT_DIR/../lab.config.toml" ]; then
    VAULT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  elif [ "$NON_INTERACTIVE" -eq 0 ] && [ -t 0 ]; then
    DEFAULT_VAULT=""
    for cand_v in "$HOME/LAB" "$HOME/Documents/LAB"; do
      if [ -f "$cand_v/lab.config.toml" ]; then
        DEFAULT_VAULT="$cand_v"
        break
      fi
    done
    if [ -n "$DEFAULT_VAULT" ]; then
      read -r -p "Podaj sciezke do skarbca LAB [$DEFAULT_VAULT]: " USER_VAULT
      VAULT_ROOT="${USER_VAULT:-$DEFAULT_VAULT}"
    else
      read -r -p "Podaj pelna sciezke do skarbca LAB (z plikiem lab.config.toml): " VAULT_ROOT
    fi
  fi
fi

if [ -z "$VAULT_ROOT" ] || [ ! -f "$VAULT_ROOT/lab.config.toml" ]; then
  echo "BLAD: Nie znaleziono pliku lab.config.toml w katalogu docelowym: '${VAULT_ROOT:-brak}'."
  if [ "$NON_INTERACTIVE" -eq 0 ] && [ -t 0 ]; then
    read -r -p "Nacisnij Enter, aby zamknac..."
  fi
  exit 1
fi

VAULT_ROOT="$(cd "$VAULT_ROOT" && pwd)"

echo "=== LAB 3.0 - Aktualizacja Silnika In-Place ==="
echo "Skarbiec docelowy: $VAULT_ROOT"

set +e
"$PYTHON_BIN" - "$SCRIPT_DIR" "$VAULT_ROOT" "$PAYLOAD_ARG" "$RELEASE_ZIP_URL" <<'PYEOF'
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True

script_dir = Path(sys.argv[1]).resolve()
vault_root = Path(sys.argv[2]).resolve()
payload_arg = sys.argv[3].strip()
release_url = sys.argv[4].strip()


def safe_extract(zpath: Path, dest: Path) -> Path:
    dest_res = dest.resolve()
    dest_res.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath, "r") as zf:
        for member in zf.infolist():
            mp = (dest_res / member.filename).resolve()
            mp.relative_to(dest_res)
        zf.extractall(dest_res)
    return dest_res


def find_skrypty(root: Path) -> Path | None:
    for cand in (
        root / ".lab-payload" / "08 Zespół AI" / "Skrypty",
        root / "08 Zespół AI" / "Skrypty",
        root / "Skrypty",
    ):
        if (cand / "lab_core" / "installer.py").is_file():
            return cand
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "Skrypty" / "lab_core" / "installer.py").is_file():
                return child / "Skrypty"
    return None


with tempfile.TemporaryDirectory(prefix="lab_updater_") as tmp:
    tmp_dir = Path(tmp)
    payload_root: Path | None = None

    if payload_arg:
        if payload_arg.startswith(("http://", "https://")):
            zfile = tmp_dir / "LAB-Installer.zip"
            print(f"Pobieranie paczki z {payload_arg}...")
            req = urllib.request.Request(payload_arg, headers={"User-Agent": "LAB-Updater/3.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                zfile.write_bytes(resp.read())
            payload_root = safe_extract(zfile, tmp_dir / "extracted")
        else:
            p = Path(payload_arg).expanduser().resolve()
            if p.is_file() and p.suffix.lower() == ".zip":
                payload_root = safe_extract(p, tmp_dir / "extracted")
            elif p.is_dir():
                payload_root = p
    else:
        local_zip = script_dir / "LAB-Installer.zip"
        if local_zip.is_file():
            print(f"Wykryto lokalna paczke: {local_zip}")
            payload_root = safe_extract(local_zip, tmp_dir / "extracted")
        elif (script_dir / ".lab-payload" / "08 Zespół AI" / "Skrypty" / "lab_core" / "installer.py").is_file():
            print(f"Wykryto lokalny wsad: {script_dir / '.lab-payload'}")
            payload_root = script_dir / ".lab-payload"
        elif script_dir != vault_root and (script_dir / "08 Zespół AI" / "Skrypty" / "lab_core" / "installer.py").is_file():
            print(f"Wykryto lokalne repozytorium: {script_dir}")
            payload_root = script_dir
        else:
            zfile = tmp_dir / "LAB-Installer.zip"
            print(f"Pobieranie najnowszego wydania z {release_url}...")
            req = urllib.request.Request(release_url, headers={"User-Agent": "LAB-Updater/3.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                zfile.write_bytes(resp.read())
            payload_root = safe_extract(zfile, tmp_dir / "extracted")

    if payload_root is None:
        print("BLAD: Nie udalo sie ustalic zrodla aktualizacji.", file=sys.stderr)
        sys.exit(1)

    if (payload_root / ".lab-payload").is_dir():
        payload_root = payload_root / ".lab-payload"

    skrypty_dir = find_skrypty(payload_root)
    if skrypty_dir is None:
        print("BLAD: Paczka nie zawiera silnika '08 Zespół AI/Skrypty/lab_core/installer.py'.", file=sys.stderr)
        sys.exit(1)

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(skrypty_dir)
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            "-W",
            "ignore::RuntimeWarning:runpy",
            "-m",
            "lab_core.installer",
            "--update",
            "--vault-root",
            str(vault_root),
            "--payload",
            str(payload_root),
        ],
        cwd=str(vault_root),
        env=env,
    )
    sys.exit(proc.returncode)
PYEOF
EXIT_CODE=$?
set -e

if [ "$NON_INTERACTIVE" -eq 0 ] && [ -t 0 ]; then
  read -r -p "Nacisnij Enter, aby zamknac..."
fi
exit $EXIT_CODE
