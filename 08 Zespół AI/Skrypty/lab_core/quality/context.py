"""lab_core.quality.context - Struktury danych i protokół inspektora jakości LAB 3.0
Standard: 100% Python Standard Library (dataclasses, typing). Zero-Pip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from lab_core.config import ConfigManager


@dataclass
class Finding:
    """Pojedyncze ustalenie / zdarzenie audytowe wykryte przez inspektora."""
    level: str  # "error", "warning", "info"
    category: str  # "rooms", "readme", "management_files", "inbox", "frontmatter", "links", "products", "projects", "workshop", "sprint", "assets", "policies"
    message: str
    path: Optional[str] = None
    room: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "category": self.category,
            "message": self.message,
            "path": self.path,
            "room": self.room,
        }


@dataclass
class QualityReport:
    """Raport końcowy z inspekcji jakościowej całego skarbca."""
    status: str  # "ok", "attention", "broken"
    vault_root: str
    instance_name: str
    rooms_count: int
    findings: List[Finding] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    strict_checkpoint: bool = False
    audit_legacy: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "vault_root": self.vault_root,
            "instance_name": self.instance_name,
            "rooms_count": self.rooms_count,
            "stats": self.stats,
            "findings_count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class QualityContext:
    """Kontekst operacyjny przekazywany do każdego polimorficznego inspektora."""
    vault_root: Path
    config: ConfigManager
    findings: List[Finding] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    strict_checkpoint: bool = False
    audit_legacy: bool = False

    def add_finding(
        self,
        level: str,
        category: str,
        message: str,
        path: Optional[str] = None,
        room: Optional[str] = None,
    ) -> Finding:
        finding = Finding(
            level=level,
            category=category,
            message=message,
            path=path,
            room=room,
        )
        self.findings.append(finding)
        return finding


class QualityInspector(Protocol):
    """Protokół polimorficznego inspektora jakości (Pythonic OOP)."""
    name: str

    def inspect(self, ctx: QualityContext) -> None:
        """Przeprowadza inspekcję i dopisuje ustalenia do ctx.findings oraz metryki do ctx.stats."""
        ...
