"""Tests for the giveaway-bot CLI (cli.py)."""

from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

from giveaway_bot import __version__, cli
from giveaway_bot.config import Settings


class CliTests(unittest.TestCase):
    def test_version_flag(self) -> None:
        out = io.StringIO()
        with self.assertRaises(SystemExit) as cm, patch("sys.stdout", out):
            cli.main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(__version__, out.getvalue())

    def test_doctor_reports_missing_env(self) -> None:
        settings = Settings(bot_token="", turso_url="", turso_token="")
        out = io.StringIO()
        with patch("giveaway_bot.cli.get_settings", return_value=settings), patch("sys.stdout", out):
            code = cli.main(["doctor"])
        self.assertEqual(code, 1)
        val = out.getvalue()
        self.assertIn("DISCORD_BOT_TOKEN is not set", val)
        self.assertIn("TURSO_DATABASE_URL is not set", val)

    def test_doctor_detects_bad_token_format_and_file_url(self) -> None:
        settings = Settings(bot_token="invalid-token", turso_url="file:local.db", turso_token="")  # noqa: S106
        out = io.StringIO()
        with patch("giveaway_bot.cli.get_settings", return_value=settings), patch("sys.stdout", out):
            code = cli.main(["doctor"])
        self.assertEqual(code, 1)
        val = out.getvalue()
        self.assertIn("does not look like a bot token", val)
        self.assertIn("TURSO_DATABASE_URL is a file: URL", val)

    def test_doctor_success_with_healthy_database(self) -> None:
        settings = Settings(
            bot_token="aaa.bbb.ccc",  # noqa: S106
            turso_url="libsql://dummy.turso.io",
            turso_token="dummy-token",  # noqa: S106
        )
        mock_db = MagicMock()
        mock_db.backend = "turso"
        mock_db.query_one.side_effect = [{"n": 10}, {"n": 2}]
        out = io.StringIO()
        with (
            patch("giveaway_bot.cli.get_settings", return_value=settings),
            patch("giveaway_bot.cli.Database", return_value=mock_db),
            patch("sys.stdout", out),
        ):
            code = cli.main(["doctor"])
        self.assertEqual(code, 0)
        val = out.getvalue()
        self.assertIn("[ok] configuration looks good", val)
        self.assertIn("giveaways on record: 10 (2 active)", val)
        mock_db.close_all.assert_called_once()

    def test_run_command_delegates_to_run_forever(self) -> None:
        settings = Settings(bot_token="aaa.bbb.ccc", turso_url="libsql://dummy")  # noqa: S106
        with (
            patch("giveaway_bot.cli.get_settings", return_value=settings),
            patch("giveaway_bot.cli.run_forever") as mock_run,
        ):
            code = cli.main(["run"])
            self.assertEqual(code, 0)
            mock_run.assert_called_once_with(settings)


if __name__ == "__main__":
    unittest.main()
