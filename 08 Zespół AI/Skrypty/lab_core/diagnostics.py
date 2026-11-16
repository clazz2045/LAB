"""lab_core.diagnostics - Pre-flight System Diagnostics dla LAB 3.0 (Framework PZW)
Standard inżynieryjny: 100% Python Standard Library (platform, shutil, subprocess, sys, pathlib).
Weryfikuje gotowość środowiska przed uruchomieniem lub powołaniem nowej instancji.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

sys.dont_write_bytecode = True


@dataclass
class DiagnosticItem:
    """Reprezentacja pojedynczej weryfikacji diagnostycznej."""
    name: str
    category: str  # "critical" | "optional"
    passed: bool
    version: Optional[str] = None
    path: Optional[str] = None
    details: str = ""
    remedy: Optional[str] = None

    @property
    def is_critical(self) -> bool:
        return self.category == "critical"


@dataclass
class PreflightReport:
    """Całościowy raport diagnostyczny środowiska systemowego."""
    os_info: str
    python_version: str
    items: List[DiagnosticItem] = field(default_factory=list)

    @property
    def all_critical_passed(self) -> bool:
        return all(item.passed for item in self.items if item.is_critical)

    @property
    def passed_count(self) -> int:
        return sum(1 for item in self.items if item.passed)

    @property
    def total_count(self) -> int:
        return len(self.items)

    def format_cli(self) -> str:
        """Formatuje raport do czytelnego, technicznego bloku terminala."""
        lines = [
            "============================================================",
            " LAB 3.0 - PRE-FLIGHT SYSTEM DIAGNOSTICS",
            "============================================================",
            f" System OS   : {self.os_info}",
            f" Python Core : {self.python_version}",
            "------------------------------------------------------------",
        ]

        for item in self.items:
            tag = "PASS" if item.passed else ("FAIL" if item.is_critical else "WARN")
            icon = "[OK]" if item.passed else ("[ERR]" if item.is_critical else "[OPT]")
            label = f"{item.name} ({item.category.upper()})"
            lines.append(f" {icon:<6} {label:<24} : {item.details}")
            if item.version:
                lines.append(f"        Wersja: {item.version}")
            if item.path:
                lines.append(f"        Ścieżka: {item.path}")
            if not item.passed and item.remedy:
                lines.append(f"        -> Instrukcja: {item.remedy}")

        lines.append("------------------------------------------------------------")
        if self.all_critical_passed:
            lines.append(" Status: GOTOWY (Wszystkie wymagania krytyczne spełnione)")
        else:
            lines.append(" Status: BLOKADA (Braki w wymaganiach krytycznych)")
        lines.append("============================================================")
        return "\n".join(lines)


def check_python(min_version: Tuple[int, int] = (3, 11)) -> DiagnosticItem:
    """Weryfikuje wersję interpretera Pythona."""
    current = sys.version_info
    major = getattr(current, "major", current[0])
    minor = getattr(current, "minor", current[1])
    micro = getattr(current, "micro", current[2] if len(current) > 2 else 0)
    ver_str = f"{major}.{minor}.{micro}"
    passed = (major, minor) >= min_version
    min_str = f"{min_version[0]}.{min_version[1]}"

    if passed:
        details = f"Python {ver_str} spełnia wymagania (>= {min_str})"
        remedy = None
    else:
        details = f"Wykryto Python {ver_str}. Wymagany Python >= {min_str}"
        remedy = "Zainstaluj Pythona 3.11+ przez https://python.org lub Homebrew (brew install python)."

    return DiagnosticItem(
        name="Python Interpreter",
        category="critical",
        passed=passed,
        version=ver_str,
        path=sys.executable,
        details=details,
        remedy=remedy,
    )


def check_obsidian() -> DiagnosticItem:
    """Weryfikuje obecność aplikacji Obsidian w systemie."""
    system = platform.system()
    found_path: Optional[Path] = None

    if system == "Darwin":
        home = Path.home()
        candidates = [
            Path("/Applications/Obsidian.app"),
            home / "Applications" / "Obsidian.app",
            Path("/System/Applications/Obsidian.app"),
        ]
        for c in candidates:
            if c.exists():
                found_path = c
                break

    elif system == "Linux":
        which_path = shutil.which("obsidian")
        if which_path:
            found_path = Path(which_path)
        else:
            candidates = [
                Path("/usr/bin/obsidian"),
                Path("/usr/local/bin/obsidian"),
                Path("/var/lib/flatpak/exports/bin/md.obsidian.Obsidian"),
                Path.home() / ".local" / "bin" / "obsidian",
            ]
            for c in candidates:
                if c.exists():
                    found_path = c
                    break

    elif system == "Windows":
        which_path = shutil.which("obsidian")
        if which_path:
            found_path = Path(which_path)
        else:
            local_appdata = os.environ.get("LOCALAPPDATA")
            prog_files = os.environ.get("ProgramFiles")
            candidates = []
            if local_appdata:
                candidates.append(Path(local_appdata) / "Obsidian" / "Obsidian.exe")
            if prog_files:
                candidates.append(Path(prog_files) / "Obsidian" / "Obsidian.exe")
            for c in candidates:
                if c.exists():
                    found_path = c
                    break

    # Ogólny fallback shutil.which
    if not found_path:
        w = shutil.which("obsidian")
        if w:
            found_path = Path(w)

    passed = found_path is not None
    if passed:
        details = f"Obsidian odnaleziony w lokalizacji: {found_path}"
        remedy = None
    else:
        details = "Nie odnaleziono aplikacji Obsidian w standardowych lokalizacjach"
        remedy = "Pobierz i zainstaluj Obsidian z https://obsidian.md"

    return DiagnosticItem(
        name="Obsidian App",
        category="critical",
        passed=passed,
        version=None,
        path=str(found_path) if found_path else None,
        details=details,
        remedy=remedy,
    )


def check_git() -> DiagnosticItem:
    """Weryfikuje dostępność Gita i sprawdza wersję (narzędzie opcjonalne)."""
    git_bin = shutil.which("git")
    if not git_bin:
        return DiagnosticItem(
            name="Git VCS",
            category="optional",
            passed=False,
            details="Polecenie 'git' nie zostało odnalezione w PATH (opcjonalne)",
            remedy="Zainstaluj narzędzie Git (opcjonalne, przydatne jedynie przy zaawansowanym wersjonowaniu kodu silnika).",
        )

    try:
        res = subprocess.run(
            [git_bin, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3.0,
            check=False,
        )
        if res.returncode == 0:
            ver_text = res.stdout.strip()
            return DiagnosticItem(
                name="Git VCS",
                category="optional",
                passed=True,
                version=ver_text,
                path=git_bin,
                details=f"Dostępne narzędzie {ver_text} (opcjonalne)",
            )
        else:
            return DiagnosticItem(
                name="Git VCS",
                category="optional",
                passed=False,
                path=git_bin,
                details=f"Git zwrócił błąd wykonania (kod {res.returncode}): {res.stderr.strip()}",
                remedy="Upewnij się, że Git działa poprawnie w konsoli systemowej.",
            )
    except Exception as e:
        return DiagnosticItem(
            name="Git VCS",
            category="optional",
            passed=False,
            path=git_bin,
            details=f"Wyjątek podczas uruchamiania git: {e}",
            remedy="Sprawdź uprawnienia do uruchamiania polecenia git.",
        )


def check_raycast() -> DiagnosticItem:
    """Weryfikuje obecność opcjonalnego narzędzia Raycast (rekomendowane dla macOS)."""
    system = platform.system()
    found_path: Optional[Path] = None

    if system == "Darwin":
        home = Path.home()
        candidates = [
            Path("/Applications/Raycast.app"),
            home / "Applications" / "Raycast.app",
        ]
        for c in candidates:
            if c.exists():
                found_path = c
                break

    which_path = shutil.which("raycast")
    if not found_path and which_path:
        found_path = Path(which_path)

    passed = found_path is not None
    if passed:
        details = f"Raycast odnaleziony w lokalizacji: {found_path}"
        remedy = None
    else:
        details = "Raycast nie jest zainstalowany (opcjonalny skrót szybkiego zrzutu i telemetrii)"
        remedy = "Opcjonalnie zainstaluj Raycast z https://raycast.com"

    return DiagnosticItem(
        name="Raycast",
        category="optional",
        passed=passed,
        path=str(found_path) if found_path else None,
        details=details,
        remedy=remedy,
    )


def check_wispr_flow() -> DiagnosticItem:
    """Weryfikuje obecność opcjonalnego dyktafonu AI Wispr Flow."""
    system = platform.system()
    found_path: Optional[Path] = None

    if system == "Darwin":
        home = Path.home()
        candidates = [
            Path("/Applications/Wispr Flow.app"),
            home / "Applications" / "Wispr Flow.app",
            Path("/Applications/Wispr.app"),
        ]
        for c in candidates:
            if c.exists():
                found_path = c
                break

    which_path = shutil.which("wispr-flow") or shutil.which("wispr")
    if not found_path and which_path:
        found_path = Path(which_path)

    passed = found_path is not None
    if passed:
        details = f"Wispr Flow odnaleziony w lokalizacji: {found_path}"
        remedy = None
    else:
        details = "Wispr Flow nie jest zainstalowany (opcjonalny dyktafon notatek głosowych)"
        remedy = "Opcjonalnie pobierz Wispr Flow z https://wisprflow.ai"

    return DiagnosticItem(
        name="Wispr Flow",
        category="optional",
        passed=passed,
        path=str(found_path) if found_path else None,
        details=details,
        remedy=remedy,
    )


def scan_icloud_placeholders(target_dir: Optional[Path] = None, max_results: int = 20) -> List[Path]:
    """Skanuje katalog pod kątem placeholderów iCloud (*.icloud), świadczących o włączonym Optimize Mac Storage."""
    search_root = target_dir
    if search_root is None:
        icloud_obs = Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents"
        if icloud_obs.is_dir():
            search_root = icloud_obs
    if search_root is None or not search_root.is_dir():
        return []

    found: List[Path] = []
    base_depth = len(search_root.parts)
    for root, dirs, files in os.walk(search_root):
        cur_depth = len(Path(root).parts) - base_depth
        if cur_depth >= 3:
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if d not in {".git", ".obsidian", "__pycache__", ".venv", "venv", "node_modules"}]
        for fname in files:
            if fname.endswith(".icloud"):
                found.append(Path(root) / fname)
                if len(found) >= max_results:
                    return found
    return found


def scan_icloud_hazards(target_dir: Path) -> Dict[str, List[str]]:
    """Wykrywa niedozwolone katalogi (.git, .venv, venv, node_modules) oraz placeholdery .icloud w ścieżce iCloud."""
    hazards: Dict[str, List[str]] = {
        "forbidden_dirs": [],
        "icloud_placeholders": [],
    }
    if not target_dir.is_dir():
        return hazards

    forbidden_names = {".git", ".venv", "venv", "node_modules"}
    base_depth = len(target_dir.parts)
    for root, dirs, files in os.walk(target_dir):
        cur_depth = len(Path(root).parts) - base_depth
        kept_dirs: List[str] = []
        for d in dirs:
            if d in forbidden_names:
                rel = str((Path(root) / d).relative_to(target_dir))
                hazards["forbidden_dirs"].append(rel)
            elif cur_depth < 3 and d not in {"__pycache__", ".obsidian"}:
                kept_dirs.append(d)
        dirs[:] = kept_dirs

        for fname in files:
            if fname.endswith(".icloud"):
                rel = str((Path(root) / fname).relative_to(target_dir))
                hazards["icloud_placeholders"].append(rel)
                if len(hazards["icloud_placeholders"]) >= 20:
                    return hazards
    return hazards


def check_icloud_storage(target_dir: Optional[Path] = None) -> DiagnosticItem:
    """Weryfikuje stan 'Optimize Mac Storage = OFF' na macOS przy użyciu iCloud Drive."""
    if platform.system() != "Darwin":
        return DiagnosticItem(
            name="iCloud Optimize Storage",
            category="optional",
            passed=True,
            details="Nie dotyczy (system inny niż macOS).",
        )

    placeholders = scan_icloud_placeholders(target_dir=target_dir)
    if placeholders:
        return DiagnosticItem(
            name="iCloud Optimize Storage",
            category="critical",
            passed=False,
            path=str(placeholders[0]),
            details=f"Wykryto {len(placeholders)} plików placeholderowych .icloud (Optimize Mac Storage jest włączone).",
            remedy="Wyłącz 'Optymalizuj pamięć Maca' (Optimize Mac Storage = OFF) w Ustawienia Systemowe -> Apple ID -> iCloud -> iCloud Drive.",
        )

    try:
        res = subprocess.run(
            ["defaults", "read", "com.apple.bird", "optimize-storage"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip() == "1":
            return DiagnosticItem(
                name="iCloud Optimize Storage",
                category="critical",
                passed=False,
                details="Ustawienie systemowe com.apple.bird optimize-storage = 1 (włączone).",
                remedy="Wyłącz 'Optymalizuj pamięć Maca' (Optimize Mac Storage = OFF) w ustawieniach iCloud.",
            )
    except Exception:
        pass

    return DiagnosticItem(
        name="iCloud Optimize Storage",
        category="optional",
        passed=True,
        details="Brak wykrytych placeholderów .icloud (Optimize Mac Storage = OFF lub brak plików w chmurze).",
    )


def clear_macos_quarantine(path: Path) -> bool:
    """Zdejmuje atrybut Gatekeepera com.apple.quarantine z pliku na macOS (np. .command, .sh)."""
    if platform.system() != "Darwin" or not path.exists():
        return False
    xattr_bin = shutil.which("xattr")
    if not xattr_bin:
        return False
    try:
        res = subprocess.run(
            [xattr_bin, "-d", "com.apple.quarantine", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.0,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def run_preflight_diagnostics() -> PreflightReport:
    """Przeprowadza pełną diagnostykę środowiska i zwraca ustrukturyzowany raport."""
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    items = [
        check_python(),
        check_obsidian(),
        check_git(),
        check_raycast(),
        check_wispr_flow(),
        check_icloud_storage(),
    ]

    return PreflightReport(
        os_info=os_info,
        python_version=py_ver,
        items=items,
    )
