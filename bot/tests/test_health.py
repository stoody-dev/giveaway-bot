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


class MetricsRouteTests(unittest.TestCase):
    TOKEN = "scrape-token-1234"  # noqa: S105 - test fixture, not a credential

    def _serve(self, token: str, provider) -> int:
        server = start_health_server(
            0, ready_state={"status": "online", "discord": "online"},
            metrics_token=token, metrics_provider=provider,
        )
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def _call(self, port: int, path: str, auth: str | None = None, method: str = "GET"):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)  # noqa: S310
        if auth is not None:
            req.add_header("Authorization", auth)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                return resp.status, resp.read().decode(), resp.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode(), exc.headers

    def test_route_does_not_exist_without_a_token(self) -> None:
        port = self._serve("", lambda: "giveaway_bot_up 1\n")
        status, body, _ = self._call(port, "/metrics", f"Bearer {self.TOKEN}")
        self.assertEqual((status, body), (404, "not found"))

    def test_missing_or_wrong_token_is_unauthorized(self) -> None:
        port = self._serve(self.TOKEN, lambda: "giveaway_bot_up 1\n")
        for auth in (None, "Bearer wrong", self.TOKEN, "Basic abc"):
            status, body, _ = self._call(port, "/metrics", auth)
            self.assertEqual(status, 401, auth)
            self.assertNotIn("giveaway_bot_up", body)

    def test_right_token_serves_the_provider_page(self) -> None:
        port = self._serve(self.TOKEN, lambda: "giveaway_bot_up 1\n")
        status, body, headers = self._call(port, "/metrics?x=1", f"Bearer {self.TOKEN}")
        self.assertEqual(status, 200)
        self.assertEqual(body, "giveaway_bot_up 1\n")
        self.assertTrue(headers["Content-Type"].startswith("text/plain; version=0.0.4"))
        code, empty, _ = self._call(port, "/metrics", f"Bearer {self.TOKEN}", method="HEAD")
        self.assertEqual((code, empty), (200, ""))

    def test_provider_failure_only_breaks_metrics(self) -> None:
        def boom() -> str:
            raise RuntimeError("stats exploded")

        port = self._serve(self.TOKEN, boom)
        status, body, _ = self._call(port, "/metrics", f"Bearer {self.TOKEN}")
        self.assertEqual(status, 500)
        self.assertNotIn("exploded", body)
        self.assertEqual(self._call(port, "/health")[0], 200)
        self.assertEqual(self._call(port, "/ready")[0], 200)


if __name__ == "__main__":
    unittest.main()
