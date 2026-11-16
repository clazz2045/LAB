"""lab_core.quality.inspectors.inbox - Inspektor stanu Skrzynki i polityki drain-to-zero
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

from lab_core.config import ConfigError
from lab_core.constants import IGNORED_FILES
from lab_core.quality.context import QualityContext, QualityInspector


class InboxInspector(QualityInspector):
    """Weryfikuje stan Skrzynki (liczba elementów do przetworzenia, polityka drain-to-zero)."""
    name: str = "inbox"

    def inspect(self, ctx: QualityContext) -> None:
        try:
            inbox_room = ctx.config.get_room_by_role("ingestion")
        except ConfigError:
            try:
                inbox_room = ctx.config.get_room("01")
            except ConfigError:
                return

        inbox_dir = ctx.vault_root / inbox_room.path
        if not inbox_dir.is_dir():
            return

        items = [
            it
            for it in inbox_dir.iterdir()
            if not it.name.startswith(".") and it.name not in IGNORED_FILES and it.name != "_archiwum"
        ]
        count = len(items)
        ctx.stats["inbox_items"] = count

        if count > 0:
            level = "error" if count > 15 else "warning"
            item_names = ", ".join(f"'{x.name}'" for x in items[:3])
            more_str = f" i {count - 3} innych" if count > 3 else ""
            ctx.add_finding(
                level=level,
                category="inbox",
                room=inbox_room.id,
                path=inbox_room.path,
                message=f"Skrzynka zawiera {count} nieprzetworzonych elementów ({item_names}{more_str}) - naruszenie drain-to-zero",
            )
