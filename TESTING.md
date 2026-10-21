# Testing Guide & Verification Procedures

This guide provides instructions for setting up the environment, executing the automated test suite, running static analysis, and verifying integration behaviors for the Giveaway Bot.

---

## 1. Environment Requirements

- **Python**: Version 3.11 or newer (tested against Python 3.13.15).
- **Virtual Environment**: `.venv` directory containing project dependencies.
- **Operating System**: Windows, Linux, or macOS.
- **Node.js** (Optional, for web dashboard tests only): Node.js >= 20.

---

## 2. Installation Instructions

### 2.1 Virtual Environment Setup (PowerShell / Windows)
```powershell
# Create virtual environment if not present
python -m venv .venv

# Activate environment
.\.venv\Scripts\Activate.ps1

# Upgrade pip
.\.venv\Scripts\python.exe -m pip install --upgrade pip

# Install bot dependencies (including Turso/libsql and Ruff)
.\.venv\Scripts\pip.exe install -e "bot[turso,dev]"
.\.venv\Scripts\pip.exe install -r requirements.txt
```

### 2.2 Virtual Environment Setup (Linux / macOS / Bash)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e "bot[turso,dev]"
pip install -r requirements.txt
```

---

## 3. Automated Test Execution

The test suite uses standard-library `unittest` with an in-memory SQLite backend that executes the exact SQL queries, indexes, migrations, and constraints used in production. **No Turso account, network access, or Discord tokens are required to run the automated tests.**

### 3.1 Full Test Suite Run
Run all 372 automated tests with strict warning checking:

**Windows (PowerShell)**:
```powershell
& .\.venv\Scripts\python.exe -W error::RuntimeWarning -m unittest discover -s bot/tests -t bot
```

**Linux / macOS (Bash)**:
```bash
./.venv/bin/python -W error::RuntimeWarning -m unittest discover -s bot/tests -t bot
```

### 3.2 Running Specific Test Modules
```powershell
# Core giveaway rules, claims, entries, and persistence
& .\.venv\Scripts\python.exe -m unittest discover -s bot/tests -p "test_service.py" -t bot

# Discord bot interaction, slash commands, autocomplete, and timers
& .\.venv\Scripts\python.exe -m unittest discover -s bot/tests -p "test_bot.py" -t bot

# Database connection lifecycle, dead streams, and replay safety
& .\.venv\Scripts\python.exe -m unittest discover -s bot/tests -p "test_db.py" -t bot

# Redis cache-aside, locks, and circuit breaker
& .\.venv\Scripts\python.exe -m unittest discover -s bot/tests -p "test_redis_cache.py" -t bot

# Redis-Turso integration and fallback
& .\.venv\Scripts\python.exe -m unittest discover -s bot/tests -p "test_redis_integration.py" -t bot

# HTTP health and readiness server
& .\.venv\Scripts\python.exe -m unittest discover -s bot/tests -p "test_health.py" -t bot

# CLI subcommands (doctor, run, --version)
& .\.venv\Scripts\python.exe -m unittest discover -s bot/tests -p "test_cli.py" -t bot

# Embed generation and countdown formatting
& .\.venv\Scripts\python.exe -m unittest discover -s bot/tests -p "test_embeds.py" -t bot

# Configuration parsing and environment validation
& .\.venv\Scripts\python.exe -m unittest discover -s bot/tests -p "test_config.py" -t bot
```

---

## 4. Linting & Static Code Quality

The project uses `ruff` for linting, syntax checking, and code style validation.

### 4.1 Run Ruff Linter
```powershell
& .\.venv\Scripts\ruff.exe check bot
```

### 4.2 Run Ruff Format Check
```powershell
& .\.venv\Scripts\ruff.exe format --check bot
```

---

## 5. Pre-Deployment Configuration Diagnostics

Verify environment variables and remote Turso connectivity without starting the Discord gateway:

```powershell
& .\.venv\Scripts\python.exe app.py doctor
```
Or from the `bot/` directory:
```powershell
& ..\.venv\Scripts\python.exe -m giveaway_bot doctor
```

Output highlights missing required secrets (`DISCORD_BOT_TOKEN`, `TURSO_DATABASE_URL`), warns on insecure URL formats (such as local `file:` databases on ephemeral hosts), and performs a live database ping.

---

## 6. Known Test Limitations

1. **Discord API Mocks**:
   - `test_bot.py` uses controlled test doubles for Discord interactions (`FakeInteraction`, `FakeGuild`, `FakeMember`, `FakeTextChannel`). Live Discord gateway authentication is verified via `app.py doctor` and pre-deployment smoke checks.
2. **Dashboard Node.js Tests**:
   - Tests in `dashboard/smoke-api.js` require Node.js runtime environment. When running in a Python-only worker container, dashboard tests are deferred to the Vercel deploy pipeline.
3. **Redis Integration Testing**:
   - `test_redis_cache.py` uses high-fidelity fake Redis clients mimicking Redis command semantics, timing, connection drops, and pipeline responses. Live Redis benchmark can be executed via `bot/bench_redis.py` when `REDIS_URL` is available.

