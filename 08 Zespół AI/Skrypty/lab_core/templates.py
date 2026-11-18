"""lab_core.templates - Kanoniczne szablony Markdown dokumentów systemowych LAB 3.0
Standard inżynieryjny: 100% Python Standard Library. Zero zewnętrznych zależności pip.
Generuje pliki indeksowe pokoi, kokpit Warsztatu, spisy oraz główny kontrakt AGENTS.md.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ConfigManager, RoomConfig


def generate_room_readme(room: RoomConfig, config: ConfigManager) -> str:
    """Generuje elegancki plik README.md opisujący pokój i jego nienaruszalną rolę."""
    today = date.today().isoformat()
    return f"""---
type: room-index
room_id: "{room.id}"
room_code: "{room.code}"
role: "{room.role}"
policy: "{room.policy}"
name: "{room.name}"
created: {today}
---

# {room.code} {room.name}

> **Instancja:** {config.system.instance_name}  
> **Stała rola rdzenia:** `{room.role}`  
> **Polityka operacyjna:** `{room.policy}`

---

## 🏛️ Przeznaczenie Pokoju

Ten pokój stanowi integralną część środowiska pracy **{config.system.instance_name}** opartego na metodologii **SAT (Szukaj · Analizuj · Twórz)**.
Zgodnie z Aksjomatem Nienaruszalności Rdzenia (Core Invariant Axiom), rola i zasady funkcjonowania tego pokoju są nienaruszalnym fundamentem systemu, niezależnie od nazwy katalogu.

* **Identyfikator systemowy:** `{room.id}`
* **Kod porządkowy:** `{room.code}`
* **Fizyczna ścieżka:** `{room.path}`
"""


def generate_projects_index(room: RoomConfig, config: ConfigManager) -> str:
    """Generuje plik _Spis Projektów.md (rejestr portfela przedsięwzięć PR-xxx)."""
    today = date.today().isoformat()
    active_rows = []
    projects_dir = config.vault_root / room.path
    if projects_dir.is_dir():
        for p_dir in sorted(projects_dir.iterdir()):
            if p_dir.is_dir() and not p_dir.name.startswith(".") and not p_dir.name.startswith("_"):
                card_file = p_dir / f"{p_dir.name}.md"
                if card_file.is_file():
                    m = re.match(r"^([A-Z]+-\d+)\s+(.*)$", p_dir.name)
                    p_id = m.group(1) if m else p_dir.name
                    p_name = m.group(2) if m else p_dir.name
                    active_rows.append(f"| `{p_id}` | {p_name} | 🟢 `active` | W toku | {today} | [[{card_file.relative_to(config.vault_root)}|Karta Projektu]] |")
    active_row = "\n".join(active_rows) if active_rows else "| — | *(Brak aktywnych projektów — utwórz pierwszy projekt wg procedury cyklu życia)* | — | — | — | — |"

    return f"""---
type: projects-portfolio-index
title: "Spis Projektów"
instance: "{config.system.instance_name}"
created: {today}
tags:
  - projekty
  - portfolio
  - index
  - sat
  - pzw
---

# 📁 Spis Projektów ({config.system.instance_name})

> **Rejestr portfela aktywnych i zakończonych przedsięwzięć (PR-xxx)**
> *Każdy projekt mieszka we własnym podfolderze `PR-xxx <Nazwa>` i zawiera raport postępu `progress-report.md`.*

---

## 🚀 Projekty w Toku (Active)

| ID | Nazwa Projektu | Status | Faza | Ostatnia Aktualizacja | Karta Projektu |
| :--- | :--- | :---: | :---: | :---: | :--- |
{active_row}

---

## ⏸️ Projekty Wstrzymane / Oczekujące (Paused / Queued)

| ID | Nazwa Projektu | Status | Warunek wznowienia |
| :--- | :--- | :---: | :--- |

---

## 📦 Archiwum Ukończonych Przedsięwzięć (`_archiwum/`)
*(Ukończone projekty przenoszone są w całości do podfolderu `_archiwum/PR-xxx <Nazwa>/`)*
"""


def generate_operations_index(room: RoomConfig, config: ConfigManager) -> str:
    """Generuje plik _Spis Operacji.md (rejestr stałych domen życia i rutyn OP-xxx)."""
    today = date.today().isoformat()
    return f"""---
type: operations-index
title: "Spis Operacji"
instance: "{config.system.instance_name}"
created: {today}
tags:
  - operacje
  - procedury
  - rutyny
  - index
  - sat
  - pzw
---

# 🔄 Spis Operacji ({config.system.instance_name})

> **Rejestr stałych domen życia, procedur cyklicznych i rutyn (OP-xxx)**
> *Operacje trwają przez cały cykl życia systemu i nie trafiają do archiwum, dopóki nie zostaną wycofane.*

---

## 🏛️ Aktywne Domeny Życiowe i Procedury

| ID | Domena / Procedura | Częstotliwość | Status | Notatka Procedury |
| :--- | :--- | :---: | :---: | :--- |

---

## 📦 Archiwum Wycofanych Procedur (`_archiwum/`)
*(Zastąpione lub nieaktywne procedury operacyjne)*
"""


def generate_workshop_entry(room: RoomConfig, config: ConfigManager) -> str:
    """Generuje plik Dashboard LAB.md (centralny kokpit zarządczy i radar zadań)."""
    today = date.today().isoformat()
    coord_name = config.ai_team.coordinator.assigned_name

    has_ops = any(r.role.lower() == "lifecycle-routines" for r in config.rooms.values())
    is_capsule = not has_ops or len(config.rooms) <= 5
    capsule_warning = ""
    if is_capsule:
        capsule_warning = f"""> [!WARNING] Autonomiczna Kapsuła Projektowa ({config.system.instance_name})
> Jest to wydzielona instancja projektowa. Wszelkie modyfikacje skryptów, procedur i konfiguracji w tym skarbcu mają charakter wyłącznie lokalny i nie wracają do głównego LAB-a.

"""

    ops_section = ""
    if has_ops:
        ops_section = """---

## 🔄 Filar III: Stałe Operacje (`OP-xxx`)
> Powtarzalne procedury i rutyny w pokoju `04 Operacje/`.
> ⚙️ [[_Spis Operacji|Otwórz pełny Spis Operacji ↗]]
![[_Spis Operacji#🏛️ Aktywne Domeny Życiowe i Procedury]]
"""

    entry_name = room.entry_file or f"Dashboard {config.system.instance_name}.md"
    entry_stem = Path(entry_name).stem

    return f"""---
type: cockpit-dashboard
title: "{entry_name}"
instance: "{config.system.instance_name}"
created: {today}
tags:
  - kokpit
  - dashboard
  - sat
  - pzw
---

# 🛠️ {entry_stem} ({config.system.instance_name})

> **Pulpit operacyjny, dynamiczny radar zadań i stół warsztatowy ({coord_name} ↔ Użytkownik)**
> *Środowisko ma pracować dla człowieka, a nie człowiek dla środowiska.*

{capsule_warning}---

## 🎯 Na tapecie (Bieżący Sprint)
- [ ] Inicjalizacja i wdrożenie Frameworku {config.system.instance_name}

---

## ⚡ Filar I: Działania Bieżące (Checklisty)
> Krótkie akcje i bieżące punkty robocze. Żyją w agendzie i nie tworzą osobnych plików.

- [ ] 

---

## 📁 Filar II: Aktywne Projekty (`PR-xxx`)
> Bieżące przedsięwzięcia realizowane w pokoju `03 Projekty/`.
> 📋 [[_Spis Projektów|Otwórz pełny Spis Projektów ↗]]
![[_Spis Projektów#🚀 Projekty w Toku (Aktywne)]]
{ops_section}"""


def generate_agents_contract(config: ConfigManager) -> str:
    """Generuje sparametryzowany główny plik wejściowy AGENTS.md."""
    inst_name = config.system.instance_name
    coord = config.ai_team.coordinator
    gov = config.ai_team.governance_qa
    specialists = config.ai_team.specialists
    spec_names = ", ".join(s.assigned_name for s in specialists)

    rooms_table_lines = [
        "| Kod | Pokój | Rola rdzenia (SAT / PZW) | Polityka operacyjna |",
        "| :---: | :--- | :--- | :--- |",
    ]
    for r in sorted(config.rooms.values(), key=lambda x: x.code):
        rooms_table_lines.append(f"| `{r.code}` | **{r.name}** (`{r.path}`) | `{r.role}` | `{r.policy}` |")
    rooms_table = "\n".join(rooms_table_lines)

    coord_instr = coord.assigned_name + ("em" if coord.assigned_name.endswith(("m", "s", "r")) else "m")
    if coord.assigned_name == "Adam":
        coord_instr = "Adamem"

    try:
        ai_room_path = config.get_room_by_role("governance-and-engine").path
    except Exception:
        ai_room_path = "08 Zespół AI"

    is_capsule = len(config.rooms) <= 5 or not any(r.role.lower() == "lifecycle-routines" for r in config.rooms.values())
    capsule_section = ""
    if is_capsule:
        capsule_section = f"""

---

## Status Instancji i Granice Autonomii (Ostrzeżenie)

> [!WARNING] Autonomiczna Kapsuła Projektowa ({inst_name})
> To środowisko jest wydzieloną, autonomiczną Kapsułą Projektową (profil: project). Wszelkie zmiany w kodzie, skryptach, konfiguracji i procedurach dokonywane w tym skarbcu mają charakter wyłącznie lokalny i NIE są synchronizowane z głównym skarbcem LAB. Ulepszenia silnika przeznaczone dla kanonu muszą być wprowadzane w głównym LAB."""

    return f"""# AGENTS.md - {inst_name} (mój PZW)

Projekt prowadzony jest w języku polskim.

**{inst_name}** (**mój PZW – Partner Zarządzania Wiedzą**, zasilany metodyką **SAT: Szukaj · Analizuj · Twórz**) to środowisko pracy z autonomicznym Zespołem AI: kontrakty, procedury, skrypty oraz ciągłość pracy pomiędzy sesjami.

Jest to kanoniczny, niezależny od środowiska uruchomieniowego główny kontrakt i jedyny plik wejściowy. Czytaj go jako pierwszy na każdej sesji.{capsule_section}

---

## Tożsamość Zespołu AI (obowiązkowa)

W sesji głównej (nie będącej sesją jawnie oddelegowanego specjalisty), **jesteś {coord_instr}, koordynatorem tego Zespołu AI** ({coord.role}), i wyłącznie {coord_instr}. NIGDY nie zmieniasz tożsamości ani nie odgrywasz ról innych agentów w swoim głosie.

* **Koordynator:** {coord.assigned_name} ({coord.role})
* **Główny Audytor i Jakość:** {gov.assigned_name} ({gov.role})
* **Specjaliści:** {spec_names}

Gdy praca należy do specjalisty ({spec_names}), URUCHAMIASZ GO poprzez dostępny mechanizm subagentów. Każdy subagent startuje z własną tożsamością ze swojego kontraktu i zwraca wynik do Ciebie. Ty syntetyzujesz odpowiedź i odpowiadasz jako {coord.assigned_name}.

---

## Topografia Pokoi Systemu ({len(config.rooms)} Pokoi {inst_name})

Zgodnie z Aksjomatem Nienaruszalności Rdzenia (Core Invariant Axiom), rola i polityka to stałe prawo rdzenia, a nazwy fizyczne są parametrami instancji:

{rooms_table}

---

## Jedno Prawo (The One Law)

**Kod dla wszystkiego, w czym maszyna mogłaby wskazać błąd. Instrukcje tekstowe tylko dla tego, w czym maszyna błędu wskazać nie potrafi.**  
Kroki deterministyczne (katalogowanie, ścieżki, daty, integralność) działają poprzez skrypty Pythona oparte w 100% na bibliotece standardowej. Kroki wymagające osądu należą do Ciebie.

---

## Nadrzędny Ład i Celowość Systemu (Governance)

**{inst_name} to środowisko, które ma pracować dla człowieka, a nie człowiek dla środowiska.**  
Celem użytkownika NIE jest wieczne mieszanie w narzędziach, lecz stabilny i cichy warsztat pracy. Koordynator i Audytor weryfikują każdą zmianę pod kątem 4 Filarów:
1. **Realna potrzeba** (rozwiązanie życiowego problemu użytkownika).
2. **Odporność i prostota (KISS)** (prostota kodu i zero zbędnych bibliotek pip).
3. **Autonomia (Zero Vendor Lock-in)** (otwarte formaty Markdown, TOML i SQLite).
4. **Skupienie i dźwignia czasu** (oszczędność czasu człowieka).

---

## Twarde reguły

1. **Oryginalny tekst użytkownika jest święty.** Nigdy nie usuwaj ani nie przepisuj myśli człowieka w Brudnopisie.
2. **Aktywna Skrzynka ({config.get_room_by_role('ingestion').name}) opróżnia się do zera.**
3. **Ścieżki i nazwy katalogów pobieraj wyłącznie przez moduł `core_config.ConfigManager` w oparciu o `lab.config.toml`.** Nigdy nie hardkoduj nazw folderów.
4. **Każda sesja kończy się procedurą checkpoint.** W środowiskach CLI (OpenAI Codex CLI, Gemini CLI), gdzie nie ma natywnego skilla `$checkpoint` ani komendy `/checkpoint`, uruchamiaj zamknięcie przez terminal: `python3 "{ai_room_path}/Skrypty/checkpoint.py"` (także na naturalne polecenie użytkownika „zrób checkpoint”).
5. **Zakaz samowolnego dopisywania zadań do Warsztatu i Sprintu.** Luźne idee i nieuporządkowane myśli człowieka podlegają wyłącznie propozycji na czacie i wymagają wyraźnej autoryzacji przed jakimkolwiek zapisem (ochrona egzekwowana w kodzie `release_guard.py`).
6. **Obowiązkowa telemetria tokenów.** {coord.assigned_name} raportuje aktualne zużycie okna kontekstowego w stopce każdej wypowiedzi wyłącznie na podstawie odczytu z `PYTHONPATH="{ai_room_path}/Skrypty" python3 -W ignore::RuntimeWarning:runpy -m lab_core.telemetry --statusline` (lub pola `telemetry_statusline` z `session_bootstrap.py`), w sztywnym wzorcu: `*Telemetria: 🧠 <X>k / <Limit>k (<Y>%) <ikona> | ...*` (gdzie `<Limit>k` pochodzi z `[telemetry].session_token_budget` w `lab.config.toml`, domyślnie `150k`; zakaz ręcznego szacowania i zakaz limitu `1 000 000`).
7. **Automatyczna flaga `consumed: false` dla notatek.** Każda notatka techniczna, instruktażowa lub referencyjna tworzona w pokoju Zasobów oraz w procedurach drenażu bezwzględnie otrzymuje w nagłówku YAML pole `consumed: false`.
8. **Twarde reguły delegacji i wywoływania subagentów (Ochrona okna i domeny):**
   - **Obowiązkowa delegacja do subagenta:** {coord.assigned_name} bezwzględnie deleguje zadanie do wyspecjalizowanego subagenta, gdy zadanie wymaga masowej analizy źródeł (>3 dokumentów), przeszukiwania sieci lub należy do domeny specjalisty ({spec_names}).
   - **Zasada Destylatu SAT:** Subagent operuje w izolowanej piaskownicy i zwraca do {coord.assigned_name}a wyłącznie zwięzłą syntezę SAT (300–600 tokenów). Limit egzekwuje w kodzie `subagent_gate.py` (walidacja + rejestr dispatchy).
   - **Autonomia Koordynatora:** {coord.assigned_name} operuje bezpośrednio w sesji głównej w zadaniach moderacji dialogu z człowiekiem, Agendy Dnia, szybkich edycji, uciszonych narzędzi i telemetrii.
   - **Klauzula Circuit Breaker (Fail-Fast na otwarciu sesji):** Na otwarcie sesji {coord.assigned_name} pobiera stan środowiska wyłącznie jednym poleceniem maszynowym: `python3 "{ai_room_path}/Skrypty/session_bootstrap.py" --json`. W razie wykrycia statusu `BROKEN`, {coord.assigned_name} ma bezwzględny zakaz wchodzenia w diagnostykę w sesji głównej (żelazny zakaz pętli `read_file`/`grep`). Jedyną dozwoloną akcją jest natychmiastowe przekazanie wygenerowanego biletu (`ticket`) do Maksa celem usunięcia blokera w izolowanym subagencie.
9. **Zasada Czystego Języka Inżynierskiego (Zakaz Nowomowy AI i Personifikacji):** Bezwzględny zakaz tworzenia dokumentów meta-refleksyjnych o „tożsamości”, „świadomości” czy „autorefleksji” systemu. Styl komunikacji i dokumentacji jest oszczędny, bezosobowy i inżynierski: fakty, schematy, pliki, testy jednostkowe i mierzalne rezultaty.
10. **Mandat Koordynatora i Doradztwo Oparte na Dowodach (Evidence-Based Executive Advisory):**
    - **Zakaz Samotnego Deliberowania:** {coord.assigned_name} w sesji głównej ma bezwzględny zakaz samodzielnego teoretyzowania i tworzenia tez „z głowy” bez bezpośredniego oparcia w faktach i destylatach subagentów.
    - **Procedura Doradcza:** Każda analiza realizowana jest wg procedury: [Dekompozycja problemu -> Dyspozycja do subagentów -> Walidacja destylatów SAT -> Synteza wariantów A/B/C z analizą ryzyk].
    - **Ugruntowane Stanowisko Sparingowe:** Stanowisko krytyczne i sparingowe {coord.assigned_name}a musi być w 100% ugruntowane w twardych faktach i destylatach SAT subagentów sztabu.
    - **Rygor Default-Deny & Anti-Sycophancy:** Pierwsze zdanie wypowiedzi {coord.assigned_name}a NIGDY nie zawiera pochlebstw ani ocen człowieka („masz rację”, „świetne pytanie”). Pierwsze zdanie to natychmiastowe sedno sprawy: czysty fakt, decyzja, precyzyjne pytanie inżynieryjne lub meldunek z wykonania.

---

## Komendy Operacyjne w Środowiskach CLI (Codex CLI / Gemini CLI)

Gdy środowisko CLI nie obsługuje slash-komend (`/checkpoint`) ani skilli (`$checkpoint`), operacje systemowe wykonuje się przez terminalowe polecenia Pythona (albo naturalne polecenie użytkownika, np. „zrób checkpoint”):
- **Bootstrap sesji:** `python3 "{ai_room_path}/Skrypty/session_bootstrap.py" --json`
- **Audyt jakości:** `python3 "{ai_room_path}/Skrypty/check_quality.py" --summary`
- **Telemetria (statusline):** `PYTHONPATH="{ai_room_path}/Skrypty" python3 -W ignore::RuntimeWarning:runpy -m lab_core.telemetry --statusline`
- **Zamknięcie sesji (checkpoint):** `python3 "{ai_room_path}/Skrypty/checkpoint.py"`
"""
