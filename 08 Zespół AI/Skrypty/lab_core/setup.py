#!/usr/bin/env python3
"""setup.py - Uniwersalny Kreator WWW i CLI Instancji LAB 3.0 (Framework PZW)
Standard inżynieryjny: 100% Python Standard Library (http.server, urllib.parse, webbrowser, argparse, pathlib).
Zero zewnętrznych zależności pip.

Obsługuje:
1. Twardą weryfikację wersji Python >= 3.11 (wymagane przez tomllib).
2. Pre-flight System Diagnostics (Obsidian, Git, Python 3.11+).
3. Domyślny Lokalny Kreator WWW (http.server na 127.0.0.1, losowy port).
4. Tryb --non-interactive (CLI dla CI/testów) oraz --cli (wywiad terminalowy).
5. Deterministyczne powołanie czystego skarbca przez lab_core.installer (z Post-Install Smoke Check).
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import html
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any, Dict, List, Optional
import urllib.parse
import webbrowser

sys.dont_write_bytecode = True

_file_dir = Path(__file__).resolve().parent
if _file_dir.name == "lab_core":
    _scripts_dir = _file_dir.parent
    _root_dir = _scripts_dir.parent.parent
elif _file_dir.name == "Skrypty":
    _scripts_dir = _file_dir
    _root_dir = _scripts_dir.parent.parent
else:
    _root_dir = _file_dir
    _scripts_dir = _root_dir / "08 Zespół AI" / "Skrypty"

for _candidate in (_root_dir, _scripts_dir, _file_dir):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from lab_core.diagnostics import check_icloud_storage, run_preflight_diagnostics
from lab_core.installer import (
    DEFAULT_AGENT_NAMES,
    LAB_VERSION,
    InstallOptions,
    InstallResult,
    LabEngineInstaller,
)
from lab_core.themes import THEME_PRESETS, get_theme_preset, list_theme_presets


def check_python_version() -> None:
    """Wymusza Python >= 3.11 (wymagane przez wbudowany moduł tomllib)."""
    if sys.version_info < (3, 11):
        ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        raise RuntimeError(
            f"Wymagany Python >= 3.11 (wykryto {ver}). Zainstaluj nowszą wersję Pythona."
        )


def detect_default_locations() -> List[Dict[str, str]]:
    """Zwraca listę wykrytych lokalizacji docelowych dla sejfu LAB."""
    locations: List[Dict[str, str]] = []
    home = Path.home()
    icloud_obsidian = home / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents"
    icloud_drive = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    docs_dir = home / "Documents"

    if icloud_obsidian.is_dir():
        locations.append({"id": "icloud_obsidian", "label": "iCloud Drive (Obsidian)", "path": str(icloud_obsidian)})
    elif icloud_drive.is_dir():
        locations.append({"id": "icloud_drive", "label": "iCloud Drive", "path": str(icloud_drive)})
    locations.append({"id": "documents", "label": "Katalog lokalny (~/Documents)", "path": str(docs_dir)})
    return locations


def _render_wizard_html() -> str:
    """Generuje HTML dla sekwencyjnego kreatora WWW LAB 3.0 (1 okienko = 1 decyzja)."""
    locations = detect_default_locations()
    default_loc = locations[0]["path"] if locations else str(Path.home() / "Documents")
    default_user = os.environ.get("USER", "Owner").capitalize()
    presets = [(key, get_theme_preset(key)) for key in list_theme_presets()]
    icloud_diag = check_icloud_storage()
    icloud_diag_status = "OK" if icloud_diag.passed else "UWAGA"
    icloud_diag_msg = html.escape(f"[{icloud_diag_status}] {icloud_diag.details}")

    loc_options = "\n".join(
        f'<option value="{html.escape(loc["path"])}">{html.escape(loc["label"])} ({html.escape(loc["path"])})</option>'
        for loc in locations
    )
    loc_options += '\n<option value="__custom__">Ścieżka niestandardowa...</option>'

    theme_swatches = "\n".join(
        f'<label class="swatch">'
        f'<input type="radio" name="theme_preset_dark" value="{html.escape(key)}" {"checked" if key == "tokyonight" else ""}>'
        f'<span class="dot" style="background:{html.escape(p.get("accent", "#7aa2f7"))}"></span>'
        f'<span>{html.escape(p.get("name", key))}</span></label>'
        for key, p in presets
    )
    light_options = "\n".join(
        f'<option value="{html.escape(key)}" {"selected" if key == "dawn" else ""}>{html.escape(p.get("name", key))} ({html.escape(p.get("accent", "#286983"))})</option>'
        for key, p in presets
    )

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>LAB – Kreator Instancji</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }}
  .card {{ max-width: 680px; margin: 0 auto; background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 28px; }}
  h1 {{ margin-top: 0; font-size: 22px; }}
  h2 {{ font-size: 17px; margin-top: 6px; color: #93c5fd; }}
  .progress {{ font-size: 12px; color: #94a3b8; margin-bottom: 14px; letter-spacing: 0.04em; text-transform: uppercase; }}
  .step {{ display: none; }}
  .step.active {{ display: block; }}
  label {{ display: block; margin-top: 12px; font-size: 14px; color: #cbd5e1; }}
  input[type=text], select {{ width: 100%; box-sizing: border-box; padding: 10px 12px; margin-top: 6px; border-radius: 8px; border: 1px solid #475569; background: #0f172a; color: #f8fafc; font-size: 14px; }}
  .comment {{ font-size: 13px; color: #94a3b8; margin-top: 10px; line-height: 1.45; }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
  .swatches {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 8px; }}
  .swatch {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border: 1px solid #475569; border-radius: 8px; cursor: pointer; background: #0f172a; margin-top: 0; }}
  .dot {{ width: 12px; height: 12px; border-radius: 50%; display: inline-block; }}
  .warn {{ background: #451a03; border: 1px solid #d97706; color: #fde68a; padding: 10px 12px; border-radius: 8px; font-size: 13px; margin-top: 12px; }}
  .box {{ background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 14px; margin-top: 12px; font-size: 13px; }}
  .nav {{ display: flex; justify-content: space-between; gap: 12px; margin-top: 24px; }}
  .btn {{ flex: 1; padding: 12px; border: 0; border-radius: 8px; background: #0284c7; color: #fff; font-size: 15px; font-weight: 600; cursor: pointer; }}
  .btn:hover {{ background: #0369a1; }}
  .btn-sec {{ background: #334155; }}
  .btn-sec:hover {{ background: #475569; }}
  .chk {{ display: flex; align-items: flex-start; gap: 8px; margin-top: 12px; font-size: 13px; cursor: pointer; }}
  .chk input {{ margin-top: 3px; }}
</style>
</head>
<body>
<div class="card">
  <h1>LAB – Konfiguracja własna</h1>
  <div class="progress" id="progressLabel">Krok 1 z 12</div>
  <form method="POST" action="/install" id="wizardForm" autocomplete="off">

    <div class="step active" data-step="1">
      <h2>1. Imię właściciela systemu</h2>
      <label>Imię / zwrot
        <input type="text" name="user_name" id="f_user_name" value="{html.escape(default_user)}" required>
      </label>
      <p class="comment">Komentarz: Wpisywane do <code>lab.config.toml</code> oraz kontraktu <code>AGENTS.md</code> jako identyfikator właściciela sejfu.</p>
    </div>

    <div class="step" data-step="2">
      <h2>2. Nazwa sejfu</h2>
      <label>Nazwa katalogu głównego
        <input type="text" name="instance_name" id="f_instance_name" value="LAB" required>
      </label>
      <p class="comment">Komentarz: Określa nazwę instancji oraz nazwę głównego folderu Obsidiana (domyślnie <code>LAB</code>).</p>
    </div>

    <div class="step" data-step="3">
      <h2>3. Lokalizacja sejfu</h2>
      <label>Katalog nadrzędny
        <select name="location_preset" id="locSelect" onchange="toggleCustomLoc(this.value)">
          {loc_options}
        </select>
      </label>
      <label id="customLocWrap" style="display:none;">Własna ścieżka nadrzędna
        <input type="text" name="custom_location" id="f_custom_location" value="{html.escape(default_loc)}" oninput="toggleIcloudWarn()">
      </label>
      <p class="comment">Komentarz: Wybierz folder, w którym powstanie sejf. Wykryte lokalizacje iCloud i <code>~/Documents</code> są na górze listy.</p>
    </div>

    <div class="step" data-step="4">
      <h2>4. Przechowywanie w iCloud Drive</h2>
      <label>Czy sejf będzie w iCloud Drive?
        <select name="icloud_enabled" id="icloudSelect" onchange="toggleIcloudWarn()">
          <option value="auto" selected>Autodetekcja ze ścieżki</option>
          <option value="yes">Tak (chmura Apple)</option>
          <option value="no">Nie (katalog lokalny)</option>
        </select>
      </label>
      <p class="comment">Komentarz: Włączenie iCloud automatycznie zabezpieczy sejf przed konfliktami synchronizacji (blokada <code>.git</code>, <code>.venv/</code> i <code>node_modules/</code> w chmurze).</p>
    </div>

    <div class="step" data-step="5">
      <h2>5. Bramka iCloud: Optimize Mac Storage = OFF</h2>
      <div class="box">{icloud_diag_msg}</div>
      <label class="chk">
        <input type="checkbox" name="confirm_icloud_optimize_off" id="f_confirm_icloud" value="yes">
        <span>Potwierdzam, że opcja <strong>Optymalizuj pamięć Maca (Optimize Mac Storage)</strong> w ustawieniach iCloud jest <strong>WYŁĄCZONA (OFF)</strong> (lub sejf jest lokalny).</span>
      </label>
      <p class="comment">Komentarz: Przy włączonej optymalizacji macOS usuwa pliki lokalnie i zostawia placeholdery <code>.icloud</code>, co uszkadza odczyt SQLite i skryptów Pythona.</p>
    </div>

    <div class="step" data-step="6">
      <h2>6. GitHub i kontrola wersji</h2>
      <label>Czy używasz GitHuba do wersjonowania sejfu?
        <select name="github_enabled" id="githubSelect" onchange="toggleIcloudWarn()">
          <option value="no" selected>Nie</option>
          <option value="yes">Tak (generuj [vcs] i .gitignore)</option>
        </select>
      </label>
      <div id="icloudWarn" class="warn" style="display:none;">
        Uwaga: Jeśli sejf leży w iCloud Drive, używanie <code>.git</code> wewnątrz iCloud jest zablokowane, aby uniknąć uszkodzenia repozytorium.
      </div>
      <p class="comment">Komentarz: Poza iCloud generuje sekcję <code>[vcs]</code> oraz <code>.gitignore</code> chroniący bazy <code>.db</code> i pliki tymczasowe.</p>
    </div>

    <div class="step" data-step="7">
      <h2>7. Moduł: Pokój 03 Projekty</h2>
      <label>Aktywować pokój projektowy?
        <select name="enable_projects" id="f_enable_projects">
          <option value="yes" selected>Włączony (zalecane)</option>
          <option value="no">Wyłączony</option>
        </select>
      </label>
      <p class="comment">Komentarz: Pokój <code>03 Projekty</code> służy do prowadzenia zamkniętych inicjatyw z własnymi kapsułami i raportami postępu.</p>
    </div>

    <div class="step" data-step="8">
      <h2>8. Moduł: Pokój 04 Operacje</h2>
      <label>Aktywować pokój operacyjny?
        <select name="enable_operations" id="f_enable_operations">
          <option value="yes" selected>Włączony (zalecane)</option>
          <option value="no">Wyłączony</option>
        </select>
      </label>
      <p class="comment">Komentarz: Pokój <code>04 Operacje</code> utrzymuje ciągłe rutyny, procedury SOP i odpowiedzialności cykliczne.</p>
    </div>

    <div class="step" data-step="9">
      <h2>9. Nazwy 7 agentów Zespołu AI</h2>
      <div class="grid2">
        <label>Koordynator <input type="text" name="agent_coordinator" value="{DEFAULT_AGENT_NAMES['coordinator']}"></label>
        <label>Audytor <input type="text" name="agent_auditor" value="{DEFAULT_AGENT_NAMES['auditor']}"></label>
        <label>Szef Sztabu / Inżynier <input type="text" name="agent_engineer" value="{DEFAULT_AGENT_NAMES['engineer']}"></label>
        <label>Architekt Danych <input type="text" name="agent_database_code" value="{DEFAULT_AGENT_NAMES['database_code']}"></label>
        <label>Architekt Designu <input type="text" name="agent_designer" value="{DEFAULT_AGENT_NAMES['designer']}"></label>
        <label>Badacz Źródeł <input type="text" name="agent_researcher" value="{DEFAULT_AGENT_NAMES['researcher']}"></label>
        <label>Specjalista Syntezy <input type="text" name="agent_knowledge" value="{DEFAULT_AGENT_NAMES['knowledge']}"></label>
      </div>
      <p class="comment">Komentarz: Personalizacja nazw automatycznie mapuje foldery w <code>08 Zespół AI/Role/</code> oraz wpisy w <code>AGENTS.md</code>.</p>
    </div>

    <div class="step" data-step="10">
      <h2>10. Środowisko AI – wybór programu</h2>
      <label>Środowisko AI
        <select name="llm_provider" id="f_llm_provider">
          <option value="antigravity" selected>Antigravity</option>
          <option value="claude">Claude</option>
        </select>
      </label>
      <p class="comment">Komentarz: Wybierz środowisko AI, w którym pracujesz. Instalator automatycznie skonfiguruje wszystkie potrzebne ustawienia w tle.</p>
    </div>

    <div class="step" data-step="11">
      <h2>11. Wygląd Obsidiana – tryb i motywy</h2>
      <div class="grid2">
        <label>Tryb kolorystyczny
          <select name="theme_mode" id="f_theme_mode">
            <option value="system" selected>system</option>
            <option value="light">light</option>
            <option value="dark">dark</option>
          </select>
        </label>
        <label>Motyw jasny
          <select name="theme_preset_light" id="f_theme_light">{light_options}</select>
        </label>
      </div>
      <label>Motyw ciemny (próbki kolorystyczne)</label>
      <div class="swatches">{theme_swatches}</div>
      <p class="comment">Komentarz: Motywy możesz w dowolnym momencie zmienić w <code>lab.config.toml</code> lub skryptem <code>lab-set-theme-raycast.sh</code>.</p>
    </div>

    <div class="step" data-step="12">
      <h2>12. Podsumowanie przed instalacją</h2>
      <div class="box" id="summaryBox"></div>
      <p class="comment">Komentarz: Sprawdź wszystkie ustawienia powyżej. Kliknięcie „Wróć” pozwoli poprawić dowolny krok bez utraty wpisanych danych, a „Zatwierdź i zainstaluj LAB” utworzy skarbiec.</p>
    </div>

    <div class="nav">
      <button class="btn btn-sec" type="button" id="prevBtn" onclick="changeStep(-1)" style="display:none;">Wróć</button>
      <button class="btn" type="button" id="nextBtn" onclick="changeStep(1)">Dalej</button>
      <button class="btn" type="submit" id="submitBtn" style="display:none;">Zatwierdź i zainstaluj LAB</button>
    </div>
  </form>
</div>
<script>
var currentStep = 1;
var totalSteps = 12;

function isIcloudEnabled() {{
  var ic = document.getElementById("icloudSelect").value;
  if (ic === "yes") return true;
  if (ic === "no") return false;
  var loc = document.getElementById("locSelect").value;
  if (loc === "__custom__") loc = document.getElementById("f_custom_location").value || "";
  return loc.indexOf("Mobile Documents") !== -1 || loc.indexOf("com~apple~CloudDocs") !== -1 || loc.indexOf("iCloud~md~obsidian") !== -1;
}}

function getVisibleSteps() {{
  var list = [];
  var skipIcloud = !isIcloudEnabled();
  for (var i = 1; i <= totalSteps; i++) {{
    if (i === 5 && skipIcloud) continue;
    list.push(i);
  }}
  return list;
}}

function showStep(n) {{
  var visible = getVisibleSteps();
  if (visible.indexOf(n) === -1) {{
    n = visible[0];
    currentStep = n;
  }}
  var steps = document.querySelectorAll(".step");
  for (var i = 0; i < steps.length; i++) {{
    steps[i].classList.toggle("active", (i + 1) === n);
  }}
  var idx = visible.indexOf(n) + 1;
  var visibleTotal = visible.length;
  var isLast = (idx === visibleTotal);
  var submitBtn = document.getElementById("submitBtn");
  document.getElementById("progressLabel").textContent = "Krok " + idx + " z " + visibleTotal;
  document.getElementById("prevBtn").style.display = (idx === 1) ? "none" : "inline-block";
  document.getElementById("nextBtn").style.display = isLast ? "none" : "inline-block";
  submitBtn.style.display = isLast ? "inline-block" : "none";
  submitBtn.disabled = !isLast;
  if (n === totalSteps) {{
    renderSummary();
  }}
}}

function changeStep(delta) {{
  var visible = getVisibleSteps();
  var idx = visible.indexOf(currentStep);
  if (idx === -1) idx = 0;
  var nextIdx = idx + delta;
  if (nextIdx < 0 || nextIdx >= visible.length) return;
  currentStep = visible[nextIdx];
  showStep(currentStep);
}}

function _esc(s) {{
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}}

function _agentVal(name) {{
  var el = document.querySelector('input[name="agent_' + name + '"]');
  return el ? el.value : "";
}}

function renderSummary() {{
  var loc = document.getElementById("locSelect").value;
  if (loc === "__custom__") loc = document.getElementById("f_custom_location").value;
  var instName = document.getElementById("f_instance_name").value || "LAB";
  var fullVaultPath = (loc || "").replace(/[/]+$/, "") + "/" + instName;
  var darkRadio = document.querySelector('input[name="theme_preset_dark"]:checked');
  var darkTheme = darkRadio ? darkRadio.value : "tokyonight";
  var lightTheme = document.getElementById("f_theme_light").value;
  var icloudConfirm = isIcloudEnabled() ? (document.getElementById("f_confirm_icloud").checked ? " (Optimize Storage = OFF: potwierdzone)" : " (Optimize Storage = OFF: niepotwierdzone)") : "";
  var agentsText = [
    "Koordynator: " + _agentVal("coordinator"),
    "Audytor: " + _agentVal("auditor"),
    "Inżynier: " + _agentVal("engineer"),
    "Architekt Danych: " + _agentVal("database_code"),
    "Design: " + _agentVal("designer"),
    "Badacz: " + _agentVal("researcher"),
    "Synteza: " + _agentVal("knowledge")
  ].map(_esc).join(", ");
  var html = "<ul>"
    + "<li><strong>Właściciel systemu:</strong> " + _esc(document.getElementById("f_user_name").value) + "</li>"
    + "<li><strong>Nazwa instancji LAB:</strong> " + _esc(instName) + "</li>"
    + "<li><strong>Lokalizacja skarbca:</strong> <code>" + _esc(fullVaultPath) + "</code></li>"
    + "<li><strong>iCloud Drive:</strong> " + _esc(document.getElementById("icloudSelect").value + icloudConfirm) + "</li>"
    + "<li><strong>GitHub:</strong> " + _esc(document.getElementById("githubSelect").value) + "</li>"
    + "<li><strong>Moduły (Projekty / Operacje):</strong> " + _esc(document.getElementById("f_enable_projects").value) + " / " + _esc(document.getElementById("f_enable_operations").value) + "</li>"
    + "<li><strong>Środowisko AI (LLM):</strong> " + _esc(document.getElementById("f_llm_provider").value) + "</li>"
    + "<li><strong>Motywy Obsidiana:</strong> tryb " + _esc(document.getElementById("f_theme_mode").value) + " (ciemny: " + _esc(darkTheme) + ", jasny: " + _esc(lightTheme) + ")</li>"
    + "<li><strong>Imiona agentów AI:</strong> " + agentsText + "</li>"
    + "</ul>";
  document.getElementById("summaryBox").innerHTML = html;
}}

function toggleCustomLoc(val) {{
  document.getElementById("customLocWrap").style.display = (val === "__custom__") ? "block" : "none";
  toggleIcloudWarn();
}}

function toggleIcloudWarn() {{
  var icEnabled = isIcloudEnabled();
  var gh = document.getElementById("githubSelect").value;
  document.getElementById("icloudWarn").style.display = (icEnabled && gh === "yes") ? "block" : "none";
  showStep(currentStep);
}}

var wizardForm = document.getElementById("wizardForm");
wizardForm.addEventListener("keydown", function(e) {{
  if (e.key === "Enter" && e.target && e.target.tagName !== "TEXTAREA") {{
    if (currentStep < totalSteps) {{
      e.preventDefault();
      changeStep(1);
    }}
  }}
}});
wizardForm.addEventListener("submit", function(e) {{
  if (currentStep < totalSteps) {{
    e.preventDefault();
    return false;
  }}
  return true;
}});
window.addEventListener("pageshow", function(e) {{
  if (e.persisted) {{
    currentStep = 1;
    showStep(1);
  }}
}});

showStep(1);
</script>
</body>
</html>"""


def _build_starter_prompt(coordinator_name: str = "Adam", owner_name: str = "") -> str:
    """Buduje spersonalizowany prompt inicjujący dla Koordynatora nowej instancji."""
    coord = (coordinator_name or "Adam").strip()
    owner_hint = f" (właściciel: {owner_name.strip()})" if owner_name and owner_name.strip() else ""
    return (
        f"Cześć {coord}! Właśnie uruchomiłem nową instancję LAB (mój Partner Zarządzania Wiedzą – PZW){owner_hint} "
        "i chcę rozpocząć pracę w Obsidianie.\n\n"
        f"Jesteś {coord} – moim Koordynatorem Zespołu AI oraz cierpliwym Wirtualnym Asystentem Wdrożenia LAB.\n\n"
        "Twoje zasady:\n"
        "1. Prowadź mnie za rękę krok po kroku. Zapytaj mnie najpierw, na jakim systemie pracuję "
        "(macOS czy Windows) i czy mam już zainstalowanego Obsidiana oraz Pythona 3.11+.\n"
        "2. Gdy potwierdzę otwarcie nowo utworzonego folderu w programie Obsidian, uspokój mnie, że Obsidian celowo "
        "ukrywa pliki techniczne poza AGENTS.md.\n"
        "3. Wytłumacz mi w 3 przejrzystych punktach, jak korzystać z 8 Pokoi LAB "
        "(przede wszystkim: czym różni się '01 Skrzynka' od '02 Warsztat' i '03 Projekty').\n"
        "4. Po sprawdzeniu gotowości środowiska wyjaśnij mi prosto zasady higieny sesji i telemetrii:\n"
        "   - dlaczego każda odpowiedź kończy się paskiem zużycia tokenów,\n"
        "   - co oznacza próg higieniczny 200–250k tokenów i ostrzeżenia przed limitem,\n"
        "   - jak bezpiecznie zamknąć sesję komendą /checkpoint i otworzyć nową sesję w Antigravity / Claude Code.\n"
        "5. Używaj prostego, naturalnego języka. Unikaj żargonu programistycznego. "
        "Po każdym kroku zadawaj mi jedno pytanie sprawdzające, czy wszystko poszło zgodnie z planem, "
        "zanim przejdziesz dalej.\n\n"
        "Zacznij od powitania (przedstaw się jako mój Koordynator) i pierwszego pytania!"
    )


_STARTER_PROMPT_TEXT = _build_starter_prompt()


def _load_starter_card_html() -> str:
    """Wczytuje pełną zawartość Karty Startowej HTML z paczki docs/ lub konfiguracji [vcs].public_docs."""
    candidates: List[Path] = [_root_dir / "docs" / "Karta_Startowa_LAB.html"]
    try:
        from core_config import ConfigManager
        cfg = ConfigManager(vault_root=_root_dir)
        public_docs = cfg._raw_data.get("vcs", {}).get("public_docs", [])
        if isinstance(public_docs, list):
            for rel in public_docs:
                if isinstance(rel, str) and rel.endswith("Karta_Startowa_LAB.html"):
                    candidates.append(_root_dir / rel)
    except Exception:
        pass
    for path in candidates:
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                continue
    return ""


def _render_result_html(result: InstallResult, options: Optional[InstallOptions] = None) -> str:
    """Generuje ekran końcowy kreatora WWW."""
    target_str = str(result.target_dir)
    target_js = html.escape(json.dumps(target_str), quote=True)
    status_title = "Instancja LAB została utworzona" if result.success else "Instalacja zakończona z błędem"
    warnings_html = "".join(f'<div class="warn">{html.escape(w)}</div>' for w in result.warnings)
    err_html = f'<div class="warn">{html.escape(result.error_message)}</div>' if result.error_message else ""
    smoke_joined = " ".join(result.smoke_check_details)
    cfg_status = "Błąd" if "BŁĄD" in smoke_joined or not result.success else "OK"
    db_status = "Błąd" if ("SQLite" in smoke_joined and "BŁĄD" in smoke_joined) or not result.success else "OK"
    env_status = "Błąd" if ("session_bootstrap" in smoke_joined and "BŁĄD" in smoke_joined) or not result.success else "OK"

    owner_name = options.user_name if options else "—"
    inst_name = options.instance_name if options else result.instance_name
    icloud_txt = (
        ("włączony" if options.icloud_enabled else "wyłączony")
        if (options and options.icloud_enabled is not None)
        else "autodetekcja"
    )
    github_txt = ("włączony" if options.github_enabled else "wyłączony") if options else "—"
    modules_txt = (
        f"Projekty: {'tak' if options.enable_projects else 'nie'}, Operacje: {'tak' if options.enable_operations else 'nie'}"
        if options
        else "—"
    )
    llm_txt = options.llm_provider if options else "—"
    theme_txt = (
        f"tryb {options.theme_mode} (ciemny: {options.theme_preset_dark}, jasny: {options.theme_preset_light})"
        if options
        else "—"
    )
    agents_map = options.agent_names if (options and options.agent_names) else DEFAULT_AGENT_NAMES
    agents_summary = ", ".join(
        f"{label}: {agents_map.get(key, DEFAULT_AGENT_NAMES[key])}"
        for key, label in (
            ("coordinator", "Koordynator"),
            ("auditor", "Audytor"),
            ("engineer", "Inżynier"),
            ("database_code", "Architekt Danych"),
            ("designer", "Design"),
            ("researcher", "Badacz"),
            ("knowledge", "Synteza"),
        )
    )
    config_summary_html = f"""
  <div class="box" id="installedConfigSummary">
    <strong>Podsumowanie zastosowanej konfiguracji:</strong>
    <ul>
      <li>Właściciel systemu: <strong>{html.escape(owner_name)}</strong></li>
      <li>Nazwa instancji LAB: <strong>{html.escape(inst_name)}</strong></li>
      <li>Lokalizacja skarbca: <code>{html.escape(target_str)}</code></li>
      <li>iCloud / GitHub: <strong>{html.escape(icloud_txt)}</strong> / <strong>{html.escape(github_txt)}</strong></li>
      <li>Moduły: <strong>{html.escape(modules_txt)}</strong></li>
      <li>Środowisko AI (LLM): <strong>{html.escape(llm_txt)}</strong></li>
      <li>Motyw Obsidiana: <strong>{html.escape(theme_txt)}</strong></li>
      <li>Imiona agentów AI: <strong>{html.escape(agents_summary)}</strong></li>
    </ul>
  </div>"""
    coord_name = (
        (options.agent_names or {}).get("coordinator") or options.coordinator_name
        if options
        else DEFAULT_AGENT_NAMES["coordinator"]
    )
    starter_prompt_text = _build_starter_prompt(coord_name, options.user_name if options else "")
    starter_prompt_js = html.escape(json.dumps(starter_prompt_text), quote=True)
    starter_card_html = _load_starter_card_html()
    starter_card_section = (
        f"""
  <div class="box">
    <details open>
      <summary style="cursor:pointer;font-weight:700;color:#93c5fd;">📖 Pełna Karta Startowa LAB (przewodnik użytkownika)</summary>
      <p style="margin:8px 0;font-size:12px;color:#94a3b8;">Plik źródłowy znajdziesz też w katalogu <code>docs/Karta_Startowa_LAB.html</code> (oraz <code>.md</code>) w paczce instalacyjnej.</p>
      <iframe title="Karta Startowa LAB" srcdoc="{html.escape(starter_card_html, quote=True)}" style="width:100%;height:520px;border:1px solid #334155;border-radius:8px;background:#fff;"></iframe>
    </details>
  </div>"""
        if starter_card_html
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<title>LAB – Wynik instalacji</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }}
  .card {{ max-width: 720px; margin: 0 auto; background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 28px; }}
  .btn {{ display: inline-block; margin-top: 10px; padding: 10px 16px; border: 0; border-radius: 8px; background: #0284c7; color: #fff; text-decoration: none; font-size: 14px; font-weight: 600; cursor: pointer; }}
  .btn:hover {{ background: #0369a1; }}
  .warn {{ background: #451a03; border: 1px solid #d97706; color: #fde68a; padding: 10px; border-radius: 8px; margin-top: 10px; font-size: 13px; }}
  .box {{ background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 14px; margin-top: 16px; font-size: 13px; }}
  code {{ color: #93c5fd; }}
</style>
</head>
<body>
<div class="card">
  <h1>{html.escape(status_title)}</h1>
  <p>Lokalizacja sejfu: <code id="vaultPath">{html.escape(target_str)}</code> ({result.elapsed_seconds}s)</p>
  <button class="btn" type="button" id="copyPathBtn" onclick="copyVaultPath()">Kopiuj ścieżkę sejfu</button>
  {err_html}
  {warnings_html}

  <div class="box">
    <strong>Jak otworzyć sejf w Obsidianie:</strong>
    <ol>
      <li>Otwórz aplikację <strong>Obsidian</strong>.</li>
      <li>Wybierz <strong>&bdquo;Open folder as vault&rdquo;</strong> (Otwórz folder jako skarbiec).</li>
      <li>Wskaż ścieżkę: <code>{html.escape(target_str)}</code>.</li>
    </ol>
  </div>

  <div class="box">
    <strong>Pierwsze kroki w Obsidianie:</strong>
    <ol>
      <li>Otwórz Ustawienia &rarr; <em>Community plugins</em>.</li>
      <li>Wyłącz <em>Restricted Mode</em>, klikając <strong>Turn on community plugins</strong>.</li>
      <li>Włącz wtyczki: <code>obsidian-advanced-uri</code>, <code>obsidian-shellcommands</code>, <code>sqlite-db-viewer</code>.</li>
    </ol>
  </div>

  <div class="box">
    <strong>Opcjonalne narzędzia (dokumentacja w repozytorium):</strong>
    <ul>
      <li><strong>Raycast (macOS):</strong> 6 skryptów w <code>08 Zespół AI/Skrypty/integrations/raycast/</code>.</li>
      <li><strong>Wispr Flow:</strong> przepływ dyktowania notatek z <code>drain_inbox.py</code>.</li>
    </ul>
  </div>

  <div class="box">
    <strong>Weryfikacja po instalacji:</strong>
    <ul>
      <li>Konfiguracja: <strong>{cfg_status}</strong></li>
      <li>Baza danych: <strong>{db_status}</strong></li>
      <li>Środowisko: <strong>{env_status}</strong></li>
    </ul>
  </div>
  {config_summary_html}

  <div class="box">
    <strong>🤖 Łatwy start z AI — Prompt Inicjujący (z Karty Startowej):</strong>
    <p style="margin:6px 0 8px 0;">W pliku <code>Karta_Startowa_LAB.html</code> / <code>.md</code> znajduje się gotowy prompt inicjujący, który ułatwi rozpoczęcie pracy z systemem LAB. Skopiuj go poniżej i wklej do swojego czatu AI:</p>
    <pre id="starterPromptPre" style="white-space:pre-wrap;background:#020617;border:1px solid #334155;border-radius:8px;padding:12px;font-size:12px;line-height:1.45;color:#cbd5e1;margin:0;">{html.escape(starter_prompt_text)}</pre>
    <button class="btn" type="button" id="copyPromptBtn" onclick="copyStarterPrompt()">Kopiuj prompt inicjujący</button>
    <div id="copyErrorMsg" class="warn" style="display:none;margin-top:8px;"></div>
  </div>
  {starter_card_section}
</div>
<script>
function _fallbackCopyText(text) {{
  var ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  var ok = false;
  try {{
    ok = document.execCommand("copy");
  }} catch (e) {{
    ok = false;
  }}
  document.body.removeChild(ta);
  return ok;
}}
function _copyTextWithFallback(text, btn, okLabel, errLabel) {{
  var errBox = document.getElementById("copyErrorMsg");
  function _onFail() {{
    btn.textContent = errLabel;
    if (errBox) {{
      errBox.style.display = "block";
      errBox.textContent = "Przeglądarka zablokowała automatyczny dostęp do schowka. Zaznacz tekst powyżej i naciśnij Cmd+C (Mac) lub Ctrl+C (Windows).";
    }}
  }}
  function _onOk() {{
    btn.textContent = okLabel;
    if (errBox) {{ errBox.style.display = "none"; }}
  }}
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).then(_onOk).catch(function() {{
      if (_fallbackCopyText(text)) {{ _onOk(); }} else {{ _onFail(); }}
    }});
  }} else if (_fallbackCopyText(text)) {{
    _onOk();
  }} else {{
    _onFail();
  }}
}}
function copyVaultPath() {{
  _copyTextWithFallback({target_js}, document.getElementById("copyPathBtn"), "Skopiowano ścieżkę!", "Nie udało się skopiować — skopiuj ręcznie");
}}
function copyStarterPrompt() {{
  _copyTextWithFallback({starter_prompt_js}, document.getElementById("copyPromptBtn"), "Skopiowano prompt!", "Błąd schowka — skopiuj ręcznie powyżej");
}}
</script>
</body>
</html>"""


def _build_options_from_form(form: Dict[str, List[str]]) -> InstallOptions:
    """Buduje InstallOptions z danych formularza WWW."""
    def _get(key: str, default: str = "") -> str:
        vals = form.get(key, [])
        return vals[0].strip() if vals else default

    instance_name = _get("instance_name", "LAB") or "LAB"
    user_name = _get("user_name", "Owner") or "Owner"
    loc_preset = _get("location_preset", "")
    parent_dir = _get("custom_location", "") if loc_preset == "__custom__" or not loc_preset else loc_preset
    if not parent_dir:
        parent_dir = str(Path.home() / "Documents")
    target_dir = (Path(parent_dir).expanduser() / instance_name).resolve()

    icloud_raw = _get("icloud_enabled", "auto")
    icloud_enabled: Optional[bool] = None
    if icloud_raw == "yes":
        icloud_enabled = True
    elif icloud_raw == "no":
        icloud_enabled = False

    agent_names = {
        key: _get(f"agent_{key}", default_val) or default_val
        for key, default_val in DEFAULT_AGENT_NAMES.items()
    }
    dark_theme = _get("theme_preset_dark", "tokyonight") or "tokyonight"
    light_theme = _get("theme_preset_light", "dawn") or "dawn"
    theme_mode = _get("theme_mode", "system") or "system"

    llm_provider = _get("llm_provider", "antigravity") or "antigravity"
    default_llm_model = "auto" if llm_provider == "antigravity" else ""
    llm_model = _get("llm_model", default_llm_model) or default_llm_model

    return InstallOptions(
        target_dir=target_dir,
        instance_name=instance_name,
        user_name=user_name,
        call_sign=user_name,
        theme_preset=light_theme if theme_mode == "light" else dark_theme,
        theme_preset_light=light_theme,
        theme_preset_dark=dark_theme,
        theme_mode=theme_mode,
        coordinator_name=agent_names["coordinator"],
        governance_name=agent_names["auditor"],
        agent_names=agent_names,
        force=False,
        skip_preflight=True,
        copy_engine=True,
        icloud_enabled=icloud_enabled,
        github_enabled=_get("github_enabled", "no") == "yes",
        enable_projects=_get("enable_projects", "yes") == "yes",
        enable_operations=_get("enable_operations", "yes") == "yes",
        llm_provider=llm_provider,
        llm_model=llm_model,
        confirm_icloud_optimize_off=_get("confirm_icloud_optimize_off", "") in ("yes", "on", "true", "1"),
    )


def _make_wizard_handler(
    done_event: threading.Event,
    result_holder: Dict[str, InstallResult],
):
    """Tworzy handler HTTP dla kreatora WWW z nagłówkami blokującymi cache."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_html(self, html_text: str) -> None:
            body = html_text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.split("?")[0] != "/":
                self.send_error(404)
                return
            self._send_html(_render_wizard_html())

        def do_POST(self) -> None:
            if self.path.split("?")[0] != "/install":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            form = urllib.parse.parse_qs(raw, keep_blank_values=True)
            options = _build_options_from_form(form)
            res = LabEngineInstaller(options).run()
            result_holder["result"] = res
            self._send_html(_render_result_html(res, options=options))
            threading.Timer(0.4, done_event.set).start()

    return Handler


def run_web_wizard(open_browser: bool = True) -> int:
    """Uruchamia lokalny kreator WWW na 127.0.0.1 (losowy wolny port)."""
    done_event = threading.Event()
    result_holder: Dict[str, InstallResult] = {}
    handler_cls = _make_wizard_handler(done_event, result_holder)

    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"Kreator WWW LAB {LAB_VERSION} uruchomiony pod adresem: {url}")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()
    try:
        done_event.wait()
    except KeyboardInterrupt:
        print("\nPrzerwano działanie kreatora WWW.")
        server.shutdown()
        server.server_close()
        return 130

    server.shutdown()
    server.server_close()
    res = result_holder.get("result")
    return 0 if (res and res.success) else 1


def prompt_user(question: str, default: str) -> str:
    """Pomocnik bezpiecznego odpytywania użytkownika z wartością domyślną."""
    try:
        reply = input(f"{question} [{default}]: ").strip()
        return reply if reply else default
    except (EOFError, KeyboardInterrupt):
        print("\n\nPrzerwano instalację.")
        sys.exit(130)


def prompt_yes_no(question: str, default: bool = True) -> bool:
    """Pomocnik pytań tak/nie."""
    def_str = "T/n" if default else "t/N"
    try:
        reply = input(f"{question} ({def_str}): ").strip().lower()
        if not reply:
            return default
        return reply in ("t", "tak", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        print("\n\nPrzerwano instalację.")
        sys.exit(130)


def run_interactive_wizard() -> InstallOptions:
    """Prowadzi użytkownika przez interaktywny wywiad CLI."""
    print(f"\nLAB {LAB_VERSION} - PARTNER ZARZĄDZANIA WIEDZĄ (PZW)\n")
    diag = run_preflight_diagnostics()
    print(diag.format_cli())
    if not diag.all_critical_passed and not prompt_yes_no("Kontynuować mimo ostrzeżeń?", default=False):
        sys.exit(1)

    default_target = str(Path.home() / "Documents" / "LAB")
    target_dir = Path(prompt_user("1. Ścieżka docelowa dla nowego skarbca", default_target)).expanduser().resolve()
    instance_name = prompt_user("2. Nazwa instancji środowiska", "LAB")
    default_user = os.environ.get("USER", "Owner").capitalize()
    user_name = prompt_user("3. Twoje imię / zwrot", default_user)
    call_sign = prompt_user("4. Twój pseudonim operacyjny", user_name)

    return InstallOptions(
        target_dir=target_dir,
        instance_name=instance_name,
        user_name=user_name,
        call_sign=call_sign,
        force=False,
        skip_preflight=True,
        copy_engine=True,
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Główny punkt wejścia CLI/WWW instalatora LAB 3.0."""
    try:
        check_python_version()
    except RuntimeError as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(
        description=f"LAB {LAB_VERSION} - Zunifikowany Kreator i Instalator Środowiska"
    )
    parser.add_argument("--target-dir", "-t", type=str, default=None, help="Ścieżka docelowa nowego skarbca")
    parser.add_argument("--name", "-n", type=str, default="LAB", help="Nazwa instancji systemu")
    parser.add_argument("--user", "-u", type=str, default=None, help="Imię właściciela systemu")
    parser.add_argument("--call-sign", type=str, default=None, help="Pseudonim operacyjny")
    parser.add_argument("--theme", type=str, default="tokyonight", help="Motyw ciemny / główny")
    parser.add_argument("--theme-light", type=str, default="dawn", help="Motyw jasny")
    parser.add_argument("--theme-mode", choices=["system", "light", "dark"], default="system", help="Tryb kolorystyczny")
    parser.add_argument("--coordinator", type=str, default="Adam", help="Imię Koordynatora AI")
    parser.add_argument("--auditor", type=str, default="Ada", help="Imię Audytora AI")
    parser.add_argument("--profile", type=str, choices=["full", "project"], default="full", help="Profil instancji")
    parser.add_argument("--github", action="store_true", help="Włącz sekcję [vcs] i .gitignore")
    parser.add_argument("--icloud", action="store_true", help="Deklaruj przechowywanie w iCloud")
    parser.add_argument("--no-projects", action="store_true", help="Wyłącz pokój 03 Projekty")
    parser.add_argument("--no-operations", action="store_true", help="Wyłącz pokój 04 Operacje")
    parser.add_argument("--llm-provider", type=str, default="antigravity", help="Dostawca LLM (antigravity/claude)")
    parser.add_argument("--llm-model", type=str, default="auto", help="Identyfikator modelu LLM")
    parser.add_argument("--force", "-f", action="store_true", help="Nadpisz istniejące pliki (tworzy .bak)")
    parser.add_argument("--skip-preflight", action="store_true", help="Pomiń diagnostykę pre-flight")
    parser.add_argument("--diagnostics-only", action="store_true", help="Tylko diagnostyka środowiska")
    parser.add_argument("--non-interactive", action="store_true", help="Tryb skryptowy bez pytań")
    parser.add_argument("--cli", action="store_true", help="Uruchom wywiad terminalowy zamiast kreatora WWW")
    parser.add_argument("--web", action="store_true", help="Uruchom lokalny kreator WWW (domyślny bez flag)")

    args = parser.parse_args(argv)

    if args.diagnostics_only:
        report = run_preflight_diagnostics()
        print(report.format_cli())
        return 0 if report.all_critical_passed else 1

    if args.web or (not args.non_interactive and not args.cli and args.target_dir is None):
        return run_web_wizard(open_browser=True)

    if args.cli and not args.non_interactive and args.target_dir is None:
        options = run_interactive_wizard()
    else:
        if not args.target_dir:
            print("BŁĄD: W trybie --non-interactive wymagane jest --target-dir / -t", file=sys.stderr)
            return 2
        target_dir = Path(args.target_dir).expanduser().resolve()
        user_name = args.user or os.environ.get("USER", "Owner").capitalize()
        options = InstallOptions(
            target_dir=target_dir,
            instance_name=args.name,
            user_name=user_name,
            call_sign=args.call_sign or user_name,
            theme_preset=args.theme,
            theme_preset_light=args.theme_light,
            theme_preset_dark=args.theme,
            theme_mode=args.theme_mode,
            coordinator_name=args.coordinator,
            governance_name=args.auditor,
            force=args.force,
            skip_preflight=args.skip_preflight,
            copy_engine=True,
            profile=args.profile,
            icloud_enabled=True if args.icloud else None,
            github_enabled=args.github,
            enable_projects=not args.no_projects,
            enable_operations=not args.no_operations,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
        )

    print(f"\nInicjalizacja instancji '{options.instance_name}' (LAB {LAB_VERSION}) w: {options.target_dir}...")
    result = LabEngineInstaller(options).run()

    if not result.success:
        print(f"\n❌ BŁĄD INSTALACJI: {result.error_message}", file=sys.stderr)
        if result.preflight:
            print(result.preflight.format_cli())
        return 1

    print("\n" + "=" * 60)
    print(f" ✨ SUKCES! Instancja '{result.instance_name}' (v{LAB_VERSION}) powołana w {result.elapsed_seconds}s!")
    print(f" 📦 Utworzono elementów : {len(result.created_items)}")
    print(f" ⏩ Pominięto elementów : {len(result.skipped_items)}")
    print(f" 📂 Ścieżka skarbca     : {result.target_dir}")
    for w in result.warnings:
        print(f" ⚠️  {w}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
