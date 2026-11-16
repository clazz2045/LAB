"""lab_core.routing - Polimorficzny silnik reguł routingu drenażu (DrainRouter)
Standard inżynieryjny: 100% Python Standard Library (typing, pathlib, datetime, re)
Zero zewnętrznych zależności pip.
Wzorzec: Chain of Responsibility / Strategy Pattern z protokołem strukturalnym DrainRoute.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union

from .config import ConfigError, ConfigManager
from .env import find_vault_root
from .markdown import parse_frontmatter


def extract_date(filename: str) -> Optional[datetime.date]:
    """Wyciąga datę YYYY-MM-DD z nazwy pliku lub zwraca None."""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    if match:
        try:
            parts = [int(p) for p in match.group(1).split("-")]
            return datetime.date(parts[0], parts[1], parts[2])
        except ValueError:
            return None
    return None


class DrainContext:
    """Kontekst ewaluacji reguły drenażu dla pojedynczego elementu."""

    def __init__(
        self,
        vault_root: Path,
        config: ConfigManager,
        item_path: Path,
        dt: Optional[datetime.date] = None,
    ) -> None:
        self.vault_root = vault_root
        self.config = config
        self.item_path = item_path
        self.dt = dt
        self.suffix = item_path.suffix.lower()
        self.is_file = item_path.is_file()

        self._metadata_loaded: bool = False
        self._frontmatter: Dict[str, Any] = {}
        self._body: str = ""

    def _ensure_metadata(self) -> None:
        if not self._metadata_loaded:
            self._metadata_loaded = True
            if self.suffix == ".md" and self.is_file:
                try:
                    fm, body = parse_frontmatter(self.item_path)
                    self._frontmatter = fm
                    self._body = body
                except Exception:
                    self._frontmatter = {}
                    self._body = ""

    @property
    def frontmatter(self) -> Dict[str, Any]:
        self._ensure_metadata()
        return self._frontmatter

    @property
    def body(self) -> str:
        self._ensure_metadata()
        return self._body

    @property
    def target_date(self) -> datetime.date:
        """Zwraca datę wyekstrahowaną z nazwy pliku, przekazany parametr dt lub dzisiejszą datę."""
        return extract_date(self.item_path.name) or self.dt or datetime.date.today()

    @property
    def inbox_dir(self) -> Path:
        """Katalog pokoju Skrzynka."""
        try:
            room = self.config.get_room_by_role("ingestion")
        except ConfigError:
            room = self.config.get_room("01")
        return self.vault_root / room.path

    @property
    def assets_dir(self) -> Path:
        """Katalog pokoju Zasoby."""
        try:
            room = self.config.get_room_by_role("reference-and-assets")
        except ConfigError:
            room = self.config.get_room("06")
        return self.vault_root / room.path


class DrainRoute(Protocol):
    """Protokół reguły routingu drenażu."""

    def matches(self, ctx: DrainContext) -> bool:
        """Weryfikuje czy dany element spełnia kryteria trasy."""
        ...

    def resolve(self, ctx: DrainContext) -> Path:
        """Zwraca docelowy katalog, do którego należy przenieść element."""
        ...


class DocumentsRoute:
    """Dokumenty i arkusze (.pdf, .docx, .epub itp.) -> 06 Zasoby/Dokumenty."""

    EXTENSIONS = {".pdf", ".docx", ".doc", ".epub", ".xlsx", ".pptx", ".csv"}

    def matches(self, ctx: DrainContext) -> bool:
        return ctx.suffix in self.EXTENSIONS

    def resolve(self, ctx: DrainContext) -> Path:
        return ctx.assets_dir / "Dokumenty"


class ImagesRoute:
    """Pliki graficzne (.png, .jpg, .webp itp.) -> 06 Zasoby/Obrazy."""

    EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}

    def matches(self, ctx: DrainContext) -> bool:
        return ctx.suffix in self.EXTENSIONS

    def resolve(self, ctx: DrainContext) -> Path:
        return ctx.assets_dir / "Obrazy"


class AudioRoute:
    """Pliki dźwiękowe (.mp3, .m4a, .wav itp.) -> 06 Zasoby/Audio."""

    EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}

    def matches(self, ctx: DrainContext) -> bool:
        return ctx.suffix in self.EXTENSIONS

    def resolve(self, ctx: DrainContext) -> Path:
        return ctx.assets_dir / "Audio"


class RawThoughtCaptureRoute:
    """
    Surowe zrzuty myśli (Wispr_*, type: raw-capture, source: wispr-flow)
    -> 01 Skrzynka/_archiwum/YYYY-MM/
    Bezwzględny zakaz przenoszenia surowych zrzutów do 06 Zasoby/!
    """

    @classmethod
    def is_raw_capture(cls, ctx: DrainContext) -> bool:
        if ctx.item_path.name.startswith("Wispr_"):
            return True
        if ctx.suffix == ".md" and ctx.is_file:
            fm_type = str(ctx.frontmatter.get("type", "")).strip().lower()
            fm_source = str(ctx.frontmatter.get("source", "")).strip().lower()
            if fm_type == "raw-capture" or fm_source == "wispr-flow":
                return True
            # Zabezpieczenie na wypadek braku frontmattera YAML (surowy tekst)
            if "source: wispr-flow" in ctx.body or "type: raw-capture" in ctx.body:
                return True
        return False

    def matches(self, ctx: DrainContext) -> bool:
        return self.is_raw_capture(ctx)

    def resolve(self, ctx: DrainContext) -> Path:
        month_str = ctx.target_date.strftime("%Y-%m")
        return ctx.inbox_dir / "_archiwum" / month_str


class ReflectionNoteRoute:
    """
    Notatki zawierające osobistą refleksję Właściciela Systemu:
    (pole frontmattera 'my_thought', note_type: reflection, type: reflection lub tag 'refleksja')
    -> 05 Baza Wiedzy/Refleksje

    Zachowuje oryginalny tytuł nadany przez człowieka i traktuje artykuł zewnętrzny
    jedynie jako przyczynek/kontekst, a nie temat nadrzędny.
    """

    def matches(self, ctx: DrainContext) -> bool:
        if ctx.suffix != ".md" or not ctx.is_file:
            return False

        if RawThoughtCaptureRoute.is_raw_capture(ctx):
            return False

        fm = ctx.frontmatter
        if bool(fm.get("my_thought")) or bool(fm.get("refleksja")):
            return True

        note_type = str(fm.get("note_type", "")).strip().lower()
        if note_type in {"reflection", "refleksja"}:
            return True

        tp = str(fm.get("type", "")).strip().lower()
        if tp in {"reflection", "refleksja"}:
            return True

        tags = fm.get("tags", [])
        if isinstance(tags, list):
            tags_lower = {str(t).strip().lower() for t in tags}
            if {"refleksja", "refleksje", "mysl", "reflection"}.intersection(tags_lower):
                return True

        return False

    def resolve(self, ctx: DrainContext) -> Path:
        try:
            room = ctx.config.get_room_by_role("atomic-synthesis-and-research")
        except ConfigError:
            room = ctx.config.get_room("05")
        return ctx.vault_root / room.path / "Refleksje"


class ReferenceNoteRoute:
    """
    Notatki referencyjne SAT:
    (type: note, note_type: reference LUB zdefiniowany source_url, o ile NIE są raw-capture)
    -> 06 Zasoby/Notatki
    """

    def matches(self, ctx: DrainContext) -> bool:
        if ctx.suffix != ".md" or not ctx.is_file:
            return False

        if RawThoughtCaptureRoute.is_raw_capture(ctx):
            return False

        fm = ctx.frontmatter
        is_typed_ref = (
            str(fm.get("type", "")).strip().lower() == "note"
            and str(fm.get("note_type", "")).strip().lower() == "reference"
        )
        has_source_url = bool(fm.get("source_url")) or ("source_url:" in ctx.body)

        return is_typed_ref or has_source_url

    def resolve(self, ctx: DrainContext) -> Path:
        return ctx.assets_dir / "Notatki"


class FallbackArchiveRoute:
    """
    Wsady surowe i pozostałe pliki/katalogi
    -> 01 Skrzynka/_archiwum/YYYY-MM/
    """

    def matches(self, ctx: DrainContext) -> bool:
        return True

    def resolve(self, ctx: DrainContext) -> Path:
        month_str = ctx.target_date.strftime("%Y-%m")
        return ctx.inbox_dir / "_archiwum" / month_str


class DrainRouter:
    """
    Zarządca reguł drenażu Skrzynki w LAB 3.0.
    Ewaluuje kolekcję tras i zwraca deterministyczny katalog docelowy.
    """

    def __init__(
        self,
        vault_root: Optional[Union[str, Path]] = None,
        config: Optional[ConfigManager] = None,
        routes: Optional[List[DrainRoute]] = None,
    ) -> None:
        self.vault_root = Path(vault_root).resolve() if vault_root else find_vault_root()
        self.config = config or ConfigManager(vault_root=self.vault_root)
        self.routes: List[DrainRoute] = routes or [
            DocumentsRoute(),
            ImagesRoute(),
            AudioRoute(),
            RawThoughtCaptureRoute(),
            ReflectionNoteRoute(),
            ReferenceNoteRoute(),
            FallbackArchiveRoute(),
        ]

    def resolve_destination(
        self,
        item_path: Path,
        dt: Optional[datetime.date] = None,
    ) -> Path:
        """Deterministycznie wyznacza katalog docelowy dla wskazanego elementu."""
        ctx = DrainContext(
            vault_root=self.vault_root,
            config=self.config,
            item_path=item_path,
            dt=dt,
        )
        for route in self.routes:
            if route.matches(ctx):
                return route.resolve(ctx)
        return FallbackArchiveRoute().resolve(ctx)
