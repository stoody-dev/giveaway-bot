"""Tests for logging setup (logs.py) and its config switches."""

from __future__ import annotations

import io
import json
import logging
import os
import unittest
from unittest.mock import patch

from giveaway_bot.config import Settings
from giveaway_bot.logs import TEXT_FORMAT, JsonFormatter, RedactFilter, configure_logging

_RealStreamHandler = logging.StreamHandler


def _record(msg: str = "hello", *args: object, **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("giveaway_bot.test", logging.WARNING, __file__, 1, msg, args or None, None)
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


class JsonFormatterTests(unittest.TestCase):
    def test_line_is_one_json_object_with_core_fields(self) -> None:
        line = JsonFormatter().format(_record("joined %s", "gw_1"))
        data = json.loads(line)
        self.assertNotIn("\n", line)
        self.assertEqual(data["level"], "WARNING")
        self.assertEqual(data["logger"], "giveaway_bot.test")
        self.assertEqual(data["msg"], "joined gw_1")
        self.assertTrue(data["ts"].endswith("+00:00"))

    def test_extra_fields_are_included_and_standard_ones_are_not_duplicated(self) -> None:
        data = json.loads(JsonFormatter().format(_record("x", giveaway_id="gw_9", entrants=12)))
        self.assertEqual(data["giveaway_id"], "gw_9")
        self.assertEqual(data["entrants"], 12)
        for noise in ("levelno", "pathname", "args", "created", "msecs"):
            self.assertNotIn(noise, data)

    def test_exception_text_is_captured(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
        data = json.loads(JsonFormatter().format(rec))
        self.assertIn("ValueError: boom", data["exc"])

    def test_unserialisable_extra_does_not_raise(self) -> None:
        data = json.loads(JsonFormatter().format(_record("x", thing=object())))
        self.assertIn("object", data["thing"])


class RedactFilterTests(unittest.TestCase):
    def test_secret_is_masked_in_message_and_args(self) -> None:
        rec = _record("login failed for %s", "tok_abcdef123456")
        RedactFilter(("tok_abcdef123456",)).filter(rec)
        self.assertEqual(rec.getMessage(), "login failed for ***")

    def test_short_or_empty_secrets_are_ignored(self) -> None:
        rec = _record("the word abc stays")
        RedactFilter(("", "abc")).filter(rec)
        self.assertEqual(rec.getMessage(), "the word abc stays")

    def test_clean_records_pass_untouched(self) -> None:
        rec = _record("nothing secret %d", 5)
        self.assertTrue(RedactFilter(("tok_abcdef123456",)).filter(rec))
        self.assertEqual(rec.getMessage(), "nothing secret 5")


class ConfigureLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        root = logging.getLogger()
        self._handlers, self._level = list(root.handlers), root.level

    def tearDown(self) -> None:
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in self._handlers:
            root.addHandler(h)
        root.setLevel(self._level)

    def _emit(self, fmt: str, level: str = "INFO", secrets: tuple[str, ...] = ()) -> str:
        stream = io.StringIO()
        with patch("giveaway_bot.logs.logging.StreamHandler", lambda: _RealStreamHandler(stream)):
            configure_logging(fmt, level, secrets)
        logging.getLogger("giveaway_bot.test").info("started %s", "ok")
        logging.getLogger("giveaway_bot.test").debug("hidden")
        return stream.getvalue()

    def test_text_is_the_default_one_line_format(self) -> None:
        out = self._emit("text")
        self.assertIn("INFO giveaway_bot.test started ok", out)
        self.assertNotIn("{", out)
        self.assertEqual(logging.Formatter(TEXT_FORMAT)._fmt, TEXT_FORMAT)

    def test_json_mode_emits_parseable_lines(self) -> None:
        data = json.loads(self._emit("json").strip())
        self.assertEqual(data["msg"], "started ok")

    def test_level_filters_and_calling_twice_does_not_duplicate(self) -> None:
        self._emit("text")
        out = self._emit("text", "WARNING")
        self.assertEqual(out, "")
        self.assertEqual(len(logging.getLogger().handlers), 1)

    def test_token_never_reaches_the_output(self) -> None:
        stream = io.StringIO()
        with patch("giveaway_bot.logs.logging.StreamHandler", lambda: _RealStreamHandler(stream)):
            configure_logging("json", "INFO", ("secret-token-value",))
        logging.getLogger("giveaway_bot.test").error("auth failed: secret-token-value")
        self.assertNotIn("secret-token-value", stream.getvalue())


class LogSettingsTests(unittest.TestCase):
    def _settings(self, env: dict[str, str]) -> Settings:
        with patch.dict(os.environ, env, clear=False):
            return Settings()

    def test_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for key in ("LOG_FORMAT", "LOG_LEVEL", "METRICS_TOKEN"):
                os.environ.pop(key, None)
            s = Settings()
        self.assertEqual((s.log_format, s.log_level, s.metrics_token), ("text", "INFO", ""))

    def test_valid_values_are_normalised(self) -> None:
        s = self._settings({"LOG_FORMAT": " JSON ", "LOG_LEVEL": "debug", "METRICS_TOKEN": " tok "})
        self.assertEqual((s.log_format, s.log_level, s.metrics_token), ("json", "DEBUG", "tok"))

    def test_typos_fall_back_to_defaults(self) -> None:
        s = self._settings({"LOG_FORMAT": "yaml", "LOG_LEVEL": "loud"})
        self.assertEqual((s.log_format, s.log_level), ("text", "INFO"))


if __name__ == "__main__":
    unittest.main()
