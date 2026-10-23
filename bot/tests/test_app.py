"""Unit tests for the repository-root app.py entrypoint."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# app.py is located at the repository root
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app  # noqa: E402


class AppEntrypointTests(unittest.TestCase):
    def test_main_defaults_to_run(self) -> None:
        with patch.object(sys, "argv", ["app.py"]), patch(
            "giveaway_bot.cli.main", return_value=0
        ) as mock_cli:
            code = app.main()
            self.assertEqual(code, 0)
            mock_cli.assert_called_once_with(["run"])

    def test_main_forwards_arguments(self) -> None:
        with patch.object(sys, "argv", ["app.py", "doctor"]), patch(
            "giveaway_bot.cli.main", return_value=42
        ) as mock_cli:
            code = app.main()
            self.assertEqual(code, 42)
            mock_cli.assert_called_once_with(["doctor"])

    def test_main_handles_missing_bot_cli(self) -> None:
        fake_path = Path("/nonexistent/giveaway_bot/cli.py")
        with patch.object(app, "BOT_CLI", fake_path), patch(
            "sys.stderr", new_callable=io.StringIO
        ) as mock_stderr:
            code = app.main()
            self.assertEqual(code, 1)
            self.assertIn("error: cannot find the bot package", mock_stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

