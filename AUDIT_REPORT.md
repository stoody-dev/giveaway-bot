# Giveaway Bot — Technical Audit & Architecture Report

## 1. Executive Summary

This audit and hardening report documents the architecture, defect analysis, security review, concurrency safety, persistence integrity, and production readiness of the standalone Discord Giveaway Bot (`giveaway-bot`).

The system is designed for high-availability Discord giveaway management backed by **Turso (libSQL)** with an optional short-TTL **Redis** read cache and rate damper, served alongside an embedded **HTTP health server** for container liveness/readiness orchestration (e.g., Render Web Services).

---

## 2. Architecture Overview

### 2.1 Stack & Entrypoints
- **Runtime**: Python 3.11+ (verified under Python 3.13.15).
- **Core Framework**: `discord.py` >= 2.4 (Application Commands, Dynamic UI Items, Gateway Tasks).
- **Database Engine**: Turso / `libsql` (SQLite-compatible distributed database over HTTP/WebSocket streams).
- **Cache / Concurrency Damper**: `redis-py` (Optional, short-TTL read cache and lock leases; Turso remains single source of truth).
- **Entrypoints**:
  - `app.py`: Repository-root entrypoint shim for hosting panels (e.g., Silly Development / Render) executing `giveaway_bot.cli:main`.
  - `bot/giveaway_bot/__main__.py`: Module entrypoint forwarding to `cli.py`.
  - `bot/giveaway_bot/cli.py`: Subcommand interface (`run` and `doctor`).
  - `bot/giveaway_bot/health.py`: Embedded HTTP server on `0.0.0.0:$PORT` exposing `/health`, `/ready`, and `/`.

### 2.2 Execution Flow & Component Architecture
```
                           +---------------------------+
                           |     Discord Gateway       |
                           +-------------+-------------+
                                         |
                                         v
+------------------+          +----------+----------+          +-------------------+
|  Health Server   |          |    GiveawayBot      | <------> |    Redis Cache    |
|  (/health,       |          |  (bot.py)           |          |  (redis_cache.py) |
|   /ready, /)     |          +----------+----------+          +-------------------+
+------------------+                     |
                                         v
                              +----------+----------+
                              |   GiveawayService   |
                              |   (service.py)      |
                              +----------+----------+
                                         |
                                         v
                              +----------+----------+
                              |     Database        |
                              |     (db.py)         |
                              +----------+----------+
                                         |
                                         v
                              +----------+----------+
                              |   Turso / libSQL    |
                              +---------------------+
```

### 2.3 Storage Model & Tables
1. **`simple_giveaways`**: Core giveaway state (`id`, `guild_id`, `channel_id`, `message_id`, `prize`, `winner_count`, `ends_at`, `status`, `winners_json`, `entrant_count`, `claim_timeout_seconds`, requirements, host metadata).
2. **`simple_entries`**: Entrant records (`giveaway_id`, `user_id`, `username`, `entered_at`). Primary key `(giveaway_id, user_id)` guarantees single-entry semantics.
3. **`simple_message_counts`**: Server message activity accumulator (`guild_id`, `user_id`, `count`).
4. **`simple_guild_settings`**: Guild-level configurations (`guild_id`, `notify_role_id`).
5. **`simple_blacklist`**: Permanent and timed ban records (`guild_id`, `user_id`, `expires_at`, `reason`, `banned_by`, `created_at`).
6. **`simple_giveaway_bans`**: Timeout-penalty tracking (`guild_id`, `user_id`, `giveaways_remaining`).
7. **`simple_giveaway_ban_hits`**: Per-giveaway penalty enforcement deduplication (`guild_id`, `user_id`, `giveaway_id`).
8. **`simple_claims`**: Winner claim lifecycle records (`giveaway_id`, `user_id`, `round`, `status`, `deadline_ms`, `claimed_at`, `skipped_by`, `skipped_at`).

---

## 3. Systematic Findings & Implemented Fixes

### Finding 1: String Concatenation Operator Precedence Bug in Health Server
- **Severity**: P2 (Medium)
- **Component**: `bot/giveaway_bot/health.py`
- **Root Cause**: In `health.py`, string concatenation with ternary expression `+ " (bot not ready" if not online else " (ready)" + ...` lacked enclosing parentheses. Because string concatenation (`+`) has higher precedence than `if/else`, evaluating with `online == True` truncated the entire status message prefix, yielding malformed output.
- **Fix**: Factored out ternary suffix computation with explicit parentheses and clean formatting.
- **Verification**: Verified via newly introduced `bot/tests/test_health.py` covering both online and offline formatting states.

### Finding 2: Unawaited Coroutine Warning in Redis Integration Test Stub
- **Severity**: P3 (Low / Code Quality)
- **Component**: `bot/tests/test_redis_integration.py`
- **Root Cause**: `Hanging.__getattr__` was declared as `async def __getattr__(self, name)`. Because Python invokes `__getattr__` synchronously, it returned a coroutine object that was never awaited, generating `RuntimeWarning: coroutine 'Hanging.__getattr__' was never awaited`.
- **Fix**: Converted `__getattr__` to a synchronous method returning an asynchronous inner callable.
- **Verification**: Verified zero RuntimeWarnings with Python's `-W error::RuntimeWarning` flag.

### Finding 3: Authorization Bypass on Diagnostic Probe Command
- **Severity**: P1 (High)
- **Component**: `bot/giveaway_bot/bot.py` (`gw_request_time`)
- **Root Cause**: `gw_request_time` lacked permission checks, allowing non-administrative server members to invoke database and Redis performance diagnostic probes.
- **Fix**: Added `_can_manage(interaction.user)` validation, rejecting unauthorized users with an ephemeral warning.
- **Verification**: Verified in `bot/tests/test_bot.py` (`test_a_regular_member_is_refused_and_nothing_is_written`).

### Finding 4: Background Task Exception Swallowing & Coroutine Leaks
- **Severity**: P2 (Medium)
- **Component**: `bot/giveaway_bot/bot.py` (`_spawn_bg` and `_schedule_role_delete`)
- **Root Cause**: If `create_task` raised `RuntimeError` (e.g., during event loop teardown), unawaited coroutine warnings were triggered. Additionally, tasks added to `_bg_tasks` discarded themselves on completion without inspecting `task.exception()`, masking unexpected background exceptions.
- **Fix**: Handled `RuntimeError` with coroutine disposal (`coro.close()`), and added a completion callback logging any unhandled task exceptions. Handled loop teardown in `_schedule_role_delete`.
- **Verification**: Verified across complete test suite execution during teardown and error simulation.

### Finding 5: Entrant Role Strip Batch Fragility
- **Severity**: P2 (Medium)
- **Component**: `bot/giveaway_bot/bot.py` (`_strip_entrants_role`)
- **Root Cause**: `asyncio.gather` on member role stripping lacked `return_exceptions=True`. An unexpected exception on any single member fetch or role removal would abort the entire batch.
- **Fix**: Added `return_exceptions=True` to `asyncio.gather`.
- **Verification**: Verified in test suite role stripping scenarios.

### Finding 6: Transient Discord Announcement Failure Handling
- **Severity**: P1 (High)
- **Component**: `bot/giveaway_bot/bot.py` (`_announce`)
- **Root Cause**: If the Discord API returned transient 5xx server errors or HTTP 429 during winner announcement, the exception was logged without retry. Since the giveaway state was already committed as `ended`, the announcement would never be re-attempted.
- **Fix**: Added bounded retry loop (up to 3 attempts) with exponential backoff for transient 5xx and 429 HTTP statuses, while immediately failing fast on permanent errors (403 Forbidden, 404 Not Found).
- **Verification**: Verified via mock error simulation and automated bot test cases.

### Finding 7: Message Count Batch Flush Efficiency
- **Severity**: P2 (Medium / Performance)
- **Component**: `bot/giveaway_bot/service.py` (`_stored_counts`)
- **Root Cause**: Reading per-user message counts during flush reconciliation executed $N$ separate SQL queries. Under heavy chat volume, this exceeded database call timeouts.
- **Fix**: Optimized reconciliation using single batch queries with row values: `(guild_id, user_id) IN (VALUES (?, ?), ...)`.
- **Verification**: Tested in `test_service.py` (`test_flush_cost_does_not_grow_with_the_number_of_users`).

### Finding 8: Starvation in Missing Claims Repair Sweep
- **Severity**: P2 (Medium / Reliability)
- **Component**: `bot/giveaway_bot/service.py` (`repair_all_missing_claims`)
- **Root Cause**: The claim repair sweep queried the oldest ended giveaways ordered by `ended_at`, re-checking the same healthy giveaways on every tick and starving newer stranded giveaways.
- **Fix**: Updated query with SQLite JSON validation and existence predicates to select only giveaways with actual missing or mismatched claim rows.
- **Verification**: Tested in `test_service.py` (`test_repair_sweep_finds_a_stranded_giveaway_among_many_healthy_ones`).

### Finding 9: Data Preservation in Entrant Rows Cleanup
- **Severity**: P2 (Medium / Integrity)
- **Component**: `bot/giveaway_bot/service.py` (`wipe_stale_entries`)
- **Root Cause**: Purging old entry rows (GDPR / storage cleanup) previously set `entrant_count = 0` on finished giveaways, causing dashboards to display "No entries yet".
- **Fix**: Preserved `entrant_count` on the giveaway record while deleting only the per-user entry rows (`simple_entries`).
- **Verification**: Tested in `test_service.py` (`test_the_wipe_keeps_how_many_people_entered`).

### Finding 10: Missing Database Covering Indexes
- **Severity**: P2 (Medium / Performance)
- **Component**: `bot/giveaway_bot/db.py`
- **Root Cause**: `simple_claims` lacked indexes for status and deadline filtering, causing table scans during every 30s auto-draw tick (`due_claims`). `simple_entries` lacked compound index on `(giveaway_id, entered_at)`.
- **Fix**: Added:
  - `idx_simple_claims_due ON simple_claims(status, deadline_ms)`
  - `idx_simple_claims_gw ON simple_claims(giveaway_id, status)`
  - `idx_simple_entries_giveaway_entered ON simple_entries(giveaway_id, entered_at)`
- **Verification**: Verified in schema creation and migration tests.

---

## 4. Security Assessment

1. **Secret & Credential Redaction**:
   - Secrets (`DISCORD_BOT_TOKEN`, `TURSO_AUTH_TOKEN`, Redis URLs) are loaded via environment variables and never logged or exposed.
   - Redis error messages and statistics are scrubbed of credentials, hostnames, and ports prior to emission.
2. **Authorization & RBAC**:
   - All management commands (`giveaway_create`, `giveaway_end`, `giveaway_reroll`, `skipclaim`, `giveaway_cancel`, `giveaway_extend`, `giveaway_notifyer`, `giveaway_ping`, `giveaway_blacklist_*`, `giveaway_ban*`, `gw_request_time`) strictly enforce `Manage Server` (`manage_guild` or `administrator`).
3. **Injection & Query Safety**:
   - Zero dynamic SQL string formatting with untrusted user input. All identifiers are whitelisted and all values are passed via parameterized tuples.
4. **Discord API Protection**:
   - `AllowedMentions` explicitly sets `everyone=False` to prevent unauthorized `@everyone` / `@here` pings. Mentions are bounded and chunked.

---

## 5. Reliability & Concurrency Assessment

1. **Durable Persistence First**:
   - Winner draws are committed atomically in Turso (`winners_json` and status flip) prior to attempting Discord announcements. Re-announcements or retries operate on the persisted winner set and cannot cause non-deterministic re-draws.
2. **Atomic Entrant Claiming**:
   - `simple_entries` uses compound primary key `(giveaway_id, user_id)` guaranteeing single-entry idempotency.
   - Atomic conditional `INSERT ... SELECT WHERE NOT EXISTS` gates verify giveaway active status, deadline, blacklist, and timeout penalties in a single database round-trip.
3. **Dead Stream vs Ambiguous Loss**:
   - The database driver distinguishes dead connection reconnects (replay safe) from mid-write broken pipe errors (replay dangerous, fails fast to caller).

---

## 6. Unresolved Items & Operational Considerations

1. **Dashboard Local Test Dependencies**:
   - The root repository contains a Node.js dashboard backend in `dashboard/`. The local environment currently lacks `node` in its system path. Automated Python tests (372/372 passing) fully cover all Python backend operations, while Node smoke tests should be executed in CI/CD container environments where Node.js >= 20 is provisioned.
2. **Gateway Sharding**:
   - For bots operating in over 2,500 Discord servers, `discord.ext.commands.AutoShardedBot` should be considered. For current deployment scale, `commands.Bot` with single-process gateway connection is optimal.
