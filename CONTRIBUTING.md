# Zasady Współpracy – LAB (`CONTRIBUTING`)

> **Klauzula Showcase / As-Is (No-SLA · Bramka QG-3):** Repozytorium `clazz2045/LAB` jest publikowane na licencji **MIT** jako projekt pokazowy i wzorzec referencyjny (*As-Is*). Autor nie gwarantuje obsługi zgłoszeń (*Issues*), przeglądów *Pull Requestów*, poprawek na żądanie ani wsparcia technicznego (*No-SLA*).

---

## 1. Ścisły Zakres (*Strict Scope*)

- **Język projektu:** Dokumentacja, komunikaty i komentarze są prowadzone w **100% w języku polskim**.
- **Standard technologiczny (Zero-Pip):** Silnik `08 Zespół AI/Skrypty/` oraz instalator `lab-init.py` muszą działać wyłącznie w oparciu o **bibliotekę standardową Pythona (`stdlib`)**. Zmiany wprowadzające zewnętrzne zależności (`pip`) będą odrzucane.
- **Architektura 8 Pokoi Rdzenia:** Struktura katalogów `01–08` oraz zasady z `AGENTS.md` i `_Księgi Governance LAB.md` są niezmienne dla rdzenia systemu.
- **Brak prywatnych danych:** Zgłoszenia i propozycje zmian nie mogą zawierać danych osobowych (PII), kluczy API, prywatnych adresów URL ani twardych ścieżek lokalnych (bramka `QG-2`).

---

## 2. Jak Zgłaszać Błędy i Propozycje Zmian

Ze względu na tryb *Showcase / As-Is* przed otwarciem zgłoszenia lub *Pull Requesta*:
1. **Sprawdź środowisko lokalne:** Upewnij się, że korzystasz z Pythona `>= 3.11` i aktualnej wersji repozytorium `LAB`.
2. **Uruchom deterministyczne bramki jakości:**
   ```bash
   python3 -m unittest discover -s "08 Zespół AI/Skrypty/tests" -p "test_*.py"
   python3 "08 Zespół AI/Skrypty/check_quality.py" --vault-root .
   ```
3. **Opisz zmianę zwięźle i technicznie (po polsku):** Podaj dokładny opis problemu, kroki reprodukcji oraz wynik powyższych testów.

---

## 3. Licencja

Wysyłając wkład do repozytorium `clazz2045/LAB`, akceptujesz, że Twoje zmiany zostaną udostępnione na warunkach licencji **MIT** (zobacz `LICENSE`), bez jakichkolwiek zobowiązań gwarancyjnych (*As-Is*).
