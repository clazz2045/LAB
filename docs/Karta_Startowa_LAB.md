---
type: installation-guide
title: "Karta Startowa LAB – Przewodnik Instalacji ze Wsparciem AI"
instance: "LAB"
date: 2026-11-16
repo: "https://github.com/clazz2045/LAB"
tags:
  - przewodnik
  - instalacja
  - starter-kit
  - karta-startowa
  - ai-onboarding
---

# 🏛️ Karta Startowa LAB
## Przewodnik Błyskawicznej Instalacji ze Wsparciem AI (Showcase / As-Is)

> *„Środowisko ma pracować dla człowieka, a nie człowiek dla środowiska.”*  
> Czas wdrożenia: **poniżej 5 minut** • Wymagana wiedza techniczna: **brak** • Koszt narzędzi: **0 zł** • Repozytorium: `https://github.com/clazz2045/LAB`

---

### 1. Czym jest LAB (Twój PZW)?

**LAB** (**Twój Partner Zarządzania Wiedzą**) to prywatne, ciche i zorganizowane środowisko pracy na Twoim własnym komputerze:
1. **Prywatność i własność:** Twoje notatki, pliki i zadania leżą w 100% na Twoim dysku w otwartych formatach (Markdown, TOML, SQLite). Zero uzależnienia od abonamentów chmurowych.
2. **Topografia 8 Pokoi:** Każda myśl, dokument, projekt i baza danych ma swoje nienaruszalne, logiczne miejsce (od `01 Skrzynka/` po `08 Zespół AI/`).
3. **Zespół AI i Zero-Pip:** Gotowa architektura ról (Adam, Ada, Maks, Kinga, Maja, Klara, Edyta) oraz silnik w **100% Python stdlib** (bez zewnętrznych zależności `pip`).

---

### 2. Wymagania Wstępne (Tylko 2 darmowe elementy)

Przed uruchomieniem instalatora upewnij się, że masz na komputerze:
1. **Obsidian** (darmowy edytor notatek) – pobierz i zainstaluj ze strony: [obsidian.md](https://obsidian.md).
2. **Python (3.11 lub nowszy):**
   * **macOS:** Zainstaluj z [python.org](https://www.python.org/downloads/) lub przez Homebrew (`brew install python@3.11`).
   * **Windows:** Pobierz z [python.org](https://www.python.org/downloads/) – **BARDZO WAŻNE:** podczas instalacji zaznacz pole wyboru: ☑️ *„Add python.exe to PATH”*.
   * **iCloud Drive (macOS):** Jeśli umieszczasz skarbiec w iCloud Drive, wyłącz opcję **Optymalizuj dysk Maca (`Optimize Mac Storage = OFF`)** w ustawieniach iCloud Drive, aby pliki nie znikały z dysku lokalnego.

*(To wszystko! LAB nie wymaga instalowania żadnych pakietów `pip` ani zewnętrznych serwerów).*

---

### 3. Instalacja Jednym Dwuklikiem Myszy (Kreator WWW)

1. **Pobierz i rozpakuj** paczkę `LAB-Installer.zip` z GitHub Releases (`clazz2045/LAB`) lub sklonuj repozytorium (`git clone https://github.com/clazz2045/LAB.git`).
   * W rozpakowanym archiwum `LAB-Installer.zip` na wierzchu zobaczysz wyłącznie dwa launchery (`LAB-Installer.command` oraz `LAB-Installer.bat`) – cały wsad instalacyjny jest bezpiecznie schowany w ukrytym podkatalogu `.lab-payload/`.
2. **Uruchom instalator:**
   * 🍏 **Na komputerze Mac:** Kliknij dwukrotnie plik:  
     👉 `LAB-Installer.command`
   * 🪟 **Na komputerze Windows:** Kliknij dwukrotnie plik:  
     👉 `LAB-Installer.bat`
   * **Uwaga – Gatekeeper macOS:** Jeśli system zablokuje plik `LAB-Installer.command`, kliknij go **Control+klik** (prawy przycisk myszy) → **Otwórz** → potwierdź **Otwórz** (albo przejdź do *Ustawienia systemowe → Prywatność i ochrona → Otwórz mimo to*). Możesz też użyć Terminala bez atrybutu `com.apple.quarantine`:
     ```bash
     xattr -dr com.apple.quarantine . && ./LAB-Installer.command
     ```
3. **Skonfiguruj instancję w kreatorze krok-po-kroku** (jedno okienko = jedna decyzja, lub przez CLI `--non-interactive`):
   * Imię / zwrot do użytkownika oraz nazwa instancji (domyślnie: *LAB*),
   * Motyw kolorystyczny (*tokyonight*, *nord*, *nordic-snow*, *green*),
   * Integracja z GitHubem (`[vcs]`), iCloud oraz wybrany model LLM (`[ai_team.llm]`, np. Antigravity / Claude).
   * **Uwaga – iCloud Drive na macOS:** Jeśli trzymasz skarbiec w iCloud Drive, upewnij się w *Ustawieniach systemowych → Apple ID → iCloud → iCloud Drive*, że opcja **Optymalizuj pamięć Mac (`Optimize Mac Storage`) = OFF** (wyłączona) – zapobiega to znikaniu plików lokalnych z dysku.
4. **Otwórz skarbiec w Obsidianie:**
   * Uruchom **Obsidian** &rarr; **Open folder as vault** (*Otwórz folder jako skarbiec*) &rarr; wskaż katalog nowo utworzonego skarbca.
   * **Uwaga – Widok GitHub ↔ Obsidian:** Obsidian automatycznie ukrywa pliki i katalogi systemowe w katalogu głównym (np. `.obsidian/`, `.gitignore`, `.lab-payload/`, skrypty `.py` i launchery `LAB-Installer.*`), pokazując poza 8 Pokojami (`01–08`) przede wszystkim plik `AGENTS.md`. To zamierzone zachowanie – pełne drzewo plików zobaczysz w Finderze/Eksploratorze lub na GitHubie.
   * **Gotowe!** Instalator automatycznie wykonał *Post-Install Smoke Check* (walidacja `lab.config.toml`, `.obsidian/*.json`, SQLite WAL oraz `session_bootstrap.py`).

---

### 4. 🤖 Gotowy Prompt dla Twojego Asystenta AI (Antigravity / Claude / ChatGPT / Gemini)

Jeśli podczas pierwszych kroków wolisz mieć przy sobie przewodnika, **skopiuj poniższą ramkę w całości i wklej ją do swojego czatu AI**:

```text
Cześć! Właśnie pobrałem paczkę startową LAB (mój Partner Zarządzania Wiedzą – PZW) i chcę postawić swoje prywatne środowisko pracy w Obsidianie.

Jesteś moim cierpliwym, przyjaznym Wirtualnym Asystentem Wdrożenia LAB.

Twoje zasady:
1. Prowadź mnie za rękę krok po kroku. Zapytaj mnie najpierw, na jakim systemie pracuję (macOS czy Windows) i czy mam już zainstalowanego Obsidiana oraz Pythona 3.11+.
2. Gdy potwierdzę uruchomienie instalatora dwuklikiem myszy (LAB-Installer.command / LAB-Installer.bat), wyjaśnij mi prosto, jak otworzyć nowo utworzony folder w programie Obsidian (oraz uspokój mnie, że Obsidian celowo ukrywa pliki techniczne poza AGENTS.md).
3. Wytłumacz mi w 3 przejrzystych punktach, jak korzystać z 8 Pokoi LAB (przede wszystkim: czym różni się '01 Skrzynka' od '02 Warsztat' i '03 Projekty').
4. Używaj prostego, naturalnego języka. Unikaj żargonu programistycznego. Po każdym kroku zadawaj mi jedno pytanie sprawdzające, czy wszystko poszło zgodnie z planem, zanim przejdziesz dalej.

Zacznij od powitania i pierwszego pytania!
```

---

### 5. Pierwsze 3 Minuty w Nowym Środowisku

1. **Otwórz Warsztat (`02 Warsztat/`):**
   * Otwórz `Dashboard LAB.md` – zobaczysz czysty kokpit operacyjny i tablicę zadań.
2. **Wrzuć pierwszą myśl do Skrzynki (`01 Skrzynka/`):**
   * Utwórz notatkę lub wrzuć plik PDF. Skrzynka opróżnia się do zera automatycznie podczas pracy z asystentem AI (drenaż Skrzynki).
3. **Zaplanuj pierwszy cel (`03 Projekty/`):**
   * Utwórz folder `PR-001 Mój Pierwszy Projekt` i rozwijaj inicjatywę bez chaosu.

---

### 6. Higiena Sesji i Telemetria Tokenów

* **Dlaczego widzisz pasek `🧠 Xk / 250k`?** Pokazuje on na bieżąco, ile „pamięci roboczej” rozmowy już zużyłeś — im dłuższa rozmowa, tym łatwiej model AI przeoczy starsze ustalenia.
* **Próg higieniczny (200–250k) i ostrzeżenia:** Przy ~75–85% limitu Koordynator ostrzeże Cię, że czas zamknąć bieżący wątek.
* **Jak bezpiecznie zacząć nową sesję:** Wpisz **`/checkpoint`** (zapisze postęp na dysku), a potem otwórz świeżą rozmowę:
  * **Antigravity:** kliknij **New Chat (`+`)**.
  * **Claude Code:** wpisz **`/clear`** lub uruchom nową sesję w folderze skarbca.

*Witaj w cichym, uporządkowanym warsztacie pracy! • LAB (MIT, Showcase / As-Is)*
