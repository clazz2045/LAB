"""lab_core.config - Centralny moduł parametryzacji LAB 3.0 (Framework PZW)
Standard inżynieryjny: 100% Python Standard Library (tomllib, dataclasses, pathlib)
Zero zewnętrznych zależności pip.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .constants import CANONICAL_MANAGEMENT_FILES, CANONICAL_ROLE_KEYS
from .env import EnvironmentError, find_vault_root

# Blokada tworzenia folderów __pycache__ w przestrzeni skarbca
sys.dont_write_bytecode = True


# ==============================================================================
# WYJĄTKI ARCHITEKTONICZNE (FAIL-SAFE ERROR HANDLING)
# ==============================================================================

class ConfigError(Exception):
    """Bazowy wyjątek dla wszystkich błędów konfiguracji LAB 3.0."""
    pass


class ConfigNotFoundError(ConfigError, EnvironmentError):
    """Podnoszony, gdy plik konfiguracyjny lub korzeń skarbca nie zostanie znaleziony."""
    pass


class ConfigValidationError(ConfigError):
    """Podnoszony, gdy plik konfiguracyjny zawiera błędy składniowe, logiczne lub brakujące pola."""
    pass


# ==============================================================================
# STRUKTURY DANYCH KONFIGURACJI (SILNE TYPOWANIE)
# ==============================================================================

@dataclass(frozen=True)
class UserConfig:
    """Konfiguracja tożsamości użytkownika (Właściciela Systemu)."""
    name: str = "Użytkownik"
    formal_title: str = "Właściciel Systemu"
    role: str = "system-owner"
    call_sign: str = "Użytkownik"


@dataclass(frozen=True)
class SystemConfig:
    """Metadane instancji systemu."""
    id: str
    instance_name: str
    version: str
    language: str
    timezone: str


@dataclass(frozen=True)
class StorageConfig:
    """Zasady przechowywania i rozwiązywania ścieżek."""
    root_var: str
    relative_mode: bool
    root_indicator: str


@dataclass(frozen=True)
class ExternalStorageConfig:
    """
    Zasady przechowywania i linkowania zasobów zewnętrznych (Cold Storage).
    
    Zgodnie z Zasadą Lekkiego Skarbca:
    - Duże pliki binarne (>15MB) przechowywane są w zewnętrznym magazynie (np. Google Drive).
    - W notatkach Markdown stosowane są wyłącznie logiczne ścieżki względne, zapobiegając
      zjawisku link rot i uzależnieniu od konkretnego dostawcy chmury (Zero Vendor Lock-in).
    """
    enabled: bool = True
    heavy_file_threshold_mb: int = 15
    provider: str = "gdrive"
    periodicals_dir: str = "Czasopisma"
    root_url: str = ""
    local_mount: str = ""


@dataclass(frozen=True)
class RoomConfig:
    """
    Konfiguracja pokoju LAB 3.0.
    
    Zgodnie z Aksjomatem Nienaruszalności Rdzenia (Core Invariant Axiom):
    - role i policy to stałe, nienaruszalne reguły rdzenia systemu.
    - name, path i color to konfigurowalne parametry prezentacji instancji.
    """
    id: str
    code: str
    role: str
    policy: str
    name: str
    path: str
    color: str
    entry_file: Optional[str] = None


@dataclass(frozen=True)
class AgentConfig:
    """Profil agenta / specjalisty AI."""
    role: str
    assigned_name: str
    role_key: str = ""
    contract_file: Optional[str] = None
    default_model: Optional[str] = None
    slug: Optional[str] = None


@dataclass(frozen=True)
class DistillateConfig:
    """Konfiguracja progów limitu tokenów destylatu SAT wg typu zadania."""
    default: int = 650
    thresholds: Dict[str, int] = field(default_factory=lambda: {
        "code": 650,
        "audit": 650,
        "research": 850,
        "synthesis": 850,
    })


@dataclass(frozen=True)
class AITeamConfig:
    """Struktura Zespołu AI."""
    coordinator: AgentConfig
    governance_qa: AgentConfig
    specialists: List[AgentConfig] = field(default_factory=list)
    distillate: DistillateConfig = field(default_factory=DistillateConfig)


@dataclass(frozen=True)
class TelemetryConfig:
    """Konfiguracja budżetu telemetrii okna kontekstowego sesji."""
    session_token_budget: int = 150_000


@dataclass(frozen=True)
class PresentationConfig:
    """Konfiguracja wizualna i design tokeny."""
    theme_preset: str
    font_sans: str
    font_mono: str
    theme_preset_light: str = "dawn"
    theme_preset_dark: str = "tokyonight"
    theme_mode: str = "system"


@dataclass(frozen=True)
class HumanThoughtsPolicyConfig:
    """
    Polityka ochrony nieuporządkowanych myśli i idei człowieka.
    
    Bezwzględny zakaz samowolnego dopisywania myśli i haseł użytkownika
    do Warsztatu/Sprintu bez uprzedniej propozycji i wyraźnej zgody.
    """
    protect_unorganized_thoughts: bool = True
    forbid_auto_sprint_injection: bool = True
    require_explicit_confirmation: bool = True
    description: str = ""


@dataclass(frozen=True)
class WorkshopPolicyConfig:
    """
    Polityka struktury i higieny zadań na Warsztacie Współpracy.
    
    Na Warsztacie dozwolone są wyłącznie zadania projektowe (PR)
    i operacyjne (OP).
    Zadania badawcze (RS) należą wyłącznie do Bazy Wiedzy (05 Baza Wiedzy).
    Sekcje 'Pokoje Robocze', 'Zrzut ze Skrzynki' oraz Filar IV są zakazane.
    """
    allowed_task_types: List[str] = field(default_factory=lambda: ["PR", "OP"])
    forbidden_task_types: List[str] = field(default_factory=lambda: ["RS"])
    forbidden_sections: List[str] = field(default_factory=lambda: [
        "Pokoje Robocze",
        "Zrzut ze Skrzynki",
        "Filar IV: Badania i Synteza Wiedzy",
    ])
    description: str = ""


# ==============================================================================
# ZARZĄDCA KONFIGURACJI (CONFIG MANAGER)
# ==============================================================================

class ConfigManager:
    """
    Główny zarządca konfiguracji LAB 3.0 (Framework PZW).
    
    Odpowiada za deterministyczne wczytanie konfiguracji TOML, walidację spójności
    oraz udostępnienie silnie typowanego API dla wszystkich skryptów i agentów.
    """

    def __init__(
        self,
        config_path: Optional[Union[str, Path]] = None,
        vault_root: Optional[Union[str, Path]] = None,
    ) -> None:
        if config_path:
            self.config_path = Path(config_path).resolve()
            if not self.config_path.is_file():
                raise ConfigNotFoundError(f"Plik konfiguracyjny nie istnieje: {self.config_path}")
            self.vault_root = Path(vault_root).resolve() if vault_root else self.config_path.parent
        else:
            self.vault_root = Path(vault_root).resolve() if vault_root else find_vault_root()
            self.config_path = self.vault_root / "lab.config.toml"
            if not self.config_path.is_file():
                raise ConfigNotFoundError(f"Nie odnaleziono pliku konfiguracyjnego w korzeniu: {self.config_path}")

        self._raw_data: Dict[str, Any] = {}
        self.user: UserConfig
        self.system: SystemConfig
        self.storage: StorageConfig
        self.external_storage: ExternalStorageConfig = ExternalStorageConfig()
        self.rooms: Dict[str, RoomConfig] = {}
        self.ai_team: AITeamConfig
        self.telemetry: TelemetryConfig = TelemetryConfig()
        self.presentation: PresentationConfig
        self.human_thoughts_policy: HumanThoughtsPolicyConfig
        self.workshop_policy: WorkshopPolicyConfig

        self._load_and_validate()

    @classmethod
    def load(
        cls,
        config_path: Optional[Union[str, Path]] = None,
        vault_root: Optional[Union[str, Path]] = None,
    ) -> ConfigManager:
        """Fabryka instancji ConfigManager."""
        return cls(config_path=config_path, vault_root=vault_root)

    def _load_and_validate(self) -> None:
        """Wczytuje binarnie TOML przez tomllib i dokonuje rygorystycznej walidacji schematu."""
        try:
            with open(self.config_path, "rb") as f:
                self._raw_data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigValidationError(f"Błąd składniowy w pliku TOML '{self.config_path}': {e}") from e
        except OSError as e:
            raise ConfigError(f"Błąd wejścia/wyjścia podczas odczytu '{self.config_path}': {e}") from e

        required_sections = ["system", "storage", "rooms", "ai_team", "presentation"]
        missing_sections = [s for s in required_sections if s not in self._raw_data]
        if missing_sections:
            raise ConfigValidationError(
                f"Niekompletna konfiguracja. Brak wymaganych sekcji: {', '.join(missing_sections)}"
            )

        self._parse_user()
        self._parse_system()
        self._parse_storage()
        self._parse_rooms()
        self._parse_ai_team()
        self._parse_telemetry()
        self._parse_presentation()
        self._parse_policies()

    def _parse_user(self) -> None:
        user_dict = self._raw_data.get("user", {})
        self.user = UserConfig(
            name=str(user_dict.get("name", "Użytkownik")).strip() or "Użytkownik",
            formal_title=str(user_dict.get("formal_title", "Właściciel Systemu")).strip() or "Właściciel Systemu",
            role=str(user_dict.get("role", "system-owner")).strip() or "system-owner",
            call_sign=str(user_dict.get("call_sign", "Użytkownik")).strip() or "Użytkownik",
        )

    def _parse_system(self) -> None:
        sys_dict = self._raw_data["system"]
        required = ["id", "instance_name", "version", "language", "timezone"]
        for field_name in required:
            if field_name not in sys_dict or not str(sys_dict[field_name]).strip():
                raise ConfigValidationError(f"Sekcja [system] nie zawiera wymaganego pola: '{field_name}'")

        self.system = SystemConfig(
            id=str(sys_dict["id"]).strip(),
            instance_name=str(sys_dict["instance_name"]).strip(),
            version=str(sys_dict["version"]).strip(),
            language=str(sys_dict["language"]).strip(),
            timezone=str(sys_dict["timezone"]).strip(),
        )

    def _parse_storage(self) -> None:
        stor_dict = self._raw_data["storage"]
        self.storage = StorageConfig(
            root_var=str(stor_dict.get("root_var", "VAULT_ROOT")),
            relative_mode=bool(stor_dict.get("relative_mode", True)),
            root_indicator=str(stor_dict.get("root_indicator", "lab.config.toml")),
        )
        ext_dict = stor_dict.get("external", {})
        if isinstance(ext_dict, dict):
            self.external_storage = ExternalStorageConfig(
                enabled=bool(ext_dict.get("enabled", True)),
                heavy_file_threshold_mb=int(ext_dict.get("heavy_file_threshold_mb", 15)),
                provider=str(ext_dict.get("provider", "gdrive")).strip(),
                periodicals_dir=str(ext_dict.get("periodicals_dir", "Czasopisma")).strip(),
                root_url=str(ext_dict.get("root_url", "")).strip(),
                local_mount=str(ext_dict.get("local_mount", "")).strip(),
            )
        else:
            self.external_storage = ExternalStorageConfig()

    def _parse_rooms(self) -> None:
        rooms_raw = self._raw_data["rooms"]
        self.rooms.clear()

        items: List[Dict[str, Any]] = []
        if isinstance(rooms_raw, dict):
            for room_key, room_val in rooms_raw.items():
                if isinstance(room_val, dict):
                    room_entry = dict(room_val)
                    if "id" not in room_entry:
                        room_entry["id"] = room_key
                    items.append(room_entry)
        elif isinstance(rooms_raw, list):
            items = [dict(item) for item in rooms_raw if isinstance(item, dict)]
        else:
            raise ConfigValidationError("Sekcja [rooms] musi być tabelą słownikową lub listą pokoi.")

        if not items:
            raise ConfigValidationError("Sekcja [rooms] nie może być pusta.")

        required_fields = ["id", "code", "role", "policy", "name", "path", "color"]
        seen_ids = set()
        seen_codes = set()
        seen_roles = set()

        for item in items:
            for req in required_fields:
                if req not in item or not str(item[req]).strip():
                    raise ConfigValidationError(f"Pokój '{item.get('id', 'nieznany')}' nie posiada pola: '{req}'")

            room_id = str(item["id"]).strip()
            room_code = str(item["code"]).strip()
            room_role = str(item["role"]).strip()

            if room_id in seen_ids:
                raise ConfigValidationError(f"Zduplikowany identyfikator pokoju: '{room_id}'")
            if room_code in seen_codes:
                raise ConfigValidationError(f"Zduplikowany kod pokoju: '{room_code}'")
            if room_role in seen_roles:
                raise ConfigValidationError(f"Zduplikowana rola pokoju (rola rdzenia musi być unikalna): '{room_role}'")

            seen_ids.add(room_id)
            seen_codes.add(room_code)
            seen_roles.add(room_role)

            room_config = RoomConfig(
                id=room_id,
                code=room_code,
                role=room_role,
                policy=str(item["policy"]).strip(),
                name=str(item["name"]).strip(),
                path=str(item["path"]).strip(),
                color=str(item["color"]).strip(),
                entry_file=str(item["entry_file"]).strip() if "entry_file" in item else None,
            )
            self.rooms[room_id] = room_config

    def _parse_ai_team(self) -> None:
        ai_dict = self._raw_data["ai_team"]
        if "coordinator" not in ai_dict or "governance_qa" not in ai_dict:
            raise ConfigValidationError("Sekcja [ai_team] musi zawierać 'coordinator' oraz 'governance_qa'.")

        coord_data = ai_dict["coordinator"]
        coord_name = str(coord_data.get("assigned_name") or coord_data.get("name") or "").strip()
        coord_role_key = str(coord_data.get("role_key", "coordinator")).strip() or "coordinator"
        coord = AgentConfig(
            role_key=coord_role_key,
            role=str(coord_data.get("role", "Koordynator")).strip(),
            assigned_name=coord_name,
            contract_file=coord_data.get("contract_file"),
            default_model=coord_data.get("default_model"),
            slug="coordinator",
        )

        gov_data = ai_dict["governance_qa"]
        gov_name = str(gov_data.get("assigned_name") or gov_data.get("name") or "").strip()
        gov_role_key = str(gov_data.get("role_key", "auditor")).strip() or "auditor"
        gov = AgentConfig(
            role_key=gov_role_key,
            role=str(gov_data.get("role", "Główny Audytor i Jakość")).strip(),
            assigned_name=gov_name,
            contract_file=gov_data.get("contract_file"),
            default_model=gov_data.get("default_model"),
            slug="governance_qa",
        )

        specialists: List[AgentConfig] = []
        specs_raw = ai_dict.get("specialists", [])
        for spec in specs_raw:
            assigned_name = str(spec.get("assigned_name") or spec.get("name") or "").strip()
            role_key = str(spec.get("role_key") or spec.get("slug") or "").strip()
            specialists.append(
                AgentConfig(
                    role_key=role_key,
                    role=str(spec.get("role", "")).strip(),
                    assigned_name=assigned_name,
                    contract_file=spec.get("contract_file"),
                    default_model=spec.get("default_model"),
                    slug=spec.get("slug"),
                )
            )

        dist_raw = ai_dict.get("distillate", {})
        default_thresholds: Dict[str, int] = {
            "code": 650,
            "audit": 650,
            "research": 850,
            "synthesis": 850,
        }
        dist_default = 650
        if isinstance(dist_raw, dict):
            if "default" in dist_raw:
                dist_default = int(dist_raw["default"])
            for k, v in dist_raw.items():
                if str(k).strip().lower() != "default":
                    default_thresholds[str(k).strip().lower()] = int(v)
        distillate_cfg = DistillateConfig(default=dist_default, thresholds=default_thresholds)

        self.ai_team = AITeamConfig(
            coordinator=coord,
            governance_qa=gov,
            specialists=specialists,
            distillate=distillate_cfg,
        )

    def _parse_telemetry(self) -> None:
        raw_tel = self._raw_data.get("telemetry", {})
        if not isinstance(raw_tel, dict):
            raise ConfigValidationError("Sekcja [telemetry] musi być tabelą TOML.")
        raw_budget = raw_tel.get("session_token_budget", 150_000)
        if isinstance(raw_budget, bool) or not isinstance(raw_budget, int) or raw_budget <= 0:
            raise ConfigValidationError(
                f"Pole [telemetry].session_token_budget musi być dodatnią liczbą całkowitą, otrzymano: {raw_budget!r}"
            )
        self.telemetry = TelemetryConfig(session_token_budget=raw_budget)

    def _parse_presentation(self) -> None:
        pres_dict = self._raw_data["presentation"]
        required = ["theme_preset", "font_sans", "font_mono"]
        for field_name in required:
            if field_name not in pres_dict:
                raise ConfigValidationError(f"Sekcja [presentation] nie posiada pola: '{field_name}'")

        preset = str(pres_dict["theme_preset"]).strip()
        preset_light = str(pres_dict.get("theme_preset_light", "dawn")).strip()
        preset_dark = str(pres_dict.get("theme_preset_dark", "tokyonight")).strip()
        mode = str(pres_dict.get("theme_mode", "system")).strip()

        self.presentation = PresentationConfig(
            theme_preset=preset,
            font_sans=str(pres_dict["font_sans"]).strip(),
            font_mono=str(pres_dict["font_mono"]).strip(),
            theme_preset_light=preset_light,
            theme_preset_dark=preset_dark,
            theme_mode=mode,
        )

    def _parse_policies(self) -> None:
        policies_dict = self._raw_data.get("policies", {})
        ht_dict = policies_dict.get("human_thoughts", {})
        self.human_thoughts_policy = HumanThoughtsPolicyConfig(
            protect_unorganized_thoughts=bool(ht_dict.get("protect_unorganized_thoughts", True)),
            forbid_auto_sprint_injection=bool(ht_dict.get("forbid_auto_sprint_injection", True)),
            require_explicit_confirmation=bool(ht_dict.get("require_explicit_confirmation", True)),
            description=str(ht_dict.get("description", "")).strip(),
        )

        ws_dict = policies_dict.get("workshop", {})
        self.workshop_policy = WorkshopPolicyConfig(
            allowed_task_types=list(ws_dict.get("allowed_task_types", ["PR", "OP"])),
            forbidden_task_types=list(ws_dict.get("forbidden_task_types", ["RS"])),
            forbidden_sections=list(ws_dict.get("forbidden_sections", [
                "Pokoje Robocze",
                "Zrzut ze Skrzynki",
                "Filar IV: Badania i Synteza Wiedzy",
            ])),
            description=str(ws_dict.get("description", "")).strip(),
        )

    # ==========================================================================
    # METODY POMOCNICZE (PUBLIC QUERY API)
    # ==========================================================================

    @property
    def instance_name(self) -> str:
        """Zwraca sparametryzowaną nazwę instancji systemu (np. 'LAB')."""
        return self.system.instance_name

    def get_room(self, identifier: str) -> RoomConfig:
        """
        Pobiera konfigurację pokoju po ID (np. 'r01_inbox'), kodzie ('01') lub nazwie ('Skrzynka').
        Rzuca ConfigValidationError, jeśli pokój nie istnieje.
        """
        ident = identifier.strip().lower()
        for room_id, room in self.rooms.items():
            if room_id.lower() == ident:
                return room

        for room in self.rooms.values():
            if room.code.lower() == ident:
                return room

        for room in self.rooms.values():
            if room.name.lower() == ident:
                return room

        raise ConfigValidationError(
            f"Nie znaleziono pokoju o identyfikatorze, kodzie lub nazwie: '{identifier}'."
        )

    def get_room_path(self, room_id: str, absolute: bool = False) -> Path:
        """
        Zwraca ścieżkę do wskazanego pokoju.
        """
        room = self.get_room(room_id)
        rel_path = Path(room.path)
        return (self.vault_root / rel_path) if absolute else rel_path

    def get_room_by_role(self, role: str) -> RoomConfig:
        """
        Zwraca konfigurację pokoju na podstawie stałej roli rdzenia Frameworku PZW.
        """
        target_role = role.strip().lower()
        for room in self.rooms.values():
            if room.role.lower() == target_role:
                return room

        valid_roles = ", ".join(f"'{r.role}'" for r in self.rooms.values())
        raise ConfigValidationError(
            f"Nie znaleziono pokoju o roli rdzenia '{role}'. Dostępne role: {valid_roles}."
        )

    def resolve_external_link(self, rel_path: str, mode: str = "url") -> str:
        """
        Rozwiązuje logiczną ścieżkę zasobu zewnętrznego (np. 'Czasopisma/Gosc_Niedzielny/2026/GN45.pdf')
        do pełnego adresu URL lub ścieżki w lokalnym punkcie montowania (Cold Storage).
        
        Parametr mode:
        - 'url': zwraca odnośnik internetowy (np. Google Drive URL).
        - 'local': zwraca lokalną ścieżkę na dysku (np. punkt montowania dysku w chmurze).
        """
        clean_rel = str(rel_path).strip().lstrip("/")
        if mode == "local" and self.external_storage.local_mount:
            mount = Path(self.external_storage.local_mount).expanduser()
            return str(mount / clean_rel)
        if self.external_storage.root_url:
            base = self.external_storage.root_url.rstrip("/")
            return f"{base}/{clean_rel}"
        return clean_rel

    def get_agent_config(self, role_or_key: str) -> AgentConfig:
        """
        Pobiera konfigurację agenta na podstawie identyfikatora roli (role_key),
        roli systemowej, sluga lub przypisanego imienia (assigned_name).
        """
        query = role_or_key.strip().lower()
        all_agents = [self.ai_team.coordinator, self.ai_team.governance_qa, *self.ai_team.specialists]

        # 1. Dopasowanie po role_key
        for agent in all_agents:
            if agent.role_key and agent.role_key.lower() == query:
                return agent

        # 2. Dopasowanie po slugu
        for agent in all_agents:
            if agent.slug and agent.slug.lower() == query:
                return agent

        # 3. Dopasowanie po roli (role)
        for agent in all_agents:
            if agent.role and agent.role.lower() == query:
                return agent

        # 4. Standardowe aliasy ról (architektoniczne, niezależne od imion)
        role_aliases: Dict[str, str] = {
            "coordinator": "coordinator",
            "koordynator": "coordinator",
            "auditor": "auditor",
            "audytor": "auditor",
            "governance_qa": "auditor",
            "audytor i jakość": "auditor",
            "główny audytor i jakość": "auditor",
            "researcher": "researcher",
            "badacz": "researcher",
            "badacz źródeł": "researcher",
            "engineer": "engineer",
            "inżynier": "engineer",
            "główny inżynier": "engineer",
            "designer": "designer",
            "architekt designu": "designer",
            "design": "designer",
            "knowledge": "knowledge",
            "procesor wiedzy": "knowledge",
            "wiedza": "knowledge",
            "database_code": "database_code",
            "architekt danych": "database_code",
            "bazy danych": "database_code",
            "baza i kod": "database_code",
            "database": "database_code",
        }
        if query in role_aliases:
            target_key = role_aliases[query]
            for agent in all_agents:
                if agent.role_key.lower() == target_key:
                    return agent

        # 5. Dynamiczne dopasowanie po przypisanym imieniu (assigned_name)
        for agent in all_agents:
            if agent.assigned_name and agent.assigned_name.lower() == query:
                return agent

        raise ConfigValidationError(
            f"Nie znaleziono agenta odpowiadającego zapytaniu '{role_or_key}'."
        )

    def get_agent_name(self, role_key: str) -> str:
        """
        Zwraca imię (assigned_name) agenta przypisanego do danego identyfikatora roli (role_key).
        """
        return self.get_agent_config(role_key).assigned_name

    def get_distillate_limit(self, task_type: Optional[str] = None) -> int:
        """Zwraca limit tokenów destylatu SAT dla wskazanego task_type (lub próg domyślny)."""
        dist = self.ai_team.distillate
        if not task_type:
            return dist.default
        key = str(task_type).strip().lower()
        return dist.thresholds.get(key, dist.default)

    def get_agent_contract_file(self, role_key: str, absolute: bool = False) -> Path:
        """
        Zwraca ścieżkę do pliku kontraktu (contract_file) agenta o danym role_key.
        """
        agent = self.get_agent_config(role_key)
        if not agent.contract_file:
            raise ConfigValidationError(
                f"Agent dla roli '{role_key}' nie posiada zdefiniowanej ścieżki 'contract_file'."
            )
        p = Path(agent.contract_file)
        if absolute and not p.is_absolute():
            return self.vault_root / p
        return p

    def get_all_roles(self) -> List[str]:
        """
        Zwraca listę wszystkich kanonicznych identyfikatorów ról systemowych (role_key) Zespołu AI.
        """
        roles: List[str] = [self.ai_team.coordinator.role_key, self.ai_team.governance_qa.role_key]
        for spec in self.ai_team.specialists:
            if spec.role_key:
                roles.append(spec.role_key)
        return roles

    def get_all_role_keys(self) -> List[str]:
        """Alias dla get_all_roles()."""
        return self.get_all_roles()

    def get_management_files(self, absolute: bool = False) -> list[Path]:
        """
        Zwraca listę ścieżek do kanonicznych plików zarządczych (KISS Governance),
        rozwiązanych dynamicznie na podstawie skonfigurowanych ścieżek pokoi.
        W przypadku braku konfiguracji pokoi używa stałej CANONICAL_MANAGEMENT_FILES jako fallbacku.
        """
        files = []
        try:
            # Pokój Warsztat (rola daily-workbench lub kod 02)
            try:
                ws_room = self.get_room_by_role("daily-workbench")
            except ConfigValidationError:
                ws_room = self.get_room("02")
            
            # Preferencja dla aktywnej Agendy Roboczej dnia (format YYYY-MM-DD – Agenda Robocza.md)
            ws_dir = self.vault_root / ws_room.path
            active_agendas = sorted([f for f in ws_dir.glob("*– Agenda Robocza.md") if f.is_file()], key=lambda x: x.name, reverse=True) if ws_dir.is_dir() else []
            if active_agendas:
                ws_p = Path(ws_room.path) / active_agendas[0].name
            else:
                ws_file = ws_room.entry_file or "Dashboard LAB.md"
                ws_p = Path(ws_room.path) / ws_file
            files.append((self.vault_root / ws_p) if absolute else ws_p)

            # Pokój Projekty (rola active-projects lub kod 03)
            try:
                pr_room = self.get_room_by_role("active-projects")
            except ConfigValidationError:
                pr_room = self.get_room("03")
            pr_file = pr_room.entry_file or "_Spis Projektów.md"
            pr_p = Path(pr_room.path) / pr_file
            files.append((self.vault_root / pr_p) if absolute else pr_p)

            # Pokój Operacje (rola lifecycle-routines lub kod 04 - opcjonalny w kapsułach projektowych)
            try:
                op_room = self.get_room_by_role("lifecycle-routines")
                op_file = op_room.entry_file or "_Spis Operacji.md"
                op_p = Path(op_room.path) / op_file
                files.append((self.vault_root / op_p) if absolute else op_p)
            except ConfigValidationError:
                try:
                    op_room = self.get_room("04")
                    op_file = op_room.entry_file or "_Spis Operacji.md"
                    op_p = Path(op_room.path) / op_file
                    files.append((self.vault_root / op_p) if absolute else op_p)
                except ConfigValidationError:
                    pass

            return files
        except Exception:
            return [(self.vault_root / p) if absolute else Path(p) for p in CANONICAL_MANAGEMENT_FILES]
