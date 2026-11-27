"""lab_core.skills - Rejestr, parser i walidator 3-poziomowego systemu skilli LAB 3.0
Standard inżynieryjny: 100% Python Standard Library. Zero zewnętrznych zależności pip.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.dont_write_bytecode = True

from .config import ConfigError, ConfigManager
from .constants import IGNORED_DIRS
from .markdown import normalize_nfc, parse_frontmatter

SKILLS_DIRNAME: str = "Skille"
SKILL_ENTRY_FILE: str = "SKILL.md"
VALID_PROJECT_TYPES: frozenset[str] = frozenset({"internal", "commercial"})
VALID_SKILL_MODES: frozenset[str] = frozenset({"standard", "custom"})
REQUIRED_SKILL_FIELDS: Tuple[str, ...] = ("name", "description")
RECOMMENDED_SKILL_FIELDS: Tuple[str, ...] = ("role", "source")


@dataclass
class SkillMetadata:
    """Metadane pojedynczego skilla z półki 08 Zespół AI/Skille/<skill_slug>/SKILL.md."""

    slug: str
    name: str
    description: str
    role: str = ""
    source: str = ""
    path: str = ""
    has_skill_file: bool = True
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "role": self.role,
            "source": self.source,
            "path": self.path,
            "valid": self.valid,
        }


@dataclass
class ProjectSkillConfig:
    """Konfiguracja skilli odczytana z frontmattera projektu (PLAN.md lub karta projektu)."""

    project_type: str = "internal"
    skill_mode: str = "standard"
    skills_map: Dict[str, List[str]] = field(default_factory=dict)
    raw_project_type: str = ""
    raw_skill_mode: str = ""
    source_file: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_type": self.project_type,
            "skill_mode": self.skill_mode,
            "skills_map": self.skills_map,
        }


def get_skills_dir(vault_root: Path, config: Optional[ConfigManager] = None) -> Path:
    """Rozwiązuje ścieżkę do półki skilli (08 Zespół AI/Skille/) przez ConfigManager."""
    root = Path(vault_root).resolve()
    ai_rel = "08 Zespół AI"
    if config is not None:
        try:
            ai_rel = config.get_room_by_role("governance-and-engine").path
        except ConfigError:
            try:
                ai_rel = config.get_room("08").path
            except ConfigError:
                ai_rel = "08 Zespół AI"
    return root / ai_rel / SKILLS_DIRNAME


def _coerce_str(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    if value is None:
        return ""
    return str(value).strip()


def _has_valid_yaml_delimiters(text: str) -> bool:
    lines = normalize_nfc(text).splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    return any(line.strip() == "---" for line in lines[1:])


def parse_skill_file(
    skill_md_path: Path,
    vault_root: Optional[Path] = None,
    slug: Optional[str] = None,
) -> SkillMetadata:
    """Parsuje i waliduje pojedynczy plik SKILL.md."""
    p = Path(skill_md_path).resolve()
    resolved_slug = normalize_nfc(slug or p.parent.name)

    if vault_root is not None:
        try:
            rel_path = normalize_nfc(p.relative_to(Path(vault_root).resolve()).as_posix())
        except ValueError:
            rel_path = normalize_nfc(p.as_posix())
    else:
        rel_path = normalize_nfc(p.as_posix())

    if not p.is_file():
        return SkillMetadata(
            slug=resolved_slug,
            name=resolved_slug,
            description="",
            path=rel_path,
            has_skill_file=False,
            valid=False,
            errors=[f"Brak pliku '{SKILL_ENTRY_FILE}' dla skilla '{resolved_slug}'."],
        )

    try:
        raw_text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return SkillMetadata(
            slug=resolved_slug,
            name=resolved_slug,
            description="",
            path=rel_path,
            has_skill_file=True,
            valid=False,
            errors=[f"Błąd odczytu pliku '{rel_path}': {exc}"],
        )

    errors: List[str] = []
    warnings: List[str] = []

    if not _has_valid_yaml_delimiters(raw_text):
        errors.append(f"Brak lub niedomknięty frontmatter YAML w '{rel_path}'.")
        fm: Dict[str, Any] = {}
    else:
        fm, _ = parse_frontmatter(raw_text)

    name = _coerce_str(fm.get("name"))
    description = _coerce_str(fm.get("description"))
    role = _coerce_str(fm.get("role"))
    source = _coerce_str(fm.get("source"))

    if not name:
        errors.append(f"Brak wymaganego pola 'name' we frontmatterze '{rel_path}'.")
        name = resolved_slug
    if not description:
        errors.append(f"Brak wymaganego pola 'description' we frontmatterze '{rel_path}'.")
    if not role:
        warnings.append(f"Brak zalecanego pola 'role' we frontmatterze '{rel_path}'.")
    if not source:
        warnings.append(f"Brak zalecanego pola 'source' we frontmatterze '{rel_path}'.")

    return SkillMetadata(
        slug=resolved_slug,
        name=name,
        description=description,
        role=role,
        source=source,
        path=rel_path,
        has_skill_file=True,
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def discover_skills(vault_root: Path, config: Optional[ConfigManager] = None) -> List[SkillMetadata]:
    """Skanuje półkę skilli (08 Zespół AI/Skille/<skill_slug>/SKILL.md) i zwraca posortowaną listę."""
    root = Path(vault_root).resolve()
    skills_dir = get_skills_dir(root, config=config)
    if not skills_dir.is_dir():
        return []

    found: List[SkillMetadata] = []
    for entry in sorted(skills_dir.iterdir(), key=lambda x: normalize_nfc(x.name).lower()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name in IGNORED_DIRS:
            continue
        skill_md = entry / SKILL_ENTRY_FILE
        found.append(parse_skill_file(skill_md, vault_root=root, slug=entry.name))
    return found


def default_environment_skill_dirs(vault_root: Optional[Path] = None) -> List[Path]:
    """Zwraca domyślną listę katalogów skilli w środowisku uruchomieniowym hosta."""
    dirs: List[Path] = []
    if vault_root is not None:
        dirs.append(Path(vault_root).resolve() / ".agents" / "skills")
    home = Path.home()
    dirs.extend([
        home / ".agents" / "skills",
        home / ".gemini" / "antigravity" / "skills",
        home / ".gemini" / "antigravity" / "builtin" / "skills",
        home / ".gemini" / "config" / "skills",
        home / ".claude" / "skills",
        home / ".codex" / "skills",
    ])
    plugins_dir = home / ".gemini" / "config" / "plugins"
    if plugins_dir.is_dir():
        try:
            for p_dir in sorted(plugins_dir.iterdir()):
                if p_dir.is_dir() and (p_dir / "skills").is_dir():
                    dirs.append(p_dir / "skills")
        except OSError:
            pass
    return dirs


def discover_environment_skills(
    vault_root: Optional[Path] = None,
    search_dirs: Optional[List[Path]] = None,
) -> Dict[str, Path]:
    """Zwraca mapę {znormalizowany_klucz_skilla: ścieżka_SKILL.md} dla skilli dostępnych w środowisku."""
    dirs = search_dirs if search_dirs is not None else default_environment_skill_dirs(vault_root)
    registry: Dict[str, Path] = {}
    for base in dirs:
        if not base or not base.is_dir():
            continue
        try:
            entries = sorted(base.iterdir(), key=lambda x: x.name.lower())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            skill_md = entry / SKILL_ENTRY_FILE
            if not skill_md.is_file():
                continue
            slug_key = normalize_nfc(entry.name).strip().lower()
            registry.setdefault(slug_key, skill_md)
            meta = parse_skill_file(skill_md, slug=entry.name)
            if meta.name:
                name_key = normalize_nfc(meta.name).strip().lower()
                registry.setdefault(name_key, skill_md)
    return registry


def _strip_yaml_comment(text: str) -> str:
    """Usuwa komentarz # poza cudzysłowami."""
    in_quote: Optional[str] = None
    for idx, ch in enumerate(text):
        if ch in {"'", '"'}:
            if in_quote is None:
                in_quote = ch
            elif in_quote == ch:
                in_quote = None
        elif ch == "#" and in_quote is None:
            return text[:idx].rstrip()
    return text.rstrip()


def _parse_inline_list(val: str) -> List[str]:
    inner = val.strip()[1:-1].strip()
    if not inner:
        return []
    return [item.strip().strip("\"'") for item in inner.split(",") if item.strip().strip("\"'")]


def _parse_inline_skills_map(val: str) -> Dict[str, List[str]]:
    inner = val.strip()[1:-1].strip()
    if not inner:
        return {}
    result: Dict[str, List[str]] = {}
    # Podziel na pary na najwyższym poziomie (poza nawiasami [])
    parts: List[str] = []
    depth = 0
    start = 0
    for idx, ch in enumerate(inner):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(inner[start:idx])
            start = idx + 1
    parts.append(inner[start:])

    for part in parts:
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        role_k = normalize_nfc(k.strip().strip("\"'"))
        v_clean = v.strip()
        if not role_k:
            continue
        if v_clean.startswith("[") and v_clean.endswith("]"):
            result[role_k] = _parse_inline_list(v_clean)
        elif v_clean in {"", "null", "~", "None"}:
            result[role_k] = []
        else:
            result[role_k] = [v_clean.strip("\"'")]
    return result


def parse_skills_map_from_frontmatter_lines(fm_lines: List[str]) -> Dict[str, List[str]]:
    """Deterministycznie parsuje zagnieżdżoną mapę `skills_map` z linii YAML frontmattera."""
    in_skills_map = False
    current_role: Optional[str] = None
    skills_map: Dict[str, List[str]] = {}

    for raw_line in fm_lines:
        line = _strip_yaml_comment(raw_line.rstrip("\r\n"))
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if not in_skills_map:
            if indent == 0 and stripped.startswith("skills_map:"):
                val = stripped.split(":", 1)[1].strip()
                if val.startswith("{") and val.endswith("}"):
                    return _parse_inline_skills_map(val)
                if val in {"", "null", "~"}:
                    in_skills_map = True
                    continue
                return {}
            continue

        # Jeśli wróciliśmy na poziom 0, blok skills_map się zakończył
        if indent == 0:
            break

        if stripped.startswith("- "):
            if current_role is not None:
                item = stripped[2:].strip().strip("\"'")
                if item:
                    skills_map.setdefault(current_role, []).append(item)
            continue

        if ":" in stripped:
            r_key, r_val = stripped.split(":", 1)
            current_role = normalize_nfc(r_key.strip().strip("\"'"))
            r_val_clean = r_val.strip()
            if not current_role:
                continue
            if r_val_clean.startswith("[") and r_val_clean.endswith("]"):
                skills_map[current_role] = _parse_inline_list(r_val_clean)
                current_role = None
            elif r_val_clean in {"", "null", "~"}:
                skills_map.setdefault(current_role, [])
            else:
                skills_map[current_role] = [r_val_clean.strip("\"'")]
                current_role = None

    return skills_map


def _extract_frontmatter_lines(text: str) -> List[str]:
    lines = normalize_nfc(text).splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return lines[1:idx]
    return []


def _read_skill_fields_from_file(md_path: Path) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    if not md_path.is_file():
        return {}, {}
    try:
        raw_text = md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}, {}
    fm, _ = parse_frontmatter(raw_text)
    fm_lines = _extract_frontmatter_lines(raw_text)
    skills_map = parse_skills_map_from_frontmatter_lines(fm_lines) if fm_lines else {}
    return fm, skills_map


def read_project_skill_config(
    project_dir: Path,
    card_path: Optional[Path] = None,
    vault_root: Optional[Path] = None,
) -> ProjectSkillConfig:
    """Odczytuje project_type, skill_mode i skills_map z PLAN.md (priorytet) lub karty projektu."""
    proj_dir = Path(project_dir).resolve()
    plan_path = proj_dir / "PLAN.md"

    resolved_card = card_path
    if resolved_card is None and proj_dir.is_dir():
        for cand in sorted(proj_dir.glob("PR-*.md")):
            resolved_card = cand
            break

    plan_fm, plan_map = _read_skill_fields_from_file(plan_path)
    card_fm, card_map = _read_skill_fields_from_file(resolved_card) if resolved_card else ({}, {})

    # Wybierz źródło dla każdego pola: PLAN.md ma pierwszeństwo przed kartą PR-*.md
    raw_pt = ""
    raw_sm = ""
    skills_map: Dict[str, List[str]] = {}
    source_path: Optional[Path] = None

    if "project_type" in plan_fm:
        raw_pt = str(plan_fm.get("project_type", "")).strip()
        source_path = plan_path
    elif "project_type" in card_fm:
        raw_pt = str(card_fm.get("project_type", "")).strip()
        source_path = resolved_card

    if "skill_mode" in plan_fm:
        raw_sm = str(plan_fm.get("skill_mode", "")).strip()
        source_path = source_path or plan_path
    elif "skill_mode" in card_fm:
        raw_sm = str(card_fm.get("skill_mode", "")).strip()
        source_path = source_path or resolved_card

    if "skills_map" in plan_fm or plan_map:
        skills_map = plan_map
        source_path = plan_path
    elif "skills_map" in card_fm or card_map:
        skills_map = card_map
        source_path = resolved_card

    if source_path is None:
        if plan_path.is_file():
            source_path = plan_path
        elif resolved_card is not None and resolved_card.is_file():
            source_path = resolved_card

    norm_pt = raw_pt.lower() if raw_pt else "internal"
    norm_sm = raw_sm.lower() if raw_sm else "standard"

    source_str: Optional[str] = None
    if source_path is not None:
        if vault_root is not None:
            try:
                source_str = normalize_nfc(source_path.resolve().relative_to(Path(vault_root).resolve()).as_posix())
            except ValueError:
                source_str = normalize_nfc(source_path.as_posix())
        else:
            source_str = normalize_nfc(source_path.as_posix())

    return ProjectSkillConfig(
        project_type=norm_pt,
        skill_mode=norm_sm,
        skills_map=skills_map,
        raw_project_type=raw_pt,
        raw_skill_mode=raw_sm,
        source_file=source_str,
    )
