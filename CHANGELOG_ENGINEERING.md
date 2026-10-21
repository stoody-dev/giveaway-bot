# Engineering Changelog & Release Notes

All notable technical changes, architectural hardening, bug fixes, and operational updates made to the Giveaway Bot codebase are documented in this changelog.

---

## [2.1.0] - Production Hardening & Reliability Release

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
  - `bot/tests/test_config.py`: Extended test suite with `_bool`, `_int`, and `tick_seconds` clamping tests.
- **Clean Linter Status**: Passed `ruff check bot` with zero warnings or errors.

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
- `DISCORD_BOT_TOKEN` (Required)
- `TURSO_DATABASE_URL` (Required)
- `TURSO_AUTH_TOKEN` (Required for remote Turso)
- `REDIS_URL` (Optional, short-TTL read cache)
- `REDIS_ENABLED` (Optional, default `true`)
- `TICK_SECONDS` (Optional, default `30`, minimum clamped to `5`)
- `PORT` (Optional, default `10000`)
- `EMBED_COLOR` (Optional, default `0x7C5CFF`)
- `DASHBOARD_URL` (Optional, default `https://giveaway-bot-duggal.vercel.app/`)

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
3. Restart the service using `python app.py`.

