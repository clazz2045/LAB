"""Testy jednostkowe dla kreatora WWW (lab_core.setup)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys
import threading
import unittest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lab_core.installer import DEFAULT_AGENT_NAMES, InstallOptions, InstallResult  # noqa: E402
from lab_core.setup import (  # noqa: E402
    _make_wizard_handler,
    _render_result_html,
    _render_wizard_html,
)


class TestSetupWebWizard(unittest.TestCase):
    def test_wizard_html_guards_enter_submit_and_bfcache(self) -> None:
        html_out = _render_wizard_html()
        self.assertIn('id="wizardForm" autocomplete="off"', html_out)
        self.assertIn("submitBtn.disabled = !isLast;", html_out)
        self.assertIn('wizardForm.addEventListener("keydown"', html_out)
        self.assertIn('e.key === "Enter"', html_out)
        self.assertIn("changeStep(1);", html_out)
        self.assertIn('wizardForm.addEventListener("submit"', html_out)
        self.assertIn('window.addEventListener("pageshow"', html_out)

    def test_wizard_summary_step_and_back_button(self) -> None:
        html_out = _render_wizard_html()
        self.assertIn("12. Podsumowanie przed instalacją", html_out)
        self.assertIn('onclick="changeStep(-1)"', html_out)
        self.assertIn(">Wróć</button>", html_out)
        self.assertIn(">Zatwierdź i zainstaluj LAB</button>", html_out)
        self.assertIn("Imiona agentów AI:", html_out)
        self.assertIn("Motywy Obsidiana:", html_out)
        self.assertIn("Lokalizacja skarbca:", html_out)

    def test_wizard_http_server_sends_no_cache_headers(self) -> None:
        class _FakeSocket:
            def __init__(self, req_bytes: bytes) -> None:
                self._r = BytesIO(req_bytes)
                self.out = BytesIO()

            def makefile(self, mode: str, *_args, **_kwargs):
                return self._r if "r" in mode else self.out

            def sendall(self, data: bytes) -> None:
                self.out.write(data)

            def close(self) -> None:
                pass

        done_event = threading.Event()
        result_holder = {}
        handler_cls = _make_wizard_handler(done_event, result_holder)
        sock = _FakeSocket(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        handler_cls(sock, ("127.0.0.1", 12345), None)
        raw_resp = sock.out.getvalue().decode("utf-8", errors="replace")
        head, _, body = raw_resp.partition("\r\n\r\n")
        self.assertIn("200 OK", head)
        self.assertIn("Cache-Control: no-store, no-cache, must-revalidate", head)
        self.assertIn("Pragma: no-cache", head)
        self.assertIn("Expires: 0", head)
        self.assertIn('id="wizardForm" autocomplete="off"', body)

    def test_result_html_includes_starter_prompt_and_card(self) -> None:
        agents = dict(DEFAULT_AGENT_NAMES)
        agents["coordinator"] = "Zenek"
        agents["engineer"] = "Borys"
        opts = InstallOptions(
            target_dir=Path("/tmp/LAB-Test"),
            instance_name="LAB-Custom",
            user_name="Jan",
            coordinator_name="Zenek",
            theme_mode="dark",
            theme_preset_dark="nord",
            theme_preset_light="dawn",
            llm_provider="antigravity",
            agent_names=agents,
        )
        res = InstallResult(
            success=True,
            target_dir=Path("/tmp/LAB-Test"),
            instance_name="LAB-Custom",
            elapsed_seconds=0.1,
        )
        html_out = _render_result_html(res, options=opts)
        self.assertIn("Kopiuj prompt inicjujący", html_out)
        self.assertIn("Pełna Karta Startowa LAB", html_out)
        self.assertIn("Podsumowanie zastosowanej konfiguracji:", html_out)
        self.assertIn("Cześć Zenek!", html_out)
        self.assertIn("200–250k tokenów", html_out)
        self.assertIn("/checkpoint", html_out)
        self.assertIn("_fallbackCopyText", html_out)
        self.assertIn('document.execCommand("copy")', html_out)
        self.assertIn('id="copyErrorMsg"', html_out)
        self.assertIn("Jan", html_out)
        self.assertIn("LAB-Custom", html_out)
        self.assertIn("ciemny: nord", html_out)
        self.assertIn("Inżynier: Borys", html_out)

    def test_wizard_new_step_order_codex_and_icloud_hints(self) -> None:
        html_out = _render_wizard_html()
        self.assertIn("<h2>3. Środowisko AI – wybór programu</h2>", html_out)
        self.assertIn('<option value="codex">OpenAI Codex CLI</option>', html_out)
        self.assertIn("<h2>4. Lokalizacja sejfu</h2>", html_out)
        self.assertIn('id="vaultPathPreviewCode"', html_out)
        self.assertIn("<h2>5. Przechowywanie w iCloud Drive</h2>", html_out)
        self.assertIn("<h2>6. Bramka iCloud: Optimize Mac Storage = OFF</h2>", html_out)
        self.assertIn("Jeśli nie chcesz wyłączać optymalizacji iCloud dla całego Maca, wróć i wybierz katalog lokalny (np. ~/LAB).", html_out)
        self.assertIn("«Zachowuj pobrane»", html_out)

    def test_result_html_detects_fail_in_smoke_check(self) -> None:
        res = InstallResult(
            success=True,
            target_dir=Path("/tmp/LAB-Test"),
            instance_name="LAB",
            elapsed_seconds=0.1,
            smoke_check_passed=False,
            smoke_check_details=["TOML: OK", "SQLite WAL + UTF-8: FAIL (błąd)", "session_bootstrap: FAIL (brak pliku)"],
        )
        html_out = _render_result_html(res)
        self.assertIn("Konfiguracja: <strong>Błąd</strong>", html_out)
        self.assertIn("Baza danych: <strong>Błąd</strong>", html_out)
        self.assertIn("Środowisko: <strong>Błąd</strong>", html_out)

    def test_build_options_for_codex_forces_icloud_false(self) -> None:
        from lab_core.setup import _build_options_from_form

        opts = _build_options_from_form({
            "user_name": ["Jan"],
            "instance_name": ["CodexVault"],
            "location_preset": ["__custom__"],
            "custom_location": ["/tmp"],
            "icloud_enabled": ["yes"],
            "llm_provider": ["codex"],
        })
        self.assertEqual(opts.llm_provider, "codex")
        self.assertFalse(opts.icloud_enabled)
        self.assertEqual(opts.llm_model, "gpt-5.4")
        self.assertEqual(opts.session_token_budget, 150_000)

        res = InstallResult(
            success=True,
            target_dir=Path("/tmp/CodexVault"),
            instance_name="CodexVault",
            elapsed_seconds=0.1,
        )
        html_codex = _render_result_html(res, options=opts)
        self.assertIn('id="telemetryBudgetInfo"', html_codex)
        self.assertIn("120–150k tokenów", html_codex)
        self.assertIn("OpenAI Codex CLI", html_codex)


if __name__ == "__main__":
    unittest.main()
