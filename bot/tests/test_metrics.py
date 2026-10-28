"""Tests for the Prometheus page (metrics.py)."""

from __future__ import annotations

import unittest

from giveaway_bot.metrics import render

BASE = {
    "discord_state": "online",
    "uptime_seconds": 12.5,
    "db_stats": {"reads": 7, "writes": 3, "read_ms": 1500.0, "write_ms": 250.0},
    "redis_stats": None,
    "guilds": 4,
    "pending_invalidations": 2,
}


def samples(page: str) -> dict[str, float]:
    out = {}
    for line in page.splitlines():
        if line and not line.startswith("#"):
            name, value = line.rsplit(" ", 1)
            out[name] = float(value)
    return out


class RenderTests(unittest.TestCase):
    def test_core_metrics_and_units(self) -> None:
        s = samples(render(**BASE))
        self.assertEqual(s["giveaway_bot_up"], 1)
        self.assertEqual(s["giveaway_bot_uptime_seconds"], 12.5)
        self.assertEqual(s["giveaway_bot_guilds"], 4)
        self.assertEqual(s['giveaway_bot_db_statements_total{kind="read"}'], 7)
        self.assertEqual(s['giveaway_bot_db_statements_total{kind="write"}'], 3)
        self.assertEqual(s['giveaway_bot_db_seconds_total{kind="read"}'], 1.5)
        self.assertEqual(s["giveaway_bot_pending_invalidations"], 2)

    def test_discord_state_is_one_hot(self) -> None:
        s = samples(render(**{**BASE, "discord_state": "rate-limited"}))
        self.assertEqual(s['giveaway_bot_discord_state{state="rate-limited"}'], 1)
        self.assertEqual(s['giveaway_bot_discord_state{state="online"}'], 0)

    def test_unknown_state_is_still_reported(self) -> None:
        s = samples(render(**{**BASE, "discord_state": "weird"}))
        self.assertEqual(s['giveaway_bot_discord_state{state="weird"}'], 1)

    def test_no_redis_section_without_a_cache(self) -> None:
        self.assertNotIn("giveaway_bot_redis", render(**BASE))

    def test_redis_section_from_cache_stats(self) -> None:
        stats = {
            "available": True,
            "breaker_open": False,
            "counters": {"attempted": 10, "ok": 8, "failed": 1, "skipped": 1, "hits": 5, "misses": 3},
            "errors": {"TimeoutError": 1},
        }
        s = samples(render(**{**BASE, "redis_stats": stats}))
        self.assertEqual(s["giveaway_bot_redis_available"], 1)
        self.assertEqual(s["giveaway_bot_redis_breaker_open"], 0)
        self.assertEqual(s['giveaway_bot_redis_ops_total{result="skipped"}'], 1)
        self.assertEqual(s['giveaway_bot_redis_lookups_total{result="hit"}'], 5)
        self.assertEqual(s['giveaway_bot_redis_errors_total{kind="TimeoutError"}'], 1)

    def test_missing_db_stats_report_zero(self) -> None:
        s = samples(render(**{**BASE, "db_stats": {}}))
        self.assertEqual(s['giveaway_bot_db_statements_total{kind="read"}'], 0)

    def test_label_values_are_escaped(self) -> None:
        stats = {"available": False, "counters": {}, "errors": {'Bad"Name\n': 1}}
        page = render(**{**BASE, "redis_stats": stats})
        self.assertIn('kind="Bad\\"Name\\n"', page)
        self.assertTrue(page.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
