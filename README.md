# LAB – Silnik Rdzenia i Governance Zespołu AI dla środowiska Obsidian

**Deterministyczny silnik, architektura agentowa i korpus ładu działający w środowisku Obsidian (Partner Zarządzania Wiedzą).**

Projekt rozwijany w standardzie **SAT (Szukaj · Analizuj · Twórz)** oraz **100% Python stdlib (Zero-Pip)**.

> **Licencja MIT · Klauzula Showcase / As-Is (No-SLA):** Repozytorium udostępniane jest na licencji **MIT** (zobacz `LICENSE`) w trybie *Showcase / As-Is*, bez gwarancji wsparcia technicznego, utrzymania ani SLA.

---

## Szybki start (macOS / Windows)

**Wariant 1 – Paczka instalacyjna (`LAB-Installer.zip` z GitHub Releases):**
Rozpakuj archiwum. Na wierzchu zobaczysz launchery: `LAB-Installer.command` / `LAB-Update.command` (macOS) oraz `LAB-Installer.bat` / `LAB-Update.bat` (Windows) – cały wsad instalacyjny jest bezpiecznie ukryty w `.lab-payload/`.

**Wariant 2 – Klonowanie repozytorium:**
```bash
git clone https://github.com/clazz2045/LAB.git
cd LAB
```

- **macOS:** uruchom `LAB-Installer.command` (lub `python3 lab-init.py`)
- **Windows:** uruchom `LAB-Installer.bat` (lub `py -3 lab-init.py`)
- **Tryb bezinteraktywny (CI):** `python3 lab-init.py --non-interactive --target-dir ~/Documents/LAB`

### Aktualizacja istniejącego środowiska LAB (1 kliknięcie)

Masz już własny skarbiec LAB i chcesz wgrać najnowszą wersję silnika? Zrobisz to w 3 prostych krokach:

1. Pobierz plik `LAB-Update.command` (macOS) lub `LAB-Update.bat` (Windows) z repozytorium lub z paczki Release (`LAB-Installer.zip`).
2. Przenieś pobrany plik do głównego katalogu swojego skarbca LAB (obok pliku `lab.config.toml`).
3. Kliknij dwukrotnie w plik. Skrypt automatycznie i bezpiecznie pobierze najnowszy silnik do `08 Zespół AI/Skrypty/` oraz zaktualizuje konfigurację agentów, gwarantując 100% nienaruszalności Twoich prywatnych notatek, projektów i baz danych (pokoje `01–07`).

### Uwaga: Gatekeeper macOS (pierwsze uruchomienie `.command`)
Jeśli macOS zablokuje plik `LAB-Installer.command` komunikatem o nieznanym deweloperze:
- **Przez Finder (GUI):** kliknij plik **Control+klik** (prawy przycisk myszy) → **Otwórz** → potwierdź **Otwórz** (lub przejdź do *Ustawienia systemowe → Prywatność i ochrona → Otwórz mimo to*).
- **Przez Terminal (bez atrybutu `com.apple.quarantine`):**
  ```bash
  xattr -dr com.apple.quarantine . && ./LAB-Installer.command
  ```

### Uwaga: Widok GitHub ↔ Obsidian
Obsidian w nawigatorze plików ukrywa pliki i katalogi systemowe w katalogu głównym (m.in. `.obsidian/`, `.gitignore`, `.lab-payload/`, skrypty `.py` oraz launchery `LAB-Installer.*`), pokazując poza 8 Pokojami (`01–08`) przede wszystkim plik `AGENTS.md`. Pełną strukturę techniczną zobaczysz w Finderze, Eksploratorze plików lub bezpośrednio na GitHubie.

Konfiguracja wzorcowa znajduje się w `lab.config.example.toml` (sekcje `[vcs]` oraz `[ai_team.llm]` dla **Antigravity**, **Claude** i **OpenAI Codex CLI**). W trybie **OpenAI Codex CLI** instalator wymaga katalogu lokalnego (poza iCloud Drive).

---

## Struktura Repozytorium

```text
LAB/
├── core_config.py           # Silnik parametryzacji, walidacji TOML i stałych ról pokoi
├── lab-init.py              # Zunifikowany instalator i kreator WWW (http.server)
├── LAB-Installer.command    # Launcher instalacji macOS (sprawdza Python >= 3.11)
├── LAB-Installer.bat        # Launcher instalacji Windows (py -3)
├── LAB-Update.command       # Launcher aktualizacji macOS (1 kliknięcie)
├── LAB-Update.bat           # Launcher aktualizacji Windows (1 kliknięcie)
├── lab.config.example.toml  # Wzorzec konfiguracji ([vcs], [ai_team.llm] Antigravity, Claude, OpenAI Codex CLI)
├── pytest.py                # 100% stdlib runner testów (Zero-Pip)
├── AGENTS.md                # Główny kontrakt wejściowy Zespołu AI
├── LICENSE                  # Licencja MIT
├── README.md                # Dokumentacja startowa repozytorium
├── CONTRIBUTING.md          # Zasady współpracy i tryb Showcase / As-Is
├── docs/                    # Podręcznik AI, Use Cases (PDF) oraz Karta Startowa LAB
├── .obsidian/               # Lekka konfiguracja Obsidiana (app.json, appearance.json, hotkeys.json, snippets/)
├── 01 Skrzynka/             # Bufor wejściowy (drain-to-zero, .gitkeep)
├── 02 Warsztat/             # Stół roboczy dnia, Agenda Robocza i Dashboard LAB (.gitkeep)
├── 03 Projekty/             # Aktywne projekty o zamkniętym horyzoncie (.gitkeep)
├── 04 Operacje/             # Ciągłe operacje, rutyny i utrzymanie (.gitkeep)
├── 05 Baza Wiedzy/          # Synteza wiedzy (sieć połączonych notatek, mapy tematów, .gitkeep)
├── 06 Zasoby/               # Archiwum referencyjne i zasoby multimedialne (.gitkeep)
├── 07 Bazy Danych/          # Warstwa relacyjna SQLite WAL (inicjalizowana deterministycznie, .gitkeep)
└── 08 Zespół AI/            # Silnik Zespołu AI i Governance
    ├── Dzienniki Sesji/     # Puste logi sesji (.gitkeep)
    ├── Governance/          # Jednolity dokument ustrojowy (_Księga Governance LAB.md)
    ├── Role/                # Kontrakty agentów (Adam, Ada, Maks, Kinga, Maja, Klara, Edyta)
    ├── Skrypty/             # Narzędzia CLI, lab_core/, tests/
    │   └── integrations/raycast/  # Opcjonalne skrypty Raycast dla macOS
    └── Szablony/            # Szablony dokumentów i agendy roboczej
```

---

## Testy i Kontrola Jakości

```bash
python3 -m unittest discover -s "08 Zespół AI/Skrypty/tests" -p "test_*.py"
python3 "08 Zespół AI/Skrypty/check_quality.py" --vault-root .
```

---

## Higiena sesji i telemetria tokenów

1. **Pasek telemetrii pod każdą odpowiedzią:** Koordynator raportuje aktualne zużycie okna kontekstowego (`🧠 Xk / <Limit>k (Y%)`), abyś widział, ile pamięci roboczej pozostało w bieżącej rozmowie.
2. **Budżet higieniczny sesji (`[telemetry].session_token_budget`):** Uniwersalny próg domyślny to **150k tokenów** dla **Claude Code** i **OpenAI Codex CLI** (ostrzeżenie od **80%**, czyli 120k). Przy pracy w **Antigravity** z modelami wielko-oknowymi możesz ustawić w `lab.config.toml` wartość **250_000** (`250k`, ostrzeżenie od 200k), aby chronić jakość analiz w długich wątkach (*Lost in the Middle*).
3. **Bezpieczne zamknięcie i nowa sesja:** Uruchom **`/checkpoint`** (zapisuje stan Agendy i Dziennik Sesji), a następnie otwórz nową, świeżą rozmowę:
   - **Antigravity:** kliknij *New Chat* (`+`) w panelu konwersacji.
   - **Claude Code:** wpisz `/clear` (lub uruchom nową sesję `claude` w katalogu skarbca).
   - **OpenAI Codex CLI:** uruchom nową sesję `codex` w katalogu skarbca (lub użyj `/new`).

---

## Licencja i Zakres Odpowiedzialności (`QG-3`)

1. **Licencja MIT:** Całość kodu i szablonów publikowana jest na licencji MIT.
2. **Tryb Showcase / As-Is (No-SLA):** Projekt pełni rolę wzorca referencyjnego. Autor nie świadczy indywidualnego wsparcia technicznego, konsultacji wdrożeniowych ani nie gwarantuje terminów reakcji na zgłoszenia.
3. **Wkład zewnętrzny:** Szczegóły i ograniczenia zakresu opisuje plik `CONTRIBUTING.md`.
