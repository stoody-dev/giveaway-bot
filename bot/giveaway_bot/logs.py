"""Logging setup: the familiar text format by default, one JSON object per line on request.

Set ``LOG_FORMAT=json`` for hosts that index logs (Render, Loki, Datadog) and
``LOG_LEVEL`` to raise or lower the verbosity. Both are read in config.py, so a
typo falls back to the defaults instead of breaking startup.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

#: Attributes every LogRecord has. Anything else came from ``extra=`` and is
#: copied into the JSON line.
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, exc and any ``extra=`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                out[key] = value
        return json.dumps(out, default=str, ensure_ascii=False)


class RedactFilter(logging.Filter):
    """Replace the secrets in a record with ``***`` before any handler sees it.

    Errors from Discord and Turso occasionally echo a credential back; this is
    the last line of defence, not a license to log them.
    """

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        # Short strings would mangle ordinary words, so only long values count.
        self._secrets = tuple(s for s in secrets if s and len(s) >= 8)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        message = record.getMessage()
        if any(secret in message for secret in self._secrets):
            for secret in self._secrets:
                message = message.replace(secret, "***")
            record.msg = message
            record.args = None
        return True


def configure_logging(
    fmt: str = "text", level: str = "INFO", secrets: tuple[str, ...] = ()
) -> None:
    """Install one stderr handler on the root logger, replacing any earlier one."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT))
    handler.addFilter(RedactFilter(secrets))
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))
