"""lab_core.quality.inspectors.portability - Inspektor przenośności konfiguracji (Zero Host-Paths)
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

from lab_core.quality.context import QualityContext, QualityInspector


class PortabilityInspector(QualityInspector):
    """Weryfikuje brak bezwzględnych ścieżek kont użytkownika w konfiguracjach .obsidian/ oraz poprawność schematów."""
    name: str = "portability"

    FORBIDDEN_USER_PREFIXES = (
        "/Users/",
        "/home/",
        "C:\\Users",
        "C:/Users",
    )

    def inspect(self, ctx: QualityContext) -> None:
        violations: List[str] = []
        obsidian_dir = ctx.vault_root / ".obsidian"
        shell_commands_data = obsidian_dir / "plugins" / "obsidian-shellcommands" / "data.json"

        # 1. Sprawdź data.json shellcommands jeśli istnieje
        if obsidian_dir.is_dir() and shell_commands_data.is_file():
            try:
                content = shell_commands_data.read_text(encoding="utf-8")
                # Sprawdź twarde ścieżki użytkownika
                for prefix in self.FORBIDDEN_USER_PREFIXES:
                    if prefix in content:
                        ctx.add_finding(
                            level="error",
                            category="portability",
                            path=str(shell_commands_data.relative_to(ctx.vault_root)),
                            message=f"Wykryto prywatną ścieżkę konta użytkownika ('{prefix}') w konfiguracji wtyczki.",
                        )
                        violations.append(prefix)

                # Sprawdź poprawność schematu prompts
                data = json.loads(content)
                prompts = data.get("prompts", [])
                if not isinstance(prompts, list):
                    ctx.add_finding(
                        level="error",
                        category="portability",
                        path=str(shell_commands_data.relative_to(ctx.vault_root)),
                        message="Pole 'prompts' w data.json musi być listą.",
                    )
                    violations.append("prompts_not_list")
                else:
                    for p in prompts:
                        if isinstance(p, dict) and "fields" not in p:
                            ctx.add_finding(
                                level="error",
                                category="portability",
                                path=str(shell_commands_data.relative_to(ctx.vault_root)),
                                message=f"Wpis prompt '{p.get('id', 'unknown')}' nie posiada tablicy 'fields' (grozi błędem 'fields is not iterable').",
                            )
                            violations.append("prompt_missing_fields")

                # Sprawdź czy komendy używają {{vault_path}} i czy nie hardkodują interpretera
                for sc in data.get("shell_commands", []):
                    cmd = sc.get("platform_specific_commands", {}).get("default", "")
                    if ("08 Zespół AI" in cmd or "set_theme.py" in cmd or "lab-export-pdf.py" in cmd) and "{{vault_path}}" not in cmd:
                        ctx.add_finding(
                            level="error",
                            category="portability",
                            path=str(shell_commands_data.relative_to(ctx.vault_root)),
                            message=f"Komenda '{sc.get('id')}' odwołuje się do skryptu LAB bez zmiennej dynamicznej '{{{{vault_path}}}}'.",
                        )
                        violations.append(f"command_{sc.get('id')}_no_vault_path")

            except Exception as e:
                ctx.add_finding(
                    level="error",
                    category="portability",
                    path=str(shell_commands_data.relative_to(ctx.vault_root)),
                    message=f"Nie udało się sparsować pliku konfiguracyjnego wtyczki: {e}",
                )
                violations.append("json_parse_error")

        
        # 2. Sprawdź zakaz hardkodowania bytów instancji (PR-xxx, RS-xxx) w kodzie produkcyjnym silnika
        scripts_dir = ctx.vault_root / "08 Zespół AI" / "Skrypty"
        if scripts_dir.is_dir():
            id_pattern = re.compile(r'\b(PR|RS)-\d{3}\b')
            for py_path in scripts_dir.rglob("*.py"):
                if "tests" in py_path.parts:
                    continue
                try:
                    code_text = py_path.read_text(encoding="utf-8")
                except Exception:
                    continue

                for line_no, line in enumerate(code_text.splitlines(), start=1):
                    # Ignoruj czyste komentarze i docstringi
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    m = id_pattern.search(line)
                    if m:
                        # Sprawdź czy to nie jest regex lub ogólny wzorzec
                        matched_id = m.group(0)
                        # Ignoruj definicje regex / formatowania jeśli są generyczne
                        if 're.compile' in line or 'r"' in line or "r'" in line:
                            continue
                        rel_py = py_path.relative_to(ctx.vault_root)
                        ctx.add_finding(
                            level="error",
                            category="portability",
                            path=str(rel_py),
                            message=(
                                f"Naruszenie Dyrektywy Architektonicznej (Zakaz hardkodowania encji instancji w silniku): "
                                f"wykryto zahardkodowany identyfikator '{matched_id}' w linii {line_no}: {stripped[:80]}"
                            ),
                        )
                        violations.append(f"hardcoded_id_{matched_id}")

            # 3. Sprawdź zakaz twardo zakodowanych nazw instancji (Studio, JAZZ, ICOR, PKA, Larry) oraz twardych 'instance: "LAB"' / 'ws_dir / "Dashboard LAB.md"'
            legacy_instance_re = re.compile(r"\b(Studio|JAZZ|myICOR|ICOR|myPKA|Larry|PKA)\b")
            hardcoded_lab_re = re.compile(r"""instance:\s*["']LAB["']|ws_dir\s*/\s*["']Dashboard LAB\.md["']""")
            for py_path in scripts_dir.rglob("*.py"):
                if "tests" in py_path.parts or py_path.name == "portability.py":
                    continue
                try:
                    code_text = py_path.read_text(encoding="utf-8")
                except Exception:
                    continue

                rel_py = py_path.relative_to(ctx.vault_root)
                for line_no, line in enumerate(code_text.splitlines(), start=1):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    m_legacy = legacy_instance_re.search(line)
                    if m_legacy:
                        token = m_legacy.group(0)
                        ctx.add_finding(
                            level="error",
                            category="portability",
                            path=str(rel_py),
                            message=(
                                f"Naruszenie przenośności instancji: wykryto dawną nazwę instancji '{token}' "
                                f"w linii {line_no}: {stripped[:80]}"
                            ),
                        )
                        violations.append(f"legacy_instance_{token}")

                    m_lab = hardcoded_lab_re.search(line)
                    if m_lab:
                        ctx.add_finding(
                            level="error",
                            category="portability",
                            path=str(rel_py),
                            message=(
                                f"Naruszenie przenośności instancji: wykryto twardo zakodowane 'LAB' / 'Dashboard LAB.md' "
                                f"zamiast ConfigManager w linii {line_no}: {stripped[:80]}"
                            ),
                        )
                        violations.append(f"hardcoded_lab_{rel_py.name}_{line_no}")

        ctx.stats["portability_violations"] = len(violations)
