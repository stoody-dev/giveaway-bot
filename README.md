# Giveaway Bot

Simple standalone Discord giveaway bot. MIT licensed.

```
.
├── bot/            Python Discord bot (discord.py), Turso-backed
├── dashboard/      Website + Vercel Node.js API (live + previous giveaways)
├── app.py          Root entry point for panels that start a file
├── requirements.txt  Bot dependencies
├── render.yaml     Render blueprint: bot web service
└── docker-compose.yml  Bot, locally
```

## What it does

- `/giveaway_create` — prize, winners, duration, up to 5 required roles, blocked
  role, min account age, min messages, host picker, prize photo
- Join / Leave / Participants buttons, live countdown embed, auto-draw timer
- `/giveaway_end`, `/giveaway_extend`, `/giveaway_reroll`, `/giveaway_cancel`
  (autocomplete + live-one fallback, no id typing)
- `/giveaway_list` (entrants + win odds), `/giveaway_ping`, `/giveaway_notifyer`
  (one-time notify-role setup, pinged on every event)
- Members Discord has timed out (native `/mute`) cannot join: the attempt is
  refused, their entry is removed, and they sit out the next 3 giveaways.
  `/giveaway_timeout_bans` lists who is currently sitting out
- Per-giveaway mentionable entrants role, granted on join, stripped on end
- Optional claim window (`claim_minutes` on create): winners press **Claim Prize**
  before a deadline or the prize is re-drawn; `/skipclaim` lets staff skip one
- Timed bans: `/giveaway_ban`, `/giveaway_unban`, `/giveaway_ban_list`
  (hours + reason), alongside the permanent blacklist commands
- `/giveaway_reroll` can replace one winner (`user:`) or draw fresh ones
- `/gw_request_time` (staff only) reports gateway/Discord latency and the Redis
  cache legs
- Message counts reset for everyone **in that server** when a giveaway ends.
  They are kept in memory and written in one batched statement every 15s, so a
  busy server does not pay a database round-trip per message
- Turso-only storage, so restarts never lose data (Redis is an optional read
  cache only)
- Built-in HTTP server, so it runs on Render's free Web Service tier:
  `/health` (liveness, always 200), `/ready` (200 only once Discord is
  connected) and an opt-in, token-protected `/metrics` (Prometheus text)
- `LOG_FORMAT=json` for one-JSON-object-per-line logs

Details: [bot/README.md](bot/README.md).

## Dashboard (website + API, Vercel)

Read-only view over the **same Turso DB** the bot writes. Shows only
**live + previous** giveaways, 4 fields per card: prize, entrants, win chance, timer.
No framework or bundler — `public/` is baked into `api/page.js`; `api/` runs as Node 20
functions (`framework: null` in `vercel.json` forces the "Other" preset, so
Vercel never asks for Next.js).

| Route | What |
| --- | --- |
| `GET /` | the dashboard page |
| `GET /api/health` | `{ ok, now }` — also verifies Turso `SELECT 1` |
| `GET /api/giveaways` | `{ now, live: [...], previous: [...] }` |
| `GET /api/giveaways?id=gw_xxx` | `{ now, giveaway: {...} }` — single card |

Query params: `guild_id` (optional filter), `previous_limit` (1–10, default 5).

Privacy: usernames only, capped at 100 — **user ids never leave the DB**.
Entry rows are wiped 5h after end by the bot. Finished giveaways keep
their final `entrants.count`; older rows without a stored count use live entries.

Behaviour notes:

- Countdown ticks locally every second from `timer.ends_at`, corrected by the
  server clock offset in the response `now`. Under 60s the timer turns amber.
- Refreshes every 15s, on tab focus, and on the Refresh button.
- Light/dark follows the OS setting; the ticking clock is screen-reader-safe
  (`role="timer"`, absolute `<time>` label); all untrusted text is escaped.

Local preview (no Turso credentials needed — the API is mocked):

```bash
cd dashboard
npm i
node serve.js      # http://localhost:4321
node smoke.js      # helper + card-shape tests
```

The page is served by `api/page.js`, which has the `public/` files baked in —
after editing anything in `public/`, run `node build-page.js` (from
`dashboard/`) before committing, or the live page won't pick up the change.

`/admin` (linked in the page footer) is a password-gated panel for clearing
finished giveaways. Live ones can't be deleted there — end them in Discord
first. It pages through the whole table (the public API only returns the last
10), with search, status filters, multi-select and bulk delete behind a
confirm dialog. Configure `ADMIN_PASSWORD` before deploying; without a nonempty
value every admin API request returns 503. The local `node serve.js` mock uses
`ADMIN_PASSWORD` if supplied, otherwise a non-secret preview-only password
(`local-preview-only`) that is not used by the deployed API.

Login sends the password once (`action: "login"`) and gets back a signed token
that lasts 8 hours; every other admin call needs `Authorization: Bearer <token>`,
and the browser keeps only the token (in `sessionStorage`). Changing
`ADMIN_PASSWORD` signs every session out, unless you pin the signing key with
`ADMIN_SESSION_SECRET`. After 5 wrong passwords in 15 minutes a client is
locked out for 15 minutes (HTTP 429 with `Retry-After`). Attempts are counted
per client in the `admin_login_attempts` table in the same Turso database
(created automatically, only a salted hash of the address is stored). There is
no global cap, so keep `ADMIN_PASSWORD` long and unique.

Deploy: import the repo on Vercel with **Root Directory = `dashboard`**,
set env vars `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` (same as the bot)
and a strong, unique `ADMIN_PASSWORD` (required for admin access), then deploy.

## Quick start

```bash
python -m pip install -r requirements.txt
python -m giveaway_bot doctor   # from bot/
python -m giveaway_bot run      # or: python app.py (from root)
```

## Tests

Both suites use the standard test runners (`unittest` and plain Node); the bot
tests need the optional extras CI installs (`pip install -e "bot[turso,dev,redis]"`,
which brings `ruff` for linting). See [TESTING.md](TESTING.md).

```bash
cd bot       && python -W error::RuntimeWarning -m unittest discover -s tests -t .
cd dashboard && node smoke.js                              # page, API, admin, browser
```

The Python suite hands `Database` an in-memory SQLite connection, so the real
schema, constraints and upserts are exercised without a Turso account.

## Env

| Var | Required | What |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | yes | Bot token (`DISCORD_TOKEN` is accepted as a fallback name) |
| `TURSO_DATABASE_URL` | yes | `libsql://...` — the only database |
| `TURSO_AUTH_TOKEN` | yes | Turso auth token |
| `ADMIN_PASSWORD` | dashboard admin | Required to enable `/api/admin`; unset/empty disables it (503) |
| `ADMIN_SESSION_SECRET` | no | Dashboard only. Pins the key that signs admin session tokens; by default it is derived from `ADMIN_PASSWORD` |
| `DISCORD_GIVEAWAY_CHANNEL_ID` | no | Force all giveaways into one channel |
| `REDIS_URL` | no | Optional short-TTL read cache. Empty = every read goes to Turso |
| `REDIS_ENABLED` | no | `true`/`false` (default `true`); switches the cache off without clearing the URL |
| `LOG_FORMAT` | no | `text` (default) or `json` |
| `LOG_LEVEL` | no | `DEBUG`, `INFO` (default), `WARNING`, `ERROR`, `CRITICAL` |
| `METRICS_TOKEN` | no | Enables `GET /metrics` (send `Authorization: Bearer <token>`); empty = no route |
| `TICK_SECONDS` | no | Embed refresh + due checks (default 30) |
| `EMBED_COLOR` | no | e.g. `0x7C5CFF`, `#7C5CFF` or `7C5CFF`; masked to 24 bits |
| `DASHBOARD_URL` | no | Blue "Dashboard" link button on every giveaway message (empty = no button) |
| `PORT` | no | Health server port (Render sets it; default 10000) |

The bot needs **Server Members Intent** (Developer Portal → Bot) for role checks
and **Manage Roles** with its role above the ones it manages. On Render free
tier, ping `/health` from UptimeRobot every 5 minutes to stop it sleeping.

## License

MIT — see [LICENSE](LICENSE).
