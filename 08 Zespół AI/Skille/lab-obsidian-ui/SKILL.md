---
name: lab-obsidian-ui
description: "Wewnętrzny system estetyczny i ergonomii wizualnej dla skarbca LAB oraz Obsidiana: tokeny semantyczne w lab.config.toml i lab-design-tokens.css, typografia systemowa Apple, semiotyka barwna 8 Pokoi oraz stonowane palety zgodne z zasadą KISS."
role: designer
source: "LAB internal (wydzielone z Design - Maja/AGENT.md)"
---

# LAB Obsidian UI — Wewnętrzny System Estetyczny Skarbca LAB

## 1. Zakres stosowania
Ten skill definiuje kanon estetyczny i techniczny dla **prac wewnętrznych w skarbcu LAB** (Obsidian, snippety CSS, wewnętrzne raporty i szablony publikacji LAB), gdy projekt lub zadanie nie wskazuje dedykowanego skilla projektowego.

## 2. Rejestr Motywów i Tokeny Semantyczne
1. **Źródło prawdy i kompilacja:** Rejestr motywów oraz tokeny UI są definiowane i aktualizowane w `lab.config.toml` i kompilowane do arkusza `.obsidian/snippets/lab-design-tokens.css`.
2. **Zakaz hardcodowania HEX:** Wewnątrz stylów LAB obowiązuje bezwzględny zakaz wpisywania kolorów i stylów na sztywno (hardcoding wartości HEX w regułach docelowych) — wszystkie barwy przechodzą wyłącznie przez semantyczne tokeny CSS.
3. **Semiotyka Barwna 8 Pokoi:** Zachowanie deterministycznego kodowania barwnego 8 Pokoi skarbca na poziomie tokenów i snippetów Obsidiana.

## 3. Typografia Systemowa Apple
- Wewnątrz interfejsu LAB i Obsidiana stosuje się natywne kroje systemowe Apple: `-apple-system`, `SF Pro` (`SF Pro Display`, `SF Pro Text`) oraz `SF Mono` dla kodu, identyfikatorów i danych tabelarycznych.
- Obowiązuje pełna obsługa polskich znaków diakrytycznych oraz zachowanie zasad mikrotypografii i czytelnej hierarchii nagłówków.

## 4. Estetyka KISS i Dyscyplina Palety
- **Stonowane palety barwne:** Spójna, wyciszona kolorystyka wspierająca długą pracę z tekstem i analizę wiedzy w Obsidianie.
- **Zakaz nadmiaru wizualnego:** Bezwzględny zakaz agresywnych, jaskrawych palet barwnych oraz zbędnego ozdobnictwa łamiącego zasadę KISS.
- **Ergonomia Obsidiana i a11y:** Wszystkie snippety i arkusze `.css` muszą spełniać standard dostępności WCAG 2.2 AA i utrzymywać wysoką czytelność w trybie jasnym i ciemnym.
