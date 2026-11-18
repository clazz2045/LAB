@echo off
REM LAB-Update.bat - Kanoniczny aktualizator silnika LAB 3.0 in-place (Windows)
setlocal
cd /d "%~dp0"

set "PY_CMD="
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if %errorlevel%==0 set "PY_CMD=py -3"
)

if "%PY_CMD%"=="" (
    where python >nul 2>&1
    if %errorlevel%==0 (
        python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
        if %errorlevel%==0 set "PY_CMD=python"
    )
)

if "%PY_CMD%"=="" (
    echo BLAD: Wymagany Python ^>= 3.11 ^(modul tomllib^).
    echo Pobierz Python 3.11+ ze strony https://www.python.org/downloads/
    pause
    exit /b 1
)

%PY_CMD% -B -c "import pathlib,sys; lines=pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').splitlines(); idx=lines.index('### PYTHON_BOOTSTRAP ###')+1; exec('\n'.join(lines[idx:]))" "%~f0" "%~dp0" %*
set "UPD_ERR=%errorlevel%"
if %UPD_ERR% neq 0 pause
exit /b %UPD_ERR%

### PYTHON_BOOTSTRAP ###
import argparse
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True

RELEASE_ZIP_URL = "https://github.com/clazz2045/LAB/releases/latest/download/LAB-Installer.zip"
AI_DIR_NAME = "08 Zesp\u00f3\u0142 AI"

script_dir = Path(sys.argv[2]).resolve()
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("pos_vault", nargs="?", default=None)
parser.add_argument("--vault-root", "-v", default=None)
parser.add_argument("--payload", "-p", default=None)
parser.add_argument("--non-interactive", action="store_true")
args, _ = parser.parse_known_args(sys.argv[3:])

vault_arg = args.vault_root or args.pos_vault
if not vault_arg:
    if (script_dir / "lab.config.toml").is_file():
        vault_arg = str(script_dir)
    elif (script_dir.parent / "lab.config.toml").is_file():
        vault_arg = str(script_dir.parent)
    elif not args.non_interactive and sys.stdin and sys.stdin.isatty():
        default_cand = None
        for c in (Path.home() / "LAB", Path.home() / "Documents" / "LAB"):
            if (c / "lab.config.toml").is_file():
                default_cand = str(c)
                break
        prompt = f"Podaj sciezke do skarbca LAB [{default_cand}]: " if default_cand else "Podaj pelna sciezke do skarbca LAB (z lab.config.toml): "
        reply = input(prompt).strip()
        vault_arg = reply or default_cand

if not vault_arg:
    print("BLAD: Nie wskazano sciezki do skarbca LAB.", file=sys.stderr)
    sys.exit(1)

vault_root = Path(vault_arg).expanduser().resolve()
if not (vault_root / "lab.config.toml").is_file():
    print(f"BLAD: Brak pliku lab.config.toml w '{vault_root}'.", file=sys.stderr)
    sys.exit(1)


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
        root / ".lab-payload" / AI_DIR_NAME / "Skrypty",
        root / AI_DIR_NAME / "Skrypty",
        root / "Skrypty",
    ):
        if (cand / "lab_core" / "installer.py").is_file():
            return cand
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "Skrypty" / "lab_core" / "installer.py").is_file():
                return child / "Skrypty"
    return None


print("=== LAB 3.0 - Aktualizacja Silnika In-Place ===")
print(f"Skarbiec docelowy: {vault_root}")

with tempfile.TemporaryDirectory(prefix="lab_updater_") as tmp:
    tmp_dir = Path(tmp)
    payload_root: Path | None = None
    payload_arg = (args.payload or "").strip()

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
        elif (script_dir / ".lab-payload" / AI_DIR_NAME / "Skrypty" / "lab_core" / "installer.py").is_file():
            print(f"Wykryto lokalny wsad: {script_dir / '.lab-payload'}")
            payload_root = script_dir / ".lab-payload"
        elif script_dir != vault_root and (script_dir / AI_DIR_NAME / "Skrypty" / "lab_core" / "installer.py").is_file():
            print(f"Wykryto lokalne repozytorium: {script_dir}")
            payload_root = script_dir
        else:
            zfile = tmp_dir / "LAB-Installer.zip"
            print(f"Pobieranie najnowszego wydania z {RELEASE_ZIP_URL}...")
            req = urllib.request.Request(RELEASE_ZIP_URL, headers={"User-Agent": "LAB-Updater/3.0"})
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
        print(f"BLAD: Paczka nie zawiera silnika '{AI_DIR_NAME}/Skrypty/lab_core/installer.py'.", file=sys.stderr)
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
    if not args.non_interactive and proc.returncode == 0 and sys.stdin and sys.stdin.isatty():
        input("Nacisnij Enter, aby zamknac...")
    sys.exit(proc.returncode)
