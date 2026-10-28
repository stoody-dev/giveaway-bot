"""Prometheus text exposition for the health server's ``/metrics`` route.

Everything here is read from counters the bot already keeps; nothing is
measured by calling Discord, Turso or Redis. ``render`` is a pure function of
plain values so it is trivial to test, and ``amain`` gathers the values.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

_DISCORD_STATES = ("starting", "online", "rate-limited", "auth-failed", "stopped")


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _number(value: object) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "0"
    return str(int(number)) if number == int(number) else repr(number)


class _Writer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def metric(
        self, name: str, kind: str, help_text: str, samples: list[tuple[Mapping[str, object], object]]
    ) -> None:
        self.lines.append(f"# HELP {name} {help_text}")
        self.lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            tag = ""
            if labels:
                tag = "{" + ",".join(f'{k}="{_label(v)}"' for k, v in labels.items()) + "}"
            self.lines.append(f"{name}{tag} {_number(value)}")


def render(
    *,
    discord_state: str,
    uptime_seconds: float,
    db_stats: Mapping[str, Any],
    redis_stats: Mapping[str, Any] | None,
    guilds: int,
    pending_invalidations: int,
) -> str:
    """Build the metrics page. ``redis_stats`` is RedisCache.stats(), or None without a cache."""
    w = _Writer()
    w.metric("giveaway_bot_up", "gauge", "1 while the process is running.", [({}, 1)])
    w.metric("giveaway_bot_uptime_seconds", "gauge", "Seconds since the process started.",
             [({}, uptime_seconds)])
    states = {*_DISCORD_STATES, discord_state}
    w.metric(
        "giveaway_bot_discord_state", "gauge",
        "1 for the current Discord gateway state, 0 for the others.",
        [({"state": s}, 1 if s == discord_state else 0) for s in sorted(states)],
    )
    w.metric("giveaway_bot_guilds", "gauge", "Guilds the bot is in.", [({}, guilds)])

    w.metric("giveaway_bot_db_statements_total", "counter",
             "Database statement attempts, retries included.",
             [({"kind": "read"}, db_stats.get("reads", 0)),
              ({"kind": "write"}, db_stats.get("writes", 0))])
    w.metric("giveaway_bot_db_seconds_total", "counter", "Time spent in database statements.",
             [({"kind": "read"}, float(db_stats.get("read_ms", 0.0)) / 1000.0),
              ({"kind": "write"}, float(db_stats.get("write_ms", 0.0)) / 1000.0)])

    w.metric("giveaway_bot_pending_invalidations", "gauge",
             "Cache keys queued for invalidation and not yet flushed.", [({}, pending_invalidations)])

    if redis_stats is not None:
        counters = redis_stats.get("counters", {}) or {}
        w.metric("giveaway_bot_redis_available", "gauge", "1 when the Redis cache is connected.",
                 [({}, 1 if redis_stats.get("available") else 0)])
        w.metric("giveaway_bot_redis_breaker_open", "gauge", "1 while the circuit breaker is open.",
                 [({}, 1 if redis_stats.get("breaker_open") else 0)])
        w.metric("giveaway_bot_redis_ops_total", "counter", "Redis operations by outcome.",
                 [({"result": r}, counters.get(r, 0)) for r in ("attempted", "ok", "failed", "skipped")])
        w.metric("giveaway_bot_redis_lookups_total", "counter", "Cache lookups by outcome.",
                 [({"result": r}, counters.get(k, 0)) for r, k in (("hit", "hits"), ("miss", "misses"))])
        errors = redis_stats.get("errors", {}) or {}
        if errors:
            w.metric("giveaway_bot_redis_errors_total", "counter", "Redis errors by exception class.",
                     [({"kind": k}, v) for k, v in sorted(errors.items())])
    return "\n".join(w.lines) + "\n"
