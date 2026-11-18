"""lab_core.metadata - Deterministyczny ekstraktor metadanych i szablonów SAT dla URL/Wideo
Standard: 100% Python Standard Library. Zero-Pip.
Obsługuje:
1. YouTube (oEmbed API + parser HTML dla długości i opisu).
2. Strony Web (nagłówki OpenGraph, meta tags, tytuły).
3. Deterministyczne generowanie szkieletu notatki referencyjnej SAT.
"""

from __future__ import annotations
import sys
import argparse

import datetime
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from .config import ConfigError, ConfigManager
from .env import find_vault_root

sys_timeout = 8
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

YOUTUBE_DOMAINS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
URL_REGEX = re.compile(r'https?://[^\s)\]>"\']+', re.IGNORECASE)


def extract_urls(text: str) -> List[str]:
    """Wydobywa listę unikalnych adresów URL http/https z tekstu Markdown, oczyszczając interpunkcję."""
    if not text:
        return []
    matches = URL_REGEX.findall(text)
    urls: List[str] = []
    for m in matches:
        cleaned = m.rstrip(".,;:!?)>]}\"'")
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
    return urls


def extract_youtube_video_id(url: str) -> Optional[str]:
    """Ekstrahuje 11-znakowy identyfikator wideo YouTube z różnych formatów URL."""
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in YOUTUBE_DOMAINS:
        return None

    path = parsed.path
    if hostname == "youtu.be":
        # np. https://youtu.be/JOJ5zihcd6Q
        parts = [p for p in path.split("/") if p]
        return parts[0] if parts else None

    if "/shorts/" in path:
        # np. https://www.youtube.com/shorts/JOJ5zihcd6Q
        parts = [p for p in path.split("/") if p]
        idx = parts.index("shorts") if "shorts" in parts else -1
        if idx != -1 and idx + 1 < len(parts):
            return parts[idx + 1]

    # np. https://www.youtube.com/watch?v=JOJ5zihcd6Q
    qs = urllib.parse.parse_qs(parsed.query)
    v_param = qs.get("v")
    if v_param:
        return v_param[0]

    return None


class SimpleHTMLMetaParser(HTMLParser):
    """Lekki parser HTML do wyciągania meta-tagów i tytułu strony bez zależności bs4."""

    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: List[str] = []
        self.meta_tags: Dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            attr_dict = {k.lower(): (v or "") for k, v in attrs}
            name = attr_dict.get("name") or attr_dict.get("property") or attr_dict.get("http-equiv")
            content = attr_dict.get("content", "")
            if name:
                self.meta_tags[name.lower()] = content.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    def get_title(self) -> str:
        return html.unescape("".join(self.title_parts)).strip()


def _decode_json_string_fragment(raw_fragment: str) -> str:
    """Dekoduje wycinek łańcucha JSON z HTML bez korupcji znaków UTF-8 (mojibake)."""
    try:
        return str(json.loads(f'"{raw_fragment}"'))
    except Exception:
        return raw_fragment.replace("\\n", "\n").replace('\\"', '"')


def _format_timestamp(seconds: float) -> str:
    """Formatuje czas w sekundach do postaci [mm:ss] lub [hh:mm:ss]."""
    total = max(0, int(seconds))
    mins, sec = divmod(total, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"[{hrs:02d}:{mins:02d}:{sec:02d}]"
    return f"[{mins:02d}:{sec:02d}]"


def _parse_timedtext_xml(xml_text: str) -> str:
    """Parsuje XML napisów YouTube (<transcript>/<text> lub <timedtext>/<p>) do czystego tekstu ze znacznikami czasu."""
    if not xml_text or not xml_text.strip():
        return ""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""

    lines: List[str] = []
    last_ts = -60.0

    # Format 1: <transcript><text start="0.0" dur="3.5">...</text></transcript>
    text_nodes = root.findall(".//text")
    if text_nodes:
        for node in text_nodes:
            raw = html.unescape("".join(node.itertext())).replace("\n", " ").strip()
            raw = re.sub(r"\s+", " ", raw)
            if not raw:
                continue
            try:
                start = float(node.attrib.get("start", "0"))
            except ValueError:
                start = 0.0
            if start - last_ts >= 30.0 or not lines:
                lines.append(f"{_format_timestamp(start)} {raw}")
                last_ts = start
            else:
                lines[-1] = f"{lines[-1]} {raw}"
        return "\n\n".join(lines).strip()

    # Format 2: <timedtext format="3"><body><p t="0" d="3500">...</p></body></timedtext>
    p_nodes = root.findall(".//p")
    for node in p_nodes:
        raw = html.unescape("".join(node.itertext())).replace("\n", " ").strip()
        raw = re.sub(r"\s+", " ", raw)
        if not raw:
            continue
        try:
            start = float(node.attrib.get("t", "0")) / 1000.0
        except ValueError:
            start = 0.0
        if start - last_ts >= 30.0 or not lines:
            lines.append(f"{_format_timestamp(start)} {raw}")
            last_ts = start
        else:
            lines[-1] = f"{lines[-1]} {raw}"
    return "\n\n".join(lines).strip()


def _select_caption_track(tracks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Wybiera najlepszą ścieżkę napisów (preferencja: pl -> en -> pierwsza; ręczne przed ASR)."""
    if not tracks:
        return None

    def _score(track: Dict[str, Any]) -> Tuple[int, int]:
        lang = str(track.get("languageCode", "")).lower()
        is_asr = 1 if track.get("kind") == "asr" else 0
        if lang.startswith("pl"):
            lang_rank = 0
        elif lang.startswith("en"):
            lang_rank = 1
        else:
            lang_rank = 2
        return (lang_rank, is_asr)

    valid = [t for t in tracks if isinstance(t, dict) and t.get("baseUrl")]
    if not valid:
        return None
    valid.sort(key=_score)
    return valid[0]


def _fetch_youtube_transcript_innertube(
    video_id: Optional[str],
    page_text: str,
    timeout: int = sys_timeout,
    errors: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Pobiera transkrypcję przez InnerTube player API (klient ANDROID) w czystym stdlib."""
    if not video_id or not page_text:
        return "", ""

    m_key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', page_text)
    if not m_key:
        if errors is not None:
            errors.append("innertube-android: brak INNERTUBE_API_KEY w HTML")
        return "", ""

    api_key = m_key.group(1)
    payload = json.dumps({
        "context": {
            "client": {
                "clientName": "ANDROID",
                "clientVersion": "20.10.38",
            }
        },
        "videoId": video_id,
    }).encode("utf-8")
    android_ua = "com.google.android.youtube/20.10.38 (Linux; U; Android 14) gzip"
    req = urllib.request.Request(
        f"https://www.youtube.com/youtubei/v1/player?key={api_key}",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": android_ua},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            pdata = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as exc:
        if errors is not None:
            errors.append(f"innertube-android player error: {exc}")
        return "", ""

    tracks = (
        pdata.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    chosen = _select_caption_track(tracks) if isinstance(tracks, list) else None
    if not chosen:
        if errors is not None:
            errors.append("innertube-android: brak ścieżek napisów w odpowiedzi API")
        return "", ""

    base_url = str(chosen.get("baseUrl", "")).replace("\\u0026", "&")
    if not base_url:
        if errors is not None:
            errors.append("innertube-android: pusty baseUrl")
        return "", ""

    try:
        req_tt = urllib.request.Request(base_url, headers={"User-Agent": DEFAULT_USER_AGENT})
        with urllib.request.urlopen(req_tt, timeout=timeout) as resp:
            xml_text = resp.read().decode("utf-8", errors="ignore")
        if not xml_text.strip():
            if errors is not None:
                errors.append("innertube-android: pusty timedtext (0 B)")
            return "", ""
        transcript = _parse_timedtext_xml(xml_text)
        if transcript:
            lang = chosen.get("languageCode", "unknown")
            return transcript, f"youtube-innertube ({lang})"
        if errors is not None:
            errors.append("innertube-android: nie udało się sparsować XML napisów")
    except Exception as exc:
        if errors is not None:
            errors.append(f"innertube-android timedtext error: {exc}")
    return "", ""


def _fetch_youtube_transcript_from_html(
    page_text: str,
    timeout: int = sys_timeout,
    errors: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Wyciąga captionTracks z HTML strony YouTube i pobiera transkrypcję w stdlib."""
    marker = '"captionTracks":'
    idx = page_text.find(marker)
    if idx == -1:
        if errors is not None:
            errors.append("html-captionTracks: brak markeru captionTracks")
        return "", ""

    arr_start = page_text.find("[", idx + len(marker))
    if arr_start == -1:
        if errors is not None:
            errors.append("html-captionTracks: brak tablicy JSON")
        return "", ""

    try:
        tracks, _ = json.JSONDecoder().raw_decode(page_text, arr_start)
    except Exception as exc:
        if errors is not None:
            errors.append(f"html-captionTracks JSON error: {exc}")
        return "", ""

    if not isinstance(tracks, list):
        return "", ""

    chosen = _select_caption_track(tracks)
    if not chosen:
        if errors is not None:
            errors.append("html-captionTracks: brak poprawnej ścieżki napisów")
        return "", ""

    base_url = str(chosen.get("baseUrl", "")).replace("\\u0026", "&")
    if not base_url:
        return "", ""

    try:
        req = urllib.request.Request(base_url, headers={"User-Agent": DEFAULT_USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_text = resp.read().decode("utf-8", errors="ignore")
        if not xml_text.strip():
            if errors is not None:
                errors.append("html-captionTracks: pusty timedtext (0 B)")
            return "", ""
        transcript = _parse_timedtext_xml(xml_text)
        if transcript:
            lang = chosen.get("languageCode", "unknown")
            return transcript, f"youtube-timedtext ({lang})"
        if errors is not None:
            errors.append("html-captionTracks: błąd parsowania XML napisów")
    except Exception as exc:
        if errors is not None:
            errors.append(f"html-captionTracks timedtext error: {exc}")
    return "", ""


def _parse_vtt_text(vtt_text: str) -> str:
    """Konwertuje napisy WebVTT na czysty tekst bez duplikatów."""
    lines: List[str] = []
    seen = set()
    for raw_line in vtt_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if "-->" in line or line.isdigit():
            continue
        clean = re.sub(r"<[^>]+>", "", line)
        clean = html.unescape(clean).strip()
        if clean and clean not in seen:
            seen.add(clean)
            lines.append(clean)
    return " ".join(lines).strip()


def _fetch_youtube_transcript_ytdlp(
    video_url: str,
    timeout: int = 15,
    errors: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Opcjonalny fallback pobierania napisów przez yt-dlp (jeśli obecny w PATH lub /opt/homebrew/bin)."""
    ytdlp_bin = shutil.which("yt-dlp")
    if not ytdlp_bin:
        hb_bin = Path("/opt/homebrew/bin/yt-dlp")
        if hb_bin.exists():
            ytdlp_bin = str(hb_bin)
    if not ytdlp_bin:
        if errors is not None:
            errors.append("yt-dlp: brak binarki w PATH ani /opt/homebrew/bin")
        return "", ""

    env = os.environ.copy()
    ssl_cert = env.get("SSL_CERT_FILE")
    if ssl_cert and Path(ssl_cert).exists():
        env.setdefault("REQUESTS_CA_BUNDLE", ssl_cert)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                ytdlp_bin,
                "--skip-download",
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs",
                "pl,en",
                "--sub-format",
                "vtt",
                "-o",
                str(Path(tmpdir) / "%(id)s.%(ext)s"),
                video_url,
            ]
            proc = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True, check=False, env=env)
            vtt_files = sorted(Path(tmpdir).glob("*.vtt"))
            if not vtt_files:
                if errors is not None:
                    err_msg = (proc.stderr or "").strip().splitlines()
                    tail = err_msg[-1] if err_msg else f"rc={proc.returncode}, brak plików .vtt"
                    errors.append(f"yt-dlp: {tail}")
                return "", ""
            # Preferuj pl przed en
            pl_files = [f for f in vtt_files if ".pl." in f.name]
            target_vtt = pl_files[0] if pl_files else vtt_files[0]
            text = _parse_vtt_text(target_vtt.read_text(encoding="utf-8", errors="ignore"))
            if text:
                return text, f"yt-dlp ({target_vtt.name})"
            if errors is not None:
                errors.append("yt-dlp: plik .vtt pusty po parsowaniu")
    except Exception as exc:
        if errors is not None:
            errors.append(f"yt-dlp error: {exc}")
    return "", ""


def fetch_youtube_metadata(url: str, timeout: int = sys_timeout) -> Dict[str, Any]:
    """Pobiera twarde, oficjalne metadane wideo z YouTube (oEmbed + HTML scraper + transkrypcja)."""
    vid = extract_youtube_video_id(url)
    clean_url = f"https://www.youtube.com/watch?v={vid}" if vid else url

    res: Dict[str, Any] = {
        "url": clean_url,
        "video_id": vid,
        "is_youtube": True,
        "title": "",
        "author": "",
        "author_url": "",
        "provider": "YouTube",
        "duration_seconds": None,
        "duration_formatted": "",
        "description": "",
        "transcript": "",
        "transcript_source": "",
        "transcript_error": "",
        "thumbnail_url": "",
        "raw_source": "youtube",
    }
    transcript_errors: List[str] = []

    # 1. Oficjalne oEmbed API YouTube (szybkie i deterministyczne)
    oembed_endpoint = f"https://www.youtube.com/oembed?url={urllib.parse.quote(clean_url)}&format=json"
    req_oembed = urllib.request.Request(oembed_endpoint, headers={"User-Agent": DEFAULT_USER_AGENT})
    try:
        with urllib.request.urlopen(req_oembed, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            res["title"] = data.get("title", "").strip()
            res["author"] = data.get("author_name", "").strip()
            res["author_url"] = data.get("author_url", "").strip()
            res["thumbnail_url"] = data.get("thumbnail_url", "").strip()
    except Exception:
        pass

    # 2. Pobranie strony wideo dla dokładnego czasu trwania, opisu i transkrypcji
    req_page = urllib.request.Request(clean_url, headers={"User-Agent": DEFAULT_USER_AGENT})
    page_text = ""
    try:
        with urllib.request.urlopen(req_page, timeout=timeout) as resp:
            page_text = resp.read().decode("utf-8", errors="ignore")

            # Czas trwania w ms
            m_dur = re.search(r'"approxDurationMs":"(\d+)"', page_text)
            if m_dur:
                ms = int(m_dur.group(1))
                secs = ms // 1000
                res["duration_seconds"] = secs
                mins, s = divmod(secs, 60)
                if mins >= 60:
                    hrs, m = divmod(mins, 60)
                    res["duration_formatted"] = f"{hrs}h {m}m {s}s"
                else:
                    res["duration_formatted"] = f"{mins}m {s}s"

            # Krótki opis (bezpieczne dekodowanie UTF-8 JSON bez mojibake)
            m_desc = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', page_text)
            if m_desc:
                res["description"] = _decode_json_string_fragment(m_desc.group(1)).strip()

            # Fallback dla tytułu i autora jeśli oEmbed nie zadziałał
            if not res["title"]:
                parser = SimpleHTMLMetaParser()
                parser.feed(page_text)
                res["title"] = parser.meta_tags.get("og:title") or parser.get_title()
            if not res["author"]:
                m_owner = re.search(r'"ownerChannelName":"((?:[^"\\]|\\.)*)"', page_text)
                if m_owner:
                    res["author"] = _decode_json_string_fragment(m_owner.group(1)).strip()

            # 3. Transkrypcja: najpierw InnerTube ANDROID API (stdlib), potem captionTracks z HTML
            transcript, t_src = _fetch_youtube_transcript_innertube(
                vid, page_text, timeout=timeout, errors=transcript_errors
            )
            if not transcript:
                transcript, t_src = _fetch_youtube_transcript_from_html(
                    page_text, timeout=timeout, errors=transcript_errors
                )
            if transcript:
                res["transcript"] = transcript
                res["transcript_source"] = t_src
    except Exception as exc:
        transcript_errors.append(f"youtube-page error: {exc}")

    # 4. Opcjonalny fallback yt-dlp jeśli InnerTube/HTML nie dostarczyły napisów
    if not res["transcript"]:
        transcript, t_src = _fetch_youtube_transcript_ytdlp(clean_url, errors=transcript_errors)
        if transcript:
            res["transcript"] = transcript
            res["transcript_source"] = t_src

    if not res["transcript"] and transcript_errors:
        res["transcript_error"] = " | ".join(transcript_errors)

    return res


def fetch_web_metadata(url: str, timeout: int = sys_timeout) -> Dict[str, Any]:
    """Pobiera metadane ze standardowej strony www (OpenGraph, meta tags)."""
    res: Dict[str, Any] = {
        "url": url,
        "is_youtube": False,
        "title": "",
        "author": "",
        "published_date": "",
        "description": "",
        "provider": "",
        "raw_source": "web",
    }

    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "").lower()
            if "html" not in content_type and "text" not in content_type:
                return res

            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].strip()

            raw_bytes = resp.read()
            html_text = raw_bytes.decode(charset, errors="ignore")

            parser = SimpleHTMLMetaParser()
            parser.feed(html_text)

            meta = parser.meta_tags
            res["title"] = (
                meta.get("og:title")
                or meta.get("twitter:title")
                or parser.get_title()
                or ""
            ).strip()

            res["author"] = (
                meta.get("author")
                or meta.get("article:author")
                or meta.get("og:article:author")
                or meta.get("twitter:creator")
                or ""
            ).strip()

            res["description"] = (
                meta.get("og:description")
                or meta.get("description")
                or meta.get("twitter:description")
                or ""
            ).strip()

            res["provider"] = (
                meta.get("og:site_name")
                or urllib.parse.urlparse(url).netloc
            ).strip()

            res["published_date"] = (
                meta.get("article:published_time")
                or meta.get("og:article:published_time")
                or meta.get("date")
                or ""
            ).strip()
    except Exception as e:
        res["error"] = str(e)

    return res


def fetch_resource_metadata(url: str, timeout: int = sys_timeout) -> Dict[str, Any]:
    """Główny dyspozytor pobierania metadanych zasobu zewnętrznego."""
    clean_url = url.strip()
    if extract_youtube_video_id(clean_url):
        return fetch_youtube_metadata(clean_url, timeout=timeout)
    return fetch_web_metadata(clean_url, timeout=timeout)


def sanitize_filename(title: str, max_len: int = 100) -> str:
    """Tworzy bezpieczną nazwę pliku Markdown bez znaków zabronionych w systemach plików."""
    clean = re.sub(r'[\\/*?:"<>|]', " ", title)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > max_len:
        clean = clean[:max_len].rsplit(" ", 1)[0]
    return clean or "Zasob_Referencyjny"


def get_reference_template_path(is_youtube: bool, vault_root: Optional[Path] = None) -> Path:
    """Zwraca ścieżkę do kanonicznego szablonu notatki referencyjnej SAT (SSOT)."""
    tpl_name = "Szablon Notatki Wideo SAT.md" if is_youtube else "Szablon Notatki Artykuł SAT.md"
    try:
        root = vault_root or find_vault_root()
        cfg = ConfigManager(vault_root=root)
        gov_room = cfg.get_room_by_role("governance-and-engine")
        candidate = root / gov_room.path / "Szablony" / tpl_name
        if candidate.exists():
            return candidate
    except Exception:
        pass
    return Path(__file__).resolve().parents[2] / "Szablony" / tpl_name


def extract_template_sections(template_path: Path) -> List[str]:
    """Odczytuje kanoniczną listę nagłówków H2 (## ...) bezpośrednio z pliku szablonu."""
    if not template_path.exists():
        return []
    text = template_path.read_text(encoding="utf-8", errors="ignore")
    sections: List[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+)$", line.strip())
        if m:
            heading = m.group(1).strip()
            if not heading.startswith("{{"):
                sections.append(heading)
    return sections


def generate_reference_note_content(
    metadata: Dict[str, Any],
    stage: str = "raw",
    body_text: Optional[str] = None,
    created_date: Optional[str] = None,
    vault_root: Optional[Path] = None,
    human_thought: Optional[str] = None,
) -> str:
    """
    Generuje notatkę referencyjną SAT na podstawie kanonicznego pliku szablonu (SSOT).
    Gwarantuje obecność twardo pobranych metadanych i poprawnych flag jakościowych.
    """
    today = created_date or datetime.date.today().isoformat()
    raw_title = metadata.get("title") or "Nowy Materiał Referencyjny"
    is_yt = bool(metadata.get("is_youtube", False))

    prefix = "Wideo – " if is_yt else ""
    full_title = f"{prefix}{raw_title}"

    source_url = metadata.get("url", "")
    author = metadata.get("author") or "Nieznany"
    duration = metadata.get("duration_formatted") or ""
    desc = (metadata.get("description") or "").strip()
    transcript = (metadata.get("transcript") or "").strip()
    transcript_source = (metadata.get("transcript_source") or "").strip()

    tag_list = ["zasoby", "reference"]
    if is_yt:
        tag_list.extend(["wideo", "youtube"])
    if stage == "raw":
        tag_list.append("do-opracowania")
    tags_yaml = "\n".join(f"  - {t}" for t in tag_list)

    duration_line = ""
    if duration:
        secs = metadata.get("duration_seconds", "")
        secs_str = f" ({secs} s)" if secs else ""
        duration_line = f"> - **Czas trwania:** ~{duration}{secs_str}\n"

    stage_status = (
        f"🟡 `stage: {stage}` (#do-opracowania — oczekuje na pełną destylację SAT)"
        if stage == "raw"
        else f"🟢 `stage: {stage}` (Pełna synteza SAT / Kanon Trójwarstwowy)"
    )

    tpl_path = get_reference_template_path(is_youtube=is_yt, vault_root=vault_root)
    tpl_text = tpl_path.read_text(encoding="utf-8")

    safe_full_title = full_title.replace("\\", "\\\\").replace('"', '\\"')
    safe_author = author.replace("\\", "\\\\").replace('"', '\\"')

    replacements = {
        "{{STAGE}}": stage,
        "{{FULL_TITLE_YAML}}": safe_full_title,
        "{{AUTHOR_YAML}}": safe_author,
        "{{FULL_TITLE}}": full_title,
        "{{RAW_TITLE}}": raw_title,
        "{{CREATED}}": today,
        "{{UPDATED}}": today,
        "{{SOURCE_URL}}": source_url,
        "{{AUTHOR}}": author,
        "{{TAGS_YAML}}": tags_yaml,
        "{{DURATION_LINE}}": duration_line,
        "{{STAGE_STATUS}}": stage_status,
    }

    if body_text and body_text.strip():
        # Bezpieczne oddzielenie nagłówka (YAML frontmatter + H1 + callout) przed pierwszą sekcją H2
        fm_match = re.match(r"^(---\r?\n.*?\r?\n---\r?\n)(.*)$", tpl_text, re.DOTALL)
        if fm_match:
            fm_part, after_fm = fm_match.group(1), fm_match.group(2)
            pre_h2 = re.split(r"\r?\n##\s+", after_fm, maxsplit=1)[0]
            header_part = fm_part + pre_h2.rstrip()
        else:
            header_part = re.split(r"\r?\n##\s+", tpl_text, maxsplit=1)[0].rstrip()

        rendered_header = header_part
        for key, val in replacements.items():
            rendered_header = rendered_header.replace(key, val)

        parts = [rendered_header, "", body_text.strip(), ""]
        if transcript and "## 🎙️ Transkrypcja" not in body_text:
            src_info = f" (*źródło: {transcript_source}*)" if transcript_source else ""
            parts.extend(["---", "", f"## 🎙️ Transkrypcja Źródłowa{src_info}", "", transcript, ""])
        return "\n".join(parts)

    section_thesis = metadata.get("section_thesis") or (
        desc if desc else "Brak opisu w metadanych źródłowych."
    )
    section_details = metadata.get("section_details") or (
        transcript[:1200] if transcript else (desc if desc else "Brak szczegółów w metadanych źródłowych.")
    )
    section_timeline = metadata.get("section_timeline") or (
        "- `[00:00]` Start materiału" if is_yt else ""
    )
    section_implications = metadata.get("section_implications") or (
        f"- Materiał źródłowy: {full_title} ({author})."
    )
    section_links = metadata.get("section_links") or (
        "- *(Powiązania tematyczne do uzupełnienia podczas syntezy SAT)*"
    )

    transcript_block = ""
    if transcript:
        src_info = f" (*źródło: {transcript_source}*)" if transcript_source else ""
        transcript_block = f"\n---\n\n## 🎙️ Transkrypcja Źródłowa{src_info}\n\n{transcript}\n"

    clipped_block = ""
    if metadata.get("clipped_content"):
        clipped_block = f"\n---\n\n## 📄 Oryginalna treść materiału (Clipped content)\n\n{metadata['clipped_content']}\n"

    replacements.update({
        "{{SECTION_THESIS}}": section_thesis,
        "{{SECTION_DETAILS}}": section_details,
        "{{SECTION_TIMELINE}}": section_timeline,
        "{{SECTION_IMPLICATIONS}}": section_implications,
        "{{SECTION_LINKS}}": section_links,
        "{{TRANSCRIPT_BLOCK}}": transcript_block,
        "{{CLIPPED_CONTENT_BLOCK}}": clipped_block,
    })

    rendered = tpl_text
    for key, val in replacements.items():
        rendered = rendered.replace(key, val)

    if human_thought and human_thought.strip():
        human_block = f"\n---\n\n## 🎙️ Notatka z dyktafonu / Refleksja Właściciela\n\n{human_thought.strip()}\n"
        links_match = re.search(r"(\r?\n---\r?\n\r?\n##\s+🔗)", rendered)
        if links_match:
            pos = links_match.start()
            rendered = rendered[:pos] + human_block + rendered[pos:]
        else:
            rendered = rendered.rstrip() + human_block

    return rendered





def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministyczny Ingest URL do Zasobów LAB 3.0"
    )
    parser.add_argument("url", help="Adres URL wideo YouTube lub artykułu WWW")
    parser.add_argument(
        "--vault-root",
        help="Katalog główny skarbca (domyślnie: autodetekcja)",
        default=None,
    )
    parser.add_argument(
        "--stage",
        choices=["raw", "distilled"],
        default="raw",
        help="Etap życiowy notatki (domyślnie: raw)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Tylko pobierz metadane i pokaż treść bez zapisu na dysku",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Zwróć wynik jako obiekt JSON",
    )

    args = parser.parse_args()

    # 1. Inicjalizacja konfiguracji skarbca
    vault_root = Path(args.vault_root).resolve() if args.vault_root else find_vault_root()
    cfg = ConfigManager(vault_root=vault_root)

    try:
        assets_room = cfg.get_room_by_role("reference-and-assets")
    except ConfigError:
        assets_room = cfg.get_room("06")

    notes_dir = vault_root / assets_room.path / "Notatki"
    notes_dir.mkdir(parents=True, exist_ok=True)

    # 2. Deterministyczne pobranie metadanych maszynowych
    meta = fetch_resource_metadata(args.url)
    if not meta.get("title"):
        msg = f"Nie udało się pobrać twardych metadanych dla URL: {args.url}"
        if args.json:
            print(json.dumps({"error": msg, "metadata": meta}, ensure_ascii=False))
        else:
            print(f"❌ {msg}", file=sys.stderr)
        return 1

    # 3. Generowanie zawartości notatki
    content = generate_reference_note_content(meta, stage=args.stage)

    # 4. Ustalenie nazwy pliku
    author_prefix = f"{meta['author']} – " if meta.get("author") and not meta.get("is_youtube") else ""
    raw_fname = f"{author_prefix}{meta.get('title')}"
    if meta.get("is_youtube"):
        author_suffix = f" ({meta['author']})" if meta.get("author") else ""
        raw_fname = f"Wideo – {meta.get('title')}{author_suffix}"

    safe_stem = sanitize_filename(raw_fname)
    target_path = notes_dir / f"{safe_stem}.md"

    # Obsługa kolizji nazw
    counter = 1
    while target_path.exists() and not args.dry_run:
        target_path = notes_dir / f"{safe_stem} {counter}.md"
        counter += 1

    if args.dry_run:
        if args.json:
            print(json.dumps({"target": str(target_path), "metadata": meta, "content": content}, ensure_ascii=False))
        else:
            print(f"[DRY-RUN] Docelowy plik: {target_path}")
            print(f"[DRY-RUN] Tytuł: {meta.get('title')} | Autor: {meta.get('author')}")
            print("--- Treść ---")
            print(content[:500] + "\n...")
        return 0

    target_path.write_text(content, encoding="utf-8")

    if args.json:
        payload_out = {
            "status": "success",
            "path": str(target_path.relative_to(vault_root)),
            "title": meta.get("title"),
            "author": meta.get("author"),
            "duration": meta.get("duration_formatted"),
            "stage": args.stage,
        }
        if meta.get("transcript_error") and not meta.get("transcript"):
            payload_out["transcript_error"] = meta.get("transcript_error")
        print(json.dumps(payload_out, ensure_ascii=False))
    else:
        print(f"✅ Pomyślnie zainicjowano notatkę referencyjną:")
        print(f"   Ścieżka: {target_path.relative_to(vault_root)}")
        print(f"   Tytuł:   {meta.get('title')}")
        print(f"   Autor:   {meta.get('author')}")
        if meta.get("duration_formatted"):
            print(f"   Czas:    {meta.get('duration_formatted')}")
        if meta.get("transcript_error") and not meta.get("transcript"):
            print(f"⚠️  Transkrypcja niedostępna: {meta.get('transcript_error')}", file=sys.stderr)
        print(f"   Status:  stage: {args.stage} | consumed: false")

    return 0


if __name__ == "__main__":
    sys.exit(main())
