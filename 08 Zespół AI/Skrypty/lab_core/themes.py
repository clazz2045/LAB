"""lab_core.themes - Katalog motywów i generator tokenów stylów CSS (Design System)
Standard inżynieryjny: 100% Python Standard Library. Zero zewnętrznych zależności pip.
Wzorzec: Design Tokens dla Obsidian (Framework PZW / LAB 3.0).
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .config import ConfigManager


THEME_PRESETS: Dict[str, Dict[str, str]] = {
    # --- MOTYWY CIEMNE (DARK THEMES) ---
    "rosepine": {
        "name": "Rosé Pine",
        "mode": "dark",
        "canvas": "#191724",
        "surface1": "#1f1d2e",
        "surface2": "#26233a",
        "surface3": "#403d52",
        "border": "#26233a",
        "text_primary": "#e0def4",
        "text_secondary": "#908caa",
        "accent": "#eb6f92",
    },
    "evergreen": {
        "name": "Evergreen Forest",
        "mode": "dark",
        "canvas": "#141c16",
        "surface1": "#1a241d",
        "surface2": "#223126",
        "surface3": "#2d4233",
        "border": "#283b2e",
        "text_primary": "#e1ebe3",
        "text_secondary": "#8da392",
        "accent": "#52b788",
    },
    "tokyonight": {
        "name": "Tokyo Night",
        "mode": "dark",
        "canvas": "#1a1b26",
        "surface1": "#1f2335",
        "surface2": "#24283b",
        "surface3": "#292e42",
        "border": "#292e42",
        "text_primary": "#c0caf5",
        "text_secondary": "#7982a9",
        "accent": "#7aa2f7",
    },
    "kanagawa": {
        "name": "Kanagawa Wave",
        "mode": "dark",
        "canvas": "#1f1f28",
        "surface1": "#16161d",
        "surface2": "#2a2a37",
        "surface3": "#363646",
        "border": "#2d4f67",
        "text_primary": "#dcd7ba",
        "text_secondary": "#727169",
        "accent": "#7e9cd8",
    },
    "onedark": {
        "name": "One Dark",
        "mode": "dark",
        "canvas": "#282c34",
        "surface1": "#21252b",
        "surface2": "#2c313a",
        "surface3": "#3e4451",
        "border": "#3e4451",
        "text_primary": "#abb2bf",
        "text_secondary": "#5c6370",
        "accent": "#61afef",
    },
    "gruvbox": {
        "name": "Gruvbox Dark",
        "mode": "dark",
        "canvas": "#282828",
        "surface1": "#1d2021",
        "surface2": "#3c3836",
        "surface3": "#504945",
        "border": "#3c3836",
        "text_primary": "#ebdbb2",
        "text_secondary": "#a89984",
        "accent": "#fabd2f",
    },
    "dracula": {
        "name": "Dracula",
        "mode": "dark",
        "canvas": "#282a36",
        "surface1": "#21222c",
        "surface2": "#44475a",
        "surface3": "#6272a4",
        "border": "#44475a",
        "text_primary": "#f8f8f2",
        "text_secondary": "#6272a4",
        "accent": "#bd93f9",
    },
    "nordic": {
        "name": "Nordic Dark",
        "mode": "dark",
        "canvas": "#2e3440",
        "surface1": "#272c36",
        "surface2": "#3b4252",
        "surface3": "#434c5e",
        "border": "#434c5e",
        "text_primary": "#eceff4",
        "text_secondary": "#d8dee9",
        "accent": "#88c0d0",
    },
    "everforest": {
        "name": "Everforest Dark",
        "mode": "dark",
        "canvas": "#2d353b",
        "surface1": "#232a2e",
        "surface2": "#343f44",
        "surface3": "#475258",
        "border": "#3d484d",
        "text_primary": "#d3c6aa",
        "text_secondary": "#859289",
        "accent": "#a7c080",
    },
    "catppuccin": {
        "name": "Catppuccin Mocha",
        "mode": "dark",
        "canvas": "#1e1e2e",
        "surface1": "#181825",
        "surface2": "#313244",
        "surface3": "#45475a",
        "border": "#313244",
        "text_primary": "#cdd6f4",
        "text_secondary": "#a6adc8",
        "accent": "#cba6f7",
    },
    "ayu": {
        "name": "Ayu Dark",
        "mode": "dark",
        "canvas": "#0b0e14",
        "surface1": "#131721",
        "surface2": "#1f2430",
        "surface3": "#272d38",
        "border": "#191e2a",
        "text_primary": "#b3b1ad",
        "text_secondary": "#626a73",
        "accent": "#e6b450",
    },
    "synthwave": {
        "name": "Synthwave '84",
        "mode": "dark",
        "canvas": "#262335",
        "surface1": "#1a1824",
        "surface2": "#34294f",
        "surface3": "#463465",
        "border": "#463465",
        "text_primary": "#f92aad",
        "text_secondary": "#848bbd",
        "accent": "#36f9f6",
    },
    "horizon": {
        "name": "Horizon Dark",
        "mode": "dark",
        "canvas": "#1c1e26",
        "surface1": "#232530",
        "surface2": "#2e303e",
        "surface3": "#3d4052",
        "border": "#2e303e",
        "text_primary": "#d5d8da",
        "text_secondary": "#6c6f93",
        "accent": "#e95678",
    },
    # --- MOTYWY JASNE (LIGHT THEMES) ---
    "dawn": {
        "name": "Rosé Pine Dawn",
        "mode": "light",
        "canvas": "#faf4ed",
        "surface1": "#fffaf3",
        "surface2": "#f2e9de",
        "surface3": "#e4dbcd",
        "border": "#dfdad3",
        "text_primary": "#575279",
        "text_secondary": "#797593",
        "accent": "#b45309",
    },
    "nordic-snow": {
        "name": "Nordic Snow",
        "mode": "light",
        "canvas": "#f8fafc",
        "surface1": "#ffffff",
        "surface2": "#f1f5f9",
        "surface3": "#e2e8f0",
        "border": "#cbd5e1",
        "text_primary": "#0f172a",
        "text_secondary": "#64748b",
        "accent": "#0284c7",
    },
    "solarized-light": {
        "name": "Solarized Light",
        "mode": "light",
        "canvas": "#fdf6e3",
        "surface1": "#eee8d5",
        "surface2": "#e0d7c3",
        "surface3": "#d3c7b2",
        "border": "#d3c7b2",
        "text_primary": "#073642",
        "text_secondary": "#586e75",
        "accent": "#268bd2",
    },
    "github-light": {
        "name": "GitHub Light",
        "mode": "light",
        "canvas": "#ffffff",
        "surface1": "#f6f8fa",
        "surface2": "#eaeef2",
        "surface3": "#d0d7de",
        "border": "#d0d7de",
        "text_primary": "#1f2328",
        "text_secondary": "#656d76",
        "accent": "#0969da",
    },
    "everforest-light": {
        "name": "Everforest Light",
        "mode": "light",
        "canvas": "#fdf6e3",
        "surface1": "#f4edd8",
        "surface2": "#eadfc5",
        "surface3": "#dfd3b4",
        "border": "#dfd3b4",
        "text_primary": "#3c4841",
        "text_secondary": "#708075",
        "accent": "#487e5b",
    },
    "gruvbox-light": {
        "name": "Gruvbox Light",
        "mode": "light",
        "canvas": "#fbf1c7",
        "surface1": "#f2e5bc",
        "surface2": "#ebdbb2",
        "surface3": "#d5c4a1",
        "border": "#d5c4a1",
        "text_primary": "#3c3836",
        "text_secondary": "#7c6f64",
        "accent": "#b57614",
    },
    "paper": {
        "name": "Paper Minimal",
        "mode": "light",
        "canvas": "#fafafa",
        "surface1": "#ffffff",
        "surface2": "#f4f4f5",
        "surface3": "#e4e4e7",
        "border": "#e4e4e7",
        "text_primary": "#18181b",
        "text_secondary": "#71717a",
        "accent": "#2563eb",
    },
}


def get_theme_preset(preset_key: Optional[str] = None) -> Dict[str, str]:
    """Zwraca słownik definicji motywu (domyślnie 'rosepine')."""
    key = (preset_key or "").strip().lower()
    return THEME_PRESETS.get(key, THEME_PRESETS["rosepine"])


def list_theme_presets() -> List[str]:
    """Zwraca posortowaną listę identyfikatorów dostępnych motywów."""
    return sorted(THEME_PRESETS.keys())


def _render_theme_block(mode_name: str, theme: Dict[str, str]) -> List[str]:
    """Generuje kompletny blok stylów dla danego trybu ('theme-light', 'theme-dark' lub 'all')."""
    if mode_name == "all":
        pfx_vars = "body, body.theme-light, body.theme-dark, .theme-light, .theme-dark"
        pfx_leaf = ".workspace-leaf-content, .view-content, .markdown-source-view, .markdown-rendered, .cm-scroller"
        pfx_nav = '.workspace-leaf-content[data-type="file-explorer"], .nav-files-container, .workspace-ribbon, .workspace-split.mod-left-split, .workspace-split.mod-right-split'
        pfx_head = ".inline-title, .markdown-rendered h1, .cm-header-1, .markdown-rendered h2, .cm-header-2, .markdown-rendered h3, .cm-header-3"
        pfx_act = ".nav-file-title.is-active .nav-file-title-content"
    else:
        cls = mode_name
        pfx_vars = f"body.{cls}, .{cls}"
        pfx_leaf = f"body.{cls} .workspace-leaf-content, .{cls} .workspace-leaf-content, body.{cls} .view-content, .{cls} .view-content, body.{cls} .markdown-source-view, .{cls} .markdown-source-view, body.{cls} .markdown-rendered, .{cls} .markdown-rendered, body.{cls} .cm-scroller, .{cls} .cm-scroller"
        pfx_nav = f'body.{cls} .workspace-leaf-content[data-type="file-explorer"], .{cls} .workspace-leaf-content[data-type="file-explorer"], body.{cls} .nav-files-container, .{cls} .nav-files-container, body.{cls} .workspace-ribbon, .{cls} .workspace-ribbon, body.{cls} .workspace-split.mod-left-split, .{cls} .workspace-split.mod-left-split, body.{cls} .workspace-split.mod-right-split, .{cls} .workspace-split.mod-right-split'
        pfx_head = f"body.{cls} .inline-title, .{cls} .inline-title, body.{cls} .markdown-rendered h1, .{cls} .markdown-rendered h1, body.{cls} .cm-header-1, .{cls} .cm-header-1, body.{cls} .markdown-rendered h2, .{cls} .markdown-rendered h2, body.{cls} .cm-header-2, .{cls} .cm-header-2, body.{cls} .markdown-rendered h3, .{cls} .markdown-rendered h3, body.{cls} .cm-header-3, .{cls} .cm-header-3"
        pfx_act = f"body.{cls} .nav-file-title.is-active .nav-file-title-content, .{cls} .nav-file-title.is-active .nav-file-title-content"

    return [
        f"{pfx_vars} {{",
        f"  --background-primary: {theme['canvas']} !important;",
        f"  --background-primary-alt: {theme['surface1']} !important;",
        f"  --background-secondary: {theme['surface1']} !important;",
        f"  --background-secondary-alt: {theme['surface2']} !important;",
        f"  --background-modifier-border: {theme['border']} !important;",
        f"  --background-modifier-form-field: {theme['surface2']} !important;",
        "",
        "  /* Tokeny bazowe Obsidian v1.0+ */",
        f"  --color-base-00: {theme['canvas']} !important;",
        f"  --color-base-10: {theme['surface1']} !important;",
        f"  --color-base-20: {theme['surface1']} !important;",
        f"  --color-base-25: {theme['surface2']} !important;",
        f"  --color-base-30: {theme['surface2']} !important;",
        f"  --color-base-35: {theme['surface3']} !important;",
        f"  --color-base-40: {theme['border']} !important;",
        f"  --color-base-50: {theme['border']} !important;",
        "",
        f"  --text-normal: {theme['text_primary']} !important;",
        f"  --text-muted: {theme['text_secondary']} !important;",
        f"  --text-faint: {theme['text_secondary']} !important;",
        f"  --text-accent: {theme['accent']} !important;",
        f"  --text-accent-hover: {theme['accent']} !important;",
        "",
        f"  --h1-color: {theme['text_primary']} !important;",
        f"  --h2-color: {theme['text_primary']} !important;",
        f"  --h3-color: {theme['text_primary']} !important;",
        f"  --h4-color: {theme['text_primary']} !important;",
        f"  --h5-color: {theme['text_primary']} !important;",
        f"  --h6-color: {theme['text_primary']} !important;",
        f"  --inline-title-color: {theme['text_primary']} !important;",
        "",
        f"  --interactive-accent: {theme['accent']} !important;",
        f"  --interactive-accent-hover: {theme['accent']} !important;",
        f"  --nav-item-color-active: {theme['accent']} !important;",
        f"  --tab-text-color-active: {theme['accent']} !important;",
        "}",
        "",
        f"{pfx_leaf} {{",
        f"  background-color: {theme['canvas']} !important;",
        "}",
        "",
        f"{pfx_nav} {{",
        f"  background-color: {theme['surface1']} !important;",
        "}",
        "",
        f"{pfx_head} {{",
        f"  color: {theme['text_primary']} !important;",
        "}",
        "",
        f"{pfx_act} {{",
        f"  color: {theme['accent']} !important;",
        "  font-weight: 600;",
        "}",
        "",
    ]


def generate_design_tokens_css(config: ConfigManager) -> str:
    """Generuje deterministyczny arkusz stylów CSS z tokenami motywu i pokoi."""
    pres = config.presentation
    preset_key = getattr(pres, "theme_preset", "tokyonight").strip().lower()
    light_key = getattr(pres, "theme_preset_light", "dawn").strip().lower()
    dark_key = getattr(pres, "theme_preset_dark", "tokyonight").strip().lower()
    theme_mode = getattr(pres, "theme_mode", "system").strip().lower()

    theme_light = get_theme_preset(light_key)
    theme_dark = get_theme_preset(dark_key)
    active_theme = get_theme_preset(preset_key)

    font_sans = pres.font_sans
    font_mono = pres.font_mono

    lines: List[str] = [
        "/* ==========================================================================",
        f"   {config.system.instance_name} (Framework PZW) — Dynamiczne Tokeny Design Systemu",
        f"   Tryb: {theme_mode.upper()} | Jasny: {theme_light['name']} ({light_key}) | Ciemny: {theme_dark['name']} ({dark_key})",
        "   Wygenerowano deterministycznie z: lab.config.toml",
        "   ========================================================================== */",
        "",
        ":root {",
        "  /* Typografia interfejsu */",
        f"  --lab-font-sans: {font_sans};",
        f"  --lab-font-mono: {font_mono};",
        "  --font-interface-theme: var(--lab-font-sans);",
        "  --font-text-theme: var(--lab-font-sans);",
        "  --font-monospace-theme: var(--lab-font-mono);",
        "",
        f"  /* Paleta powierzchniowa motywu aktywnego ({active_theme['name']}) */",
        f"  --lab-canvas: {active_theme['canvas']};",
        f"  --lab-surface-1: {active_theme['surface1']};",
        f"  --lab-surface-2: {active_theme['surface2']};",
        f"  --lab-surface-3: {active_theme['surface3']};",
        f"  --lab-border: {active_theme['border']};",
        f"  --lab-text-primary: {active_theme['text_primary']};",
        f"  --lab-text-secondary: {active_theme['text_secondary']};",
        f"  --lab-accent: {active_theme['accent']};",
        "",
        "  /* Kolory Pokoi LAB 3.0 (Prawo Rdzenia) */",
    ]

    for room in sorted(config.rooms.values(), key=lambda r: r.code):
        lines.append(f"  --lab-room-{room.code}-color: {room.color};")

    lines.extend([
        "}",
        "",
    ])

    if theme_mode == "light":
        lines.extend(_render_theme_block("all", theme_light))
    elif theme_mode == "dark":
        lines.extend(_render_theme_block("all", theme_dark))
    else:
        # Tryb systemowy ("system"): rozdzielne bloki dla trybu jasnego i ciemnego
        lines.append("/* 1. Motyw Jasny (Aktywowany automatycznie przez macOS / Obsidian theme-light) */")
        lines.extend(_render_theme_block("theme-light", theme_light))
        lines.append("/* 2. Motyw Ciemny (Aktywowany automatycznie przez macOS / Obsidian theme-dark) */")
        lines.extend(_render_theme_block("theme-dark", theme_dark))

    lines.extend([
        "/* 3. Kolory Pokoi LAB (Prawo Rdzenia - dynamiczne z konfiguracji) */",
    ])

    for room in sorted(config.rooms.values(), key=lambda r: r.code):
        lines.extend([
            f'.nav-folder-title[data-path^="{room.path}"] .nav-folder-title-content,',
            f'.nav-folder-title:not([data-path*="/"])[data-path^="{room.path}"] .nav-folder-title-content,',
            f'.nav-folder-title:not([data-path*="/"])[data-path^="{room.code} "] .nav-folder-title-content,',
            f'.nav-folder-title[data-path="{room.path}"] .nav-folder-title-content {{',
            f'  color: {room.color} !important;',
            '  font-weight: 600;',
            '}',
            f'.nav-folder-title:not([data-path*="/"])[data-path^="{room.path}"] .nav-folder-collapse-indicator,',
            f'.nav-folder-title:not([data-path*="/"])[data-path^="{room.code} "] .nav-folder-collapse-indicator,',
            f'.nav-folder-title[data-path="{room.path}"] .nav-folder-collapse-indicator {{',
            f'  color: {room.color} !important;',
            '}',
            '',
        ])

    lines.extend([
        "/* 4. Higiena Panelu Bocznego Obsidiana: Ukrycie plików i folderów technicznych (biała lista root) */",
        '.nav-folder:has(> .nav-folder-title[data-path="__pycache__"]),',
        '.nav-folder:has(> .nav-folder-title[data-path^="__pycache__"]),',
        '.nav-folder-title[data-path="__pycache__"],',
        '.nav-folder-title[data-path^="__pycache__"],',
        '.nav-folder-title[data-path="__pycache__"] + .nav-folder-children,',
        '.nav-folder-title[data-path^="__pycache__"] + .nav-folder-children,',
        '.nav-folder:has(> .nav-folder-title[data-path=".pytest_cache"]),',
        '.nav-folder-title[data-path=".pytest_cache"],',
        '.nav-folder-title[data-path=".pytest_cache"] + .nav-folder-children,',
        '.nav-files-container > div > .nav-file:has(> .nav-file-title:not([data-path="AGENTS.md"])),',
        '.nav-file:has(> .nav-file-title:not([data-path*="/"]):not([data-path="AGENTS.md"])),',
        '.nav-file-title:not([data-path*="/"]):not([data-path="AGENTS.md"]) {',
        "  display: none !important;",
        "}",
        "",
        "/* ==========================================================================",
        "   5. REGUŁY DRUKU I EKSPORTU DO PDF (@media print)",
        "   Gwarantuje perfekcyjną czystość i elegancję na papierze i w plikach PDF:",
        "   - Czyste tło, głęboka czerń tekstu (#0f172a / #1e293b)",
        "   - Tytuł H1 i H2 bez sztucznych belek i podkreśleń (styl czytnika Obsidiana)",
        "   - Karta cytatu/źródła pod tytułem z lewą belką akcentu i subtelnym tłem",
        "   - Delikatny separator hr (#e2e8f0)",
        "   - Równe wcięcia i pionowe odstępy dla list numerowanych (ol) i punktowanych (ul)",
        "   - Tabele w układzie zebra i czytelne callouty",
        "   - Ochrona przed łamaniem stron wewnątrz sekcji, tabel i diagramów",
        "   ========================================================================== */",
        "@media print {",
        "  :root,",
        "  body,",
        "  body.theme-dark,",
        "  body.theme-light,",
        "  body.theme-dark .workspace-leaf-content,",
        "  body.theme-dark .view-content,",
        "  body.theme-dark .markdown-source-view,",
        "  body.theme-dark .markdown-rendered,",
        "  body.theme-dark .cm-scroller,",
        "  body.theme-light .workspace-leaf-content,",
        "  body.theme-light .view-content,",
        "  body.theme-light .markdown-source-view,",
        "  body.theme-light .markdown-rendered,",
        "  body.theme-light .cm-scroller,",
        "  .theme-dark .workspace-leaf-content,",
        "  .theme-dark .view-content,",
        "  .theme-dark .markdown-rendered,",
        "  .theme-light .workspace-leaf-content,",
        "  .theme-light .view-content,",
        "  .theme-light .markdown-rendered,",
        "  .markdown-rendered,",
        "  .print {",
        "    --background-primary: #ffffff !important;",
        "    --background-primary-alt: #f8fafc !important;",
        "    --background-secondary: #f1f5f9 !important;",
        "    --text-normal: #0f172a !important;",
        "    --text-muted: #64748b !important;",
        f"    --text-accent: {active_theme['accent']} !important;",
        "    background: #ffffff !important;",
        "    background-color: #ffffff !important;",
        "    color: #0f172a !important;",
        f"    font-family: {font_sans} !important;",
        "    font-size: 11pt !important;",
        "    line-height: 1.6 !important;",
        "  }",
        "",
        "  /* Wyłączenie ozdobników ekranowych */",
        "  .metadata-container,",
        "  .metadata-properties-heading,",
        "  .mod-header,",
        "  .workspace-ribbon,",
        "  .status-bar,",
        "  .side-dock-ribbon,",
        "  .view-header {",
        "    display: none !important;",
        "  }",
        "",
        "  /* Akapity i typografia podstawowa */",
        "  body.theme-dark p, body.theme-light p, .theme-dark p, .theme-light p, p {",
        "    color: #1e293b !important;",
        "    margin-top: 0.4em !important;",
        "    margin-bottom: 0.8em !important;",
        "    line-height: 1.6 !important;",
        "  }",
        "",
        "  /* Nagłówki dla druku - szlachetna typografia bez kresek pod spodem */",
        "  body.theme-dark .inline-title, body.theme-light .inline-title,",
        "  body.theme-dark .markdown-rendered h1, body.theme-light .markdown-rendered h1,",
        "  body.theme-dark .cm-header-1, body.theme-light .cm-header-1,",
        "  body.theme-dark h1, body.theme-light h1,",
        "  .inline-title, .markdown-rendered h1, .cm-header-1, h1 {",
        "    color: #0f172a !important;",
        "    font-size: 20pt !important;",
        "    font-weight: 800 !important;",
        "    border-bottom: none !important;",
        "    padding-bottom: 0 !important;",
        "    margin-top: 0.8em !important;",
        "    margin-bottom: 0.6em !important;",
        "    page-break-after: avoid !important;",
        "    break-after: avoid !important;",
        "  }",
        "",
        "  body.theme-dark .markdown-rendered h2, body.theme-light .markdown-rendered h2,",
        "  body.theme-dark .cm-header-2, body.theme-light .cm-header-2,",
        "  body.theme-dark h2, body.theme-light h2,",
        "  .markdown-rendered h2, .cm-header-2, h2 {",
        "    color: #0f172a !important;",
        "    font-size: 14.5pt !important;",
        "    font-weight: 700 !important;",
        "    border-bottom: none !important;",
        "    padding-bottom: 0 !important;",
        "    margin-top: 1.8em !important;",
        "    margin-bottom: 0.5em !important;",
        "    page-break-after: avoid !important;",
        "    break-after: avoid !important;",
        "  }",
        "",
        "  body.theme-dark .markdown-rendered h3, body.theme-light .markdown-rendered h3,",
        "  body.theme-dark .cm-header-3, body.theme-light .cm-header-3,",
        "  body.theme-dark h3, body.theme-light h3,",
        "  .markdown-rendered h3, .cm-header-3, h3 {",
        "    color: #1e293b !important;",
        "    font-size: 12pt !important;",
        "    font-weight: 700 !important;",
        "    margin-top: 1.4em !important;",
        "    margin-bottom: 0.4em !important;",
        "    page-break-after: avoid !important;",
        "    break-after: avoid !important;",
        "  }",
        "",
        "  body.theme-dark .markdown-rendered h4, body.theme-light .markdown-rendered h4,",
        "  body.theme-dark .cm-header-4, body.theme-light .cm-header-4,",
        "  body.theme-dark h4, body.theme-light h4,",
        "  .markdown-rendered h4, .cm-header-4, h4 {",
        "    color: #334155 !important;",
        "    font-size: 11pt !important;",
        "    font-weight: 600 !important;",
        "    page-break-after: avoid !important;",
        "    break-after: avoid !important;",
        "  }",
        "",
        "  /* Karta źródła / cytatu pod nagłówkiem (zgodna z widokiem Obsidiana) */",
        "  blockquote {",
        "    background-color: #f8fafc !important;",
        "    border: none !important;",
        f"    border-left: 4px solid {active_theme['accent']} !important;",
        "    border-radius: 4px !important;",
        "    padding: 10px 16px !important;",
        "    margin: 1.2em 0 !important;",
        "    color: #334155 !important;",
        "    font-style: normal !important;",
        "    page-break-inside: avoid !important;",
        "    break-inside: avoid !important;",
        "  }",
        "",
        "  blockquote p {",
        "    margin: 0.3em 0 !important;",
        "    color: #334155 !important;",
        "  }",
        "",
        "  /* Subtelny separator hr */",
        "  hr {",
        "    border: none !important;",
        "    border-top: 1px solid #e2e8f0 !important;",
        "    margin: 24px 0 !important;",
        "    height: 0 !important;",
        "  }",
        "",
        "  /* Listy numerowane i punktowane - równe wcięcia i pionowe odstępy */",
        "  ol, ul {",
        "    margin-top: 0.6em !important;",
        "    margin-bottom: 0.8em !important;",
        "    padding-left: 1.8em !important;",
        "  }",
        "",
        "  li {",
        "    margin-bottom: 0.6em !important;",
        "    line-height: 1.55 !important;",
        "    color: #1e293b !important;",
        "  }",
        "",
        "  li > p {",
        "    margin: 0 !important;",
        "  }",
        "",
        "  /* Callouty w druku */",
        "  .callout {",
        "    background-color: #f8fafc !important;",
        "    border: 1px solid #cbd5e1 !important;",
        f"    border-left: 4px solid {active_theme['accent']} !important;",
        "    border-radius: 6px !important;",
        "    padding: 10px 14px !important;",
        "    margin: 1.2em 0 !important;",
        "    page-break-inside: avoid !important;",
        "    break-inside: avoid !important;",
        "  }",
        "",
        '  .callout[data-callout="important"], .callout[data-callout="caution"] {',
        "    background-color: #fffbeb !important;",
        "    border-left-color: #d97706 !important;",
        "  }",
        "",
        '  .callout[data-callout="warning"] {',
        "    background-color: #fef2f2 !important;",
        "    border-left-color: #dc2626 !important;",
        "  }",
        "",
        "  .callout-title {",
        "    color: #0f172a !important;",
        "    font-weight: 700 !important;",
        "    font-size: 11pt !important;",
        "  }",
        "",
        "  .callout-content {",
        "    color: #1e293b !important;",
        "    font-size: 10.5pt !important;",
        "    line-height: 1.5 !important;",
        "  }",
        "",
        "  /* Tabele w druku - układ zebra i szary nagłówek */",
        "  table {",
        "    border-collapse: collapse !important;",
        "    width: 100% !important;",
        "    margin: 1.4em 0 !important;",
        "    page-break-inside: avoid !important;",
        "    break-inside: avoid !important;",
        "  }",
        "",
        "  th {",
        "    background-color: #f8fafc !important;",
        "    color: #0f172a !important;",
        "    font-weight: 700 !important;",
        "    border: 1px solid #cbd5e1 !important;",
        "    padding: 8px 12px !important;",
        "    text-align: left !important;",
        "  }",
        "",
        "  td {",
        "    border: 1px solid #e2e8f0 !important;",
        "    padding: 8px 12px !important;",
        "    color: #1e293b !important;",
        "    vertical-align: top !important;",
        "  }",
        "",
        "  tr:nth-child(even) td {",
        "    background-color: #f8fafc !important;",
        "  }",
        "",
        "  /* Kod i diagramy */",
        "  code {",
        f"    font-family: {font_mono} !important;",
        "    font-size: 9.5pt !important;",
        "    background-color: #f1f5f9 !important;",
        "    color: #0f172a !important;",
        "    padding: 2px 5px !important;",
        "    border-radius: 4px !important;",
        "    border: 1px solid #e2e8f0 !important;",
        "  }",
        "",
        "  pre, .code-block {",
        "    background-color: #0f172a !important;",
        "    color: #f8fafc !important;",
        "    padding: 10px 14px !important;",
        "    border-radius: 6px !important;",
        "    page-break-inside: avoid !important;",
        "    break-inside: avoid !important;",
        "  }",
        "",
        "  pre code, .code-block code {",
        "    background: transparent !important;",
        "    color: inherit !important;",
        "    border: none !important;",
        "    padding: 0 !important;",
        "  }",
        "",
        "  img, svg, .mermaid {",
        "    max-width: 100% !important;",
        "    height: auto !important;",
        "    page-break-inside: avoid !important;",
        "    break-inside: avoid !important;",
        "    filter: none !important;",
        "  }",
        "}",
        "",
    ])

    return "\n".join(lines)


# --- CLI Przełącznika Motywów (lab_core.themes) ---
def list_themes() -> None:
    print("=" * 65)
    print("🎨 DOSTĘPNE MOTYWY DESIGN SYSTEMU LAB (Framework PZW):")
    print("=" * 65)
    print("🌙 MOTYWY CIEMNE (DARK):")
    for key, data in THEME_PRESETS.items():
        if data.get("mode", "dark") == "dark" and key != "evergreen":
            print(f"  • {key:18} -> {data['name']:22} (Akcent: {data['accent']})")
    print("\n☀️ MOTYWY JASNE (LIGHT):")
    for key, data in THEME_PRESETS.items():
        if data.get("mode") == "light":
            print(f"  • {key:18} -> {data['name']:22} (Akcent: {data['accent']})")
    print("=" * 65)


def set_theme(theme_key: str, vault_root: Optional[Path] = None) -> int:
    theme_key = theme_key.strip().lower()
    if theme_key not in THEME_PRESETS:
        print(f"❌ Nieznany motyw: '{theme_key}'.", file=sys.stderr)
        list_themes()
        return 1

    from lab_core.env import find_vault_root
    v_root = Path(vault_root).resolve() if vault_root else find_vault_root(Path(__file__).resolve().parent)
    config_file = v_root / "lab.config.toml"
    if not config_file.is_file():
        print(f"❌ Nie znaleziono pliku konfiguracji: {config_file}", file=sys.stderr)
        return 2

    content = config_file.read_text(encoding="utf-8")
    content = re.sub(
        r'theme_preset\s*=\s*"[^"]*"',
        f'theme_preset = "{theme_key}"',
        content,
    )

    theme_info = THEME_PRESETS[theme_key]
    slot_name = "theme_preset_light" if theme_info.get("mode") == "light" else "theme_preset_dark"
    if re.search(rf'{slot_name}\s*=\s*"[^"]*"', content):
        content = re.sub(rf'{slot_name}\s*=\s*"[^"]*"', f'{slot_name} = "{theme_key}"', content)
    else:
        content = content.replace(
            f'theme_preset = "{theme_key}"',
            f'theme_preset = "{theme_key}"\n{slot_name} = "{theme_key}"',
        )

    if "theme_mode = " not in content:
        content = content.replace(
            f'theme_preset = "{theme_key}"',
            f'theme_preset = "{theme_key}"\ntheme_mode = "system"',
        )

    config_file.write_text(content, encoding="utf-8")

    from core_config import ConfigManager
    config = ConfigManager.load(config_path=config_file, vault_root=v_root)
    css_content = generate_design_tokens_css(config)

    snippets_dir = v_root / ".obsidian" / "snippets"
    snippets_dir.mkdir(parents=True, exist_ok=True)
    css_file = snippets_dir / "lab-design-tokens.css"
    css_file.write_text(css_content, encoding="utf-8")

    mode_icon = "☀️" if theme_info.get("mode") == "light" else "🌙"
    print(f"✅ Aktywowano motyw {mode_icon} '{theme_info['name']}' ({theme_key}).")
    print(f"   • Zapisano konfigurację: {config_file.relative_to(v_root)}")
    print(f"   • Zaktualizowano tokeny CSS: {css_file.relative_to(v_root)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dynamiczny Przełącznik Motywów UI dla LAB (Design Tokens CSS)."
    )
    parser.add_argument("theme", nargs="?", help="Identyfikator motywu (np. dawn, tokyonight)")
    parser.add_argument("--list", action="store_true", help="Wyświetla listę wszystkich dostępnych motywów")
    parser.add_argument("--vault-root", type=Path, help="Opcjonalna ścieżka do skarbca LAB")
    args = parser.parse_args()

    if args.list or not args.theme:
        list_themes()
        return 0

    return set_theme(args.theme, vault_root=args.vault_root)


if __name__ == "__main__":
    import sys
    sys.exit(main())
