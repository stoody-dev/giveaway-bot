# Engineering Changelog & Release Notes

All notable technical changes, architectural hardening, bug fixes, and operational updates made to the Giveaway Bot codebase are documented in this changelog.

---

## [Unreleased] - Redis Cache, Latency Work & Dashboard Hardening

Changes already in the tree that were not recorded in the 2.1.0 notes below.

### U1. Redis Read Cache (`giveaway_bot.redis_cache`, `giveaway_bot.service`)
- **Optional and fail-open**: enabled only when `REDIS_URL` is set and `REDIS_ENABLED` is true. Any Redis failure falls back to Turso reads.
- **Circuit breaker**: opens after 5 consecutive failures, 30s cooldown, then one probe call. One log warning per outage; host:port and token-like strings are scrubbed from errors.
- **Op budget**: per-process token bucket (80 ops/s, burst capped at 20) keeps the worst sliding second under the 100 ops/s plan ceiling. Over-budget calls are skipped, not queued, and look like a cache miss.
- **Atomic helpers**: Lua `INCR` + `PEXPIRE` in one round trip, and compare-and-delete lock release. Keys use the `gwbot:v1:` prefix.
- **Cached reads**: active giveaway list (8s), guild notify-role setting (10s), entry count (4s, zero counts are cached too).
- **Lossless invalidation queue**: worker threads queue stale keys (cap 1024, oldest dropped past the cap). Flushes deduplicate, delete at most 64 keys per call, and requeue overflow.
- **Single sweep lease**: `sweep:live` (90s TTL) stops multiple processes running the same list/embed sweep. Only a confirmed "held by someone else" answer skips the sweep; a Redis outage or exhausted budget never does. The lease is always released in `finally`.
- **Benchmark**: `bot/bench_redis.py` measures cold start, ping, hit/miss, set, incr, lock pairs, bulk delete and budget exhaustion against the real `REDIS_URL`, and cleans up its keys.

### U2. Latency & Concurrency (`giveaway_bot.bot`, `giveaway_bot.db`, `giveaway_bot.service`)
- **Pinned DB pool**: a shared 64-worker `ThreadPoolExecutor` plus a 64-slot gate carries every off-loop database read, including cached reads, instead of the default executor.
- **Lane warmup**: `on_ready` runs 64 `SELECT 1` queries so every lane has a live connection before the first click.
- **Turso connect**: `PRAGMA busy_timeout` is skipped on Turso, saving a round trip on cold lanes.
- **Call timeouts**: `_db_call` defaults to 8s; multi-statement calls (`end`, `reroll`, claim expiry, blacklist add, message flush) use `DB_LONG_TIMEOUT` (60s). A timed-out message flush is dropped rather than requeued, so counts are never double-added.
- **Join path**: one-read `join_snapshot` for eligibility and `join_and_refresh` for join plus status/count in a single lane.
- **Participants panel**: one read (`entries_snapshot`, window of 500) instead of several.
- **`gw_request_time`**: now reports the Redis legs (ping, cached list/count/notify-role cold then warm, set+delete) with HIT/MISS taken from the cache's real hit counter. It does not time the database. A preview giveaway row is used when none is live.
- **`doctor`**: reports whether Redis is configured (informational, never a failure).

### U3. Dashboard (`dashboard/`)
- **Admin fails closed**: every method returns 503 when `ADMIN_PASSWORD` is unset.
- **Password check**: SHA-256 of both sides compared with `timingSafeEqual`; wrong password waits 300ms before a 401. Admin responses are `no-store`, the admin page is `noindex`.
- **Batched queries**: card hydration uses one grouped count query and one `ROW_NUMBER()` names query per page instead of two queries per card.
- **Bulk delete**: one lookup plus one transactional batch (claims, entries, giveaways). Each statement re-checks `status != 'active'`, so a race with the bot ending a giveaway is safe, and no orphaned `simple_claims` rows remain.
- **Ended cards** show the stored `entrant_count` snapshot (live count for active giveaways, or when the snapshot is NULL on legacy rows).
- **Input and routing hardening**: `guild_id`/`id` validated, delete batch capped at 100, `page.js` lookup uses `Object.hasOwn` and serves only six baked files; errors return generic 500s.
- **CI**: the dashboard job rebuilds `api/page.js` and fails on drift, then runs `node smoke.js`.

### U4. Admin Session Tokens & Login Rate Limiting (`dashboard/api/admin.js`, `dashboard/api/_lib/adminAuth.js`)
- **Password is sent once**: `action: "login"` checks it and returns an HMAC-SHA256 signed token (8h lifetime). `ping`, `list` and `delete` now require `Authorization: Bearer <token>`. The `x-admin-password` header and `body.password` no longer authorize anything except `login`.
- **Signing key**: `ADMIN_SESSION_SECRET` when set, otherwise derived from `ADMIN_PASSWORD`, so changing the password signs every session out.
- **Browser**: `public/admin.js` stores only the token in `sessionStorage`; any 401 clears it and returns to the login form. `serve.js` mocks the same flow. `api/page.js` was re-baked.
- **Lockout**: 5 failed logins in 15 minutes lock that client for 15 minutes (429 + `Retry-After`). The check runs before the password is compared and a locked client is not counted further. A good login clears the counter.
- **Storage**: new dashboard-owned table `admin_login_attempts(ip_hash, fails, window_start, locked_until)` in the same Turso database, created with `CREATE TABLE IF NOT EXISTS` on first use. Only a salted SHA-256 of the client address is stored. Counting is a single upsert, so concurrent failures are not undercounted.
- **Fails closed**: if the attempts table cannot be read or written, login returns a generic 503 instead of running unlimited.
- **Known limit**: no global cap, so a distributed guesser is slowed only by the per-client locks; keep `ADMIN_PASSWORD` long and unique.

### U5. Structured Logging (`giveaway_bot.logs`, `giveaway_bot.config`)
- `LOG_FORMAT=json` writes one JSON object per line (`ts`, `level`, `logger`, `msg`, `exc`, plus any `extra=` fields). The default `text` output is the same line format as before.
- `LOG_LEVEL` selects the level (default `INFO`). Invalid values for either variable fall back to the defaults.
- A redaction filter replaces the bot token, Turso token and Redis URL with `***` if one ever appears in a log line.

### U6. Metrics (`giveaway_bot.health`, `giveaway_bot.metrics`)
- `GET /metrics` (Prometheus text) exists only when `METRICS_TOKEN` is set, and then needs `Authorization: Bearer <token>` (constant-time compare). Unset means the route 404s like any unknown path, because the health port is public on Render.
- Exposes read-only counters the bot already keeps: up/uptime, Discord state, guild count, database statements and seconds (reads/writes), pending cache invalidations, and, with Redis, availability, breaker state, operations by outcome, hits/misses and errors by class.
- A failure while building the page returns 500 for `/metrics` only; `/health` and `/ready` are unaffected.

### U7. Fixes
- **`/ready` no longer reports online early**: `amain` used to mark Discord "online" as soon as the client was entered, before login finished. `GiveawayBot.on_ready` now sets it, so `/ready` stays 503 until the gateway is genuinely up.
- **Docs and CI aligned**: CI runs the suite with `-W error::RuntimeWarning` (as `TESTING.md` says); `TESTING.md` uses the same install and run commands as CI and no longer lists a format check CI does not run; `README.md` and `bot/README.md` now cover Redis, `PORT`, `EMBED_COLOR`, the token fallback, health/metrics routes, claims, bans and the 15s message flush; stale 372-test counts in `AUDIT_REPORT.md` and `FAILURE_SCENARIOS.md` were updated.

---

### 1. Important Changes Summary

#### A. HTTP Health Server (`giveaway_bot.health`)
- **Ternary Operator Precedence Fix**: Corrected string concatenation in the root (`/`) status endpoint where unparenthesized ternary evaluation truncated status prefixes when online.
- **Dedicated Test Coverage**: Added `test_health.py` validating liveness (`/health`), readiness (`/ready`), and status formatting across all gateway states.

#### B. Discord Gateway & Bot Layer (`giveaway_bot.bot`)
- **Diagnostic Command Authorization**: Added `_can_manage` permission check on `gw_request_time`, preventing unauthorized users from running database and Redis latency probes.
- **Background Task Resilience (`_spawn_bg`)**:
  - Closed unawaited coroutines on `RuntimeError` (e.g., event loop shutdown).
  - Attached completion callback to log unhandled background task exceptions.
- **Role Strip Batch Protection**: Wrapped batch role removal in `return_exceptions=True` so that a failure on any single member fetch does not abort the remainder of the batch.
- **Safe Role Delete Scheduling**: Handled potential `RuntimeError` during loop shutdown in `_schedule_role_delete`, avoiding stranded entries in `_scheduled_role_deletes`.
- **Transient Announcement Retries**: Implemented bounded retries (3 attempts) with backoff in `_announce` for Discord 5xx and 429 errors.
- **Graceful Shutdown & Resource Cleanup**: Ensured `db.close_all()` is executed in the `finally` block of `amain()`, ensuring no dangling database connections.

#### C. Giveaway Service Layer (`giveaway_bot.service`)
- **Optimized Message Count Flush (`_stored_counts`)**: Replaced sequential per-user read queries with a single batch `IN (VALUES (?, ?), ...)` query, preventing flush timeouts on high-volume servers.
- **Claim Repair Sweep Query Targeting**: Updated `repair_all_missing_claims` query to use SQLite JSON functions and existence checks, ensuring only giveaways with missing or mismatched claims are visited.
- **Entrant Count Preservation on Wipe**: Updated `wipe_stale_entries` to retain `simple_giveaways.entrant_count` when scrubbing personal entry rows from `simple_entries`.

#### D. Database & Persistence Layer (`giveaway_bot.db`)
- **Covering Indexes Added**:
  - `idx_simple_claims_due` on `simple_claims(status, deadline_ms)`: Accelerates the 30s auto-draw tick query for due claims.
  - `idx_simple_claims_gw` on `simple_claims(giveaway_id, status)`: Accelerates claim lookups by giveaway ID.
  - `idx_simple_entries_giveaway_entered` on `simple_entries(giveaway_id, entered_at)`: Accelerates ordered entrant fetches.

#### E. Test Suite & Code Quality
- **Resolved RuntimeWarning**: Converted `Hanging.__getattr__` in `test_redis_integration.py` to a synchronous method returning an async callable.
- **New Unit Tests**:
  - `bot/tests/test_health.py`: 5 tests covering HTTP health, readiness, and status page behavior.
  - `bot/tests/test_cli.py`: 5 tests covering `doctor`, `run`, and `--version` CLI commands.
  - `bot/tests/test_config.py`: Extended test suite with `_bool`, `_int`, and `tick_seconds` clamping tests, plus token fallback and port validation.
  - `bot/tests/test_app.py`: 3 tests covering repository-root `app.py` argument forwarding, default run execution, and missing package handling.
- **Clean Linter Status**: Passed `ruff check bot app.py` with zero warnings or errors.
- **Overall Suite**: Expanded from 359 to 377 automated tests, 100% passing.

#### F. Additional Robustness & Performance Enhancements
- **Multi-Environment Token Discovery**: `DISCORD_TOKEN` supported alongside `DISCORD_BOT_TOKEN`, avoiding configuration friction on platforms that standardize on `DISCORD_TOKEN`.
- **Port and Interval Guarding**: Clamped `PORT` to valid TCP port range (`0 <= port <= 65535`) and bounded `TICK_SECONDS` to `5..3600`, preventing malformed environment values from crashing socket binds or stalling scheduling loops.
- **Guaranteed Cleanup in Diagnostics**: Wrapped preview giveaway rows in `gw_request_time` in a `try ... finally` block, ensuring probe data is discarded even on early probe errors or network cancellations.
- **Proactive Command Cache Flushing**: Added `bot._spawn_bg(bot._flush_invalidation())` to manual administrative actions (`giveaway_end`, `giveaway_cancel`, `giveaway_reroll`, `giveaway_extend`) so Redis cache keys invalidate immediately without waiting out TTLs.
- **Concurrent Active Giveaway Counting**: Refactored `/giveaway_list` to fetch up to 10 active giveaway entry counts concurrently using `asyncio.gather`, cutting multi-giveaway list latency.
- **Dependency & CI Harmonization**: Added `redis` optional dependency to `pyproject.toml` and aligned GitHub Actions workflow to Python 3.13 (matching `Dockerfile` and `sillydev.conf`).

---

### 2. Rationale & Problem Context

| Component | Issue Identified | Engineering Rationale |
|---|---|---|
| `health.py` | String concatenation bug in `/` endpoint | The root HTTP page was producing truncated output like `" (ready) — Discord commands appear..."` instead of the full service identifier and status. |
| `bot.py` | `gw_request_time` unauthenticated | Allowed non-privileged members to invoke database and Redis performance probes. |
| `bot.py` | Unhandled task exceptions in `_spawn_bg` | Tasks dropped silently or emitted unretrieved exception warnings upon GC. |
| `bot.py` | Transient announcement failure | If Discord returned 502/503 during winner announcement, the giveaway stayed marked as `ended` in Turso but was never announced. |
| `service.py` | Flush query scaling | Sequential queries during chat activity flush exceeded database timeout thresholds. |
| `service.py` | Claim repair starvation | Order-by-ended query repeatedly revisited the same healthy giveaways, starving newer ones needing claim repair. |
| `service.py` | Entrant count zeroed on wipe | Destroyed historical giveaway entry counts displayed on dashboard cards after 5 hours. |
| `db.py` | Missing claims index | The auto-draw loop scans `simple_claims` every 30 seconds; without indexes, query time scaled with total historical claims. |

---

### 3. Compatibility Impacts

- **Backward Compatibility**: Fully preserved. All database queries, schema additions, command names, and public interfaces are 100% backward compatible with existing installations.
- **Forward Compatibility**: Index additions use `CREATE INDEX IF NOT EXISTS` and require no manual migration scripts.

---

### 4. Database Migrations

No table alters or destructive schema modifications were introduced. Three non-blocking performance indexes were added:
```sql
CREATE INDEX IF NOT EXISTS idx_simple_claims_due ON simple_claims(status, deadline_ms);
CREATE INDEX IF NOT EXISTS idx_simple_claims_gw ON simple_claims(giveaway_id, status);
CREATE INDEX IF NOT EXISTS idx_simple_entries_giveaway_entered ON simple_entries(giveaway_id, entered_at);
```
These are automatically applied on startup by `db.init_schema()`.

---

### 5. Configuration Changes

No new mandatory environment variables were introduced. Existing configurations remain standard:
- `DISCORD_BOT_TOKEN` (Required; `DISCORD_TOKEN` is accepted as a fallback)
- `DISCORD_GIVEAWAY_CHANNEL_ID` (Optional; `GIVEAWAY_CHANNEL_ID` fallback; default is the command's own channel)
- `TURSO_DATABASE_URL` (Required)
- `TURSO_AUTH_TOKEN` (Required for remote Turso)
- `REDIS_URL` (Optional, short-TTL read cache)
- `REDIS_ENABLED` (Optional, default `true`)
- `TICK_SECONDS` (Optional, default `30`, minimum clamped to `5`)
- `PORT` (Optional, default `10000`)
- `EMBED_COLOR` (Optional, default `0x7C5CFF`)
- `DASHBOARD_URL` (Optional, default `https://giveaway-bot-duggal.vercel.app/`; empty removes the button)
- `ADMIN_PASSWORD` (Dashboard/Vercel only; the admin API is disabled without it)
- `ADMIN_SESSION_SECRET` (Dashboard/Vercel only, optional; pins the admin token signing key)
- `LOG_FORMAT` (Optional, `text` default or `json`) and `LOG_LEVEL` (Optional, default `INFO`)
- `METRICS_TOKEN` (Optional; enables the Bearer-protected `/metrics` route when set)

---

### 6. Deployment Considerations

1. **Zero-Downtime Deployment**:
   - The bot can be deployed directly via `git pull` and process restart (`python app.py` or container restart).
   - Startup hooks automatically run `init_schema()` safely without locking tables or dropping existing data.
2. **Container Health Checking**:
   - For Render or Docker health checks, configure `healthCheckPath: /health`.
   - `/health` guarantees liveness (process alive), avoiding cascading container restart loops during Discord login cooldowns.

---

### 7. Rollback Guidance

If a rollback is ever necessary:
1. Revert to the prior Git commit:
   ```bash
   git checkout <previous-commit-hash>
   ```
2. The newly created indexes (`idx_simple_claims_due`, `idx_simple_claims_gw`, `idx_simple_entries_giveaway_entered`) are harmless and fully compatible with prior versions of the application. They can either be retained or dropped with:
   ```sql
   DROP INDEX IF EXISTS idx_simple_claims_due;
   DROP INDEX IF EXISTS idx_simple_claims_gw;
   DROP INDEX IF EXISTS idx_simple_entries_giveaway_entered;
   ```
3. The dashboard (Vercel) rolls back independently of the bot. Rolling it back to a version before session tokens restores password-per-request admin auth, and open browser tabs simply fall back to the login form. The `admin_login_attempts` table is dashboard-only and harmless to older versions; drop it if you want it gone:
   ```sql
   DROP TABLE IF EXISTS admin_login_attempts;
   ```
4. Restart the service using `python app.py`.

---

### 8. Known Issues & Follow-ups

- **Admin lockout is per client only**: no global cap (see U4); mitigations include strong `ADMIN_PASSWORD` and per-IP lockout.
- **`/ready` during reconnects**: it reports the state set at the last `on_ready`; a short gateway disconnect does not flip it back to 503 (deliberate, to avoid flapping).
- **Gateway Sharding**: For bots in >2,500 guilds, Discord requires `AutoShardedBot`; current architecture uses `commands.Bot`.
- **Formatting**: `ruff format --check` is not enforced in CI to preserve existing whitespace conventions; `ruff check` is strictly enforced.

Resolved in 2.2.0:
- Version drift: bumped `__version__` and `pyproject.toml` to `2.2.0`.
- Dead code: pruned unused `GW_CACHE_TTL` in `service.py`.
- Dashboard `/api/health`: edge caching disabled via explicit `no-store` headers.
- Test gaps closed: added regression tests for `/api/health` and `serve.js` in `smoke-api.js` and `smoke.js`.
- Dashboard description: updated `package.json` to reflect full frontend client.

---

### 9. Verification (2026-10-28)

- `python -W error::RuntimeWarning -m unittest discover -s tests -t .` (from `bot/`, project `.venv`): 405 tests, all passing.
- `python -m ruff check .`: all checks passed.
- `node build-page.js` then `node smoke.js` (from `dashboard/`, Node 24): all checks passed, including admin tokens and login lockout.
- Manual: a real health server over HTTP returned `/ready` 503 while starting and 200 once online, and `/metrics` 401 without or with a wrong token and 200 with the right one.

