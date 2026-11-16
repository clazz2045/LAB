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

from lab_core.installer import InstallResult  # noqa: E402
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
        res = InstallResult(
            success=True,
            target_dir=Path("/tmp/LAB-Test"),
            instance_name="LAB",
            elapsed_seconds=0.1,
        )
        html_out = _render_result_html(res)
        self.assertIn("Kopiuj prompt inicjujący", html_out)
        self.assertIn("Pełna Karta Startowa LAB", html_out)


if __name__ == "__main__":
    unittest.main()
