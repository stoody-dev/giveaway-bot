"""Tests for the HTTP health / readiness server (health.py)."""

from __future__ import annotations

import unittest
import urllib.error
import urllib.request

from giveaway_bot.health import start_health_server


class HealthServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = {"status": "starting", "discord": "starting"}
        # Port 0 lets OS choose an ephemeral free port
        self.server = start_health_server(0, ready_state=self.state)
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path: str) -> tuple[int, str]:
        req = urllib.request.Request(self._url(path), method="GET")  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def _head(self, path: str) -> tuple[int, bytes]:
        req = urllib.request.Request(self._url(path), method="HEAD")  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_health_liveness_always_ok(self) -> None:
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, "ok")

        status, body = self._get("/health?cachebust=123")
        self.assertEqual(status, 200)
        self.assertEqual(body, "ok")

    def test_ready_endpoint_reflects_discord_state(self) -> None:
        status, body = self._get("/ready")
        self.assertEqual(status, 503)
        self.assertIn("not ready", body)

        self.state["discord"] = "rate-limited"
        status, body = self._get("/ready")
        self.assertEqual(status, 503)
        self.assertIn("discord rate-limited", body)

        self.state["discord"] = "online"
        status, body = self._get("/ready")
        self.assertEqual(status, 200)
        self.assertEqual(body, "ready: discord online")

    def test_root_status_page_formatting_online_and_offline(self) -> None:
        # Offline formatting test:
        self.state["status"] = "starting"
        self.state["discord"] = "starting"
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("giveaway-bot: starting discord=starting", body)
        self.assertIn("(bot not ready — Discord commands appear once the gateway connects)", body)

        # Online formatting test:
        self.state["status"] = "online"
        self.state["discord"] = "online"
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("giveaway-bot: online — gateway connected discord=online (ready)", body)

    def test_head_requests(self) -> None:
        self.state["discord"] = "starting"
        code, body = self._head("/health")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")

        code, body = self._head("/ready")
        self.assertEqual(code, 503)
        self.assertEqual(body, b"")

        self.state["discord"] = "online"
        code, body = self._head("/ready")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")

        code, body = self._head("/")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")

    def test_unknown_path_returns_404(self) -> None:
        status, body = self._get("/unknown")
        self.assertEqual(status, 404)
        self.assertEqual(body, "not found")

        code, body = self._head("/unknown")
        self.assertEqual(code, 404)
        self.assertEqual(body, b"")


if __name__ == "__main__":
    unittest.main()
