"""lab_core.quality.inspectors.rooms - Inspektor obecności i integralności 8 pokoi rdzenia
Standard: 100% Python Standard Library. Zero-Pip.
"""

from __future__ import annotations

from lab_core.quality.context import QualityContext, QualityInspector


class RoomsInspector(QualityInspector):
    """Weryfikuje fizyczną obecność wszystkich pokoi zdefiniowanych w konfiguracji."""
    name: str = "rooms"

    def inspect(self, ctx: QualityContext) -> None:
        verified = 0
        for room_id, room in ctx.config.rooms.items():
            room_path = ctx.vault_root / room.path
            if not room_path.is_dir():
                ctx.add_finding(
                    level="error",
                    category="rooms",
                    room=room.id,
                    path=room.path,
                    message=f"Brak fizycznego katalogu pokoju '{room.name}' pod ścieżką '{room.path}' (rola: {room.role})",
                )
            else:
                verified += 1
        ctx.stats["verified_rooms"] = verified
