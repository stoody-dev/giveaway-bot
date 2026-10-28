// Offline endpoint regressions using libSQL's in-memory test database.
import assert from "node:assert/strict";
import admin from "./api/admin.js";
import giveaways from "./api/giveaways.js";
import health from "./api/health.js";
import { db } from "./api/_lib/turso.js";
import {
  LOCK_MS, MAX_FAILS, SESSION_TTL_MS, resetForTests, signToken, verifyToken,
} from "./api/_lib/adminAuth.js";

const saved = {
  password: process.env.ADMIN_PASSWORD,
  url: process.env.TURSO_DATABASE_URL,
  token: process.env.TURSO_AUTH_TOKEN,
};
const restore = (key, value) => {
  if (value === undefined) delete process.env[key];
  else process.env[key] = value;
};

async function request(handler, method = "GET", body = {}, query = {}, headers = {}) {
  const res = {
    headers: {}, statusCode: 0, body: null,
    setHeader(key, value) { this.headers[key.toLowerCase()] = value; },
    status(code) { this.statusCode = code; return this; },
    json(value) { this.body = value; return this; },
    end() { return this; },
  };
  await handler({ method, body, query, headers }, res);
  return res;
}
// Every admin action except login needs a signed session token.
const authed = () => ({ authorization: `Bearer ${signToken()}` });
const post = (body) => request(admin, "POST", body, {}, authed());
const login = (password, ip = "203.0.113.7") =>
  request(admin, "POST", { action: "login", password }, {}, { "x-forwarded-for": ip });

try {
  delete process.env.ADMIN_PASSWORD;
  delete process.env.ADMIN_SESSION_SECRET;
  delete process.env.TURSO_DATABASE_URL;
  delete process.env.TURSO_AUTH_TOKEN;
  for (const method of ["POST", "OPTIONS", "GET"]) {
    const disabled = await request(admin, method, { action: "ping" });
    assert.equal(disabled.statusCode, 503, "admin is disabled without a password");
    assert.deepEqual(disabled.body, { error: "Admin disabled: ADMIN_PASSWORD not set" });
  }
  process.env.ADMIN_PASSWORD = "smoke-test-only";

  // Session tokens: signed, expiring, tied to the password.
  assert.equal((await post({ action: "ping" })).statusCode, 200, "valid token accepted");
  const good = signToken();
  assert.equal(verifyToken(good), true);
  assert.equal(verifyToken(signToken(Date.now() - SESSION_TTL_MS - 1000)), false, "expired token rejected");
  const [payload, sig] = good.split(".");
  const flipped = sig.slice(0, -1) + (sig.endsWith("A") ? "B" : "A");
  assert.equal(verifyToken(`${payload}.${flipped}`), false, "tampered signature rejected");
  const forgedBody = Buffer.from(JSON.stringify({ iat: 0, exp: Date.now() + 1e9 })).toString("base64url");
  assert.equal(verifyToken(`${forgedBody}.${sig}`), false, "tampered payload rejected");
  for (const junk of ["", "abc", "a.b.c", undefined, null, 42, ".", `${payload}.`]) {
    assert.equal(verifyToken(junk), false, `junk token ${String(junk)}`);
  }
  for (const headers of [{}, { authorization: "Bearer nope" }, { authorization: good }, { "x-admin-password": "smoke-test-only" }]) {
    const denied = await request(admin, "POST", { action: "ping", password: "smoke-test-only" }, {}, headers);
    assert.equal(denied.statusCode, 401, "password per request no longer authorizes");
  }
  process.env.ADMIN_PASSWORD = "another-password";
  assert.equal(verifyToken(good), false, "changing the password signs sessions out");
  process.env.ADMIN_SESSION_SECRET = "pinned-secret";
  const pinned = signToken();
  process.env.ADMIN_PASSWORD = "smoke-test-only";
  assert.equal(verifyToken(pinned), true, "a pinned secret survives a password change");
  delete process.env.ADMIN_SESSION_SECRET;
  console.log("admin session tokens ok");

  // Login with no database configured: the limiter fails closed, loudly in the log only.
  const originalLoginError = console.error;
  const loginLogged = [];
  console.error = (...args) => loginLogged.push(args);
  try {
    const down = await login("smoke-test-only");
    assert.equal(down.statusCode, 503);
    assert.equal(down.body.error, "Login temporarily unavailable");
    assert.equal(loginLogged.length, 1);
  } finally {
    console.error = originalLoginError;
  }
  console.log("admin login fails closed ok");

  // Missing database configuration and internal errors never disclose details to callers.
  const originalError = console.error;
  const logged = [];
  console.error = (...args) => logged.push(args);
  try {
    for (const [handler, method, body, headers] of [
      [admin, "POST", { action: "list" }, authed()],
      [giveaways, "GET", {}],
      [health, "GET", {}],
    ]) {
      const response = await request(handler, method, body, {}, headers);
      assert.equal(response.statusCode, 500);
      assert.equal(response.body.error, "Internal server error");
    }
    assert.equal(logged.length, 3, "internal failures are logged server-side");
  } finally {
    console.error = originalError;
  }
  console.log("generic API errors ok");

  process.env.TURSO_DATABASE_URL = "file::memory:";
  const client = db();
  resetForTests();

  // Login rate limiting, per client, stored in Turso.
  const ok = await login("smoke-test-only");
  assert.equal(ok.statusCode, 200);
  assert.equal(verifyToken(ok.body.token), true, "login returns a verifiable token");
  assert.ok(ok.body.expires_at > Date.now());
  const wrongLong = await login("x".repeat(100));
  assert.equal(wrongLong.statusCode, 401, "different password lengths reject safely");
  assert.equal(wrongLong.body.error, "Wrong password");
  await login("smoke-test-only"); // success clears the failure above
  for (let n = 1; n < MAX_FAILS; n++) assert.equal((await login("bad")).statusCode, 401);
  const locking = await login("bad");
  assert.equal(locking.statusCode, 429, "the failure that reaches the limit locks the client");
  assert.ok(Number(locking.headers["retry-after"]) > 0 && Number(locking.headers["retry-after"]) <= LOCK_MS / 1000);
  const whileLocked = await login("smoke-test-only");
  assert.equal(whileLocked.statusCode, 429, "a locked client cannot log in even with the right password");
  assert.equal((await login("smoke-test-only", "198.51.100.9")).statusCode, 200, "other clients are unaffected");
  assert.equal((await post({ action: "ping" })).statusCode, 200, "an open session is not affected by a lockout");
  const stored = (await client.execute("SELECT * FROM admin_login_attempts")).rows;
  assert.ok(stored.every((r) => !JSON.stringify(r).includes("203.0.113.7")), "raw addresses are never stored");
  await client.execute({ sql: "UPDATE admin_login_attempts SET locked_until = ?", args: [Date.now() - 1] });
  assert.equal((await login("smoke-test-only")).statusCode, 200, "the lock expires");
  assert.equal((await client.execute("SELECT COUNT(*) AS n FROM admin_login_attempts WHERE fails >= 1")).rows[0].n, 0,
    "a good login resets the client's counter");
  console.log("admin login rate limiting ok");

  // Health API: 200 with no-store on GET/HEAD, 405 on POST
  const healthGet = await request(health, "GET");
  assert.equal(healthGet.statusCode, 200);
  assert.equal(healthGet.body.ok, true);
  assert.equal(healthGet.headers["cache-control"], "no-store");
  const healthHead = await request(health, "HEAD");
  assert.equal(healthHead.statusCode, 200);
  assert.equal(healthHead.headers["cache-control"], "no-store");
  const healthPost = await request(health, "POST");
  assert.equal(healthPost.statusCode, 405);
  assert.equal(healthPost.headers["cache-control"], "no-store");
  console.log("health API ok");

  await client.execute(
    "CREATE TABLE simple_giveaways (id TEXT PRIMARY KEY, guild_id TEXT, prize TEXT, " +
    "winner_count INTEGER, created_at INTEGER, ends_at INTEGER, ended_at INTEGER, " +
    "entrant_count INTEGER, status TEXT, image_url TEXT, host_name TEXT, " +
    "claim_timeout_seconds INTEGER NOT NULL DEFAULT 0)"
  );
  await client.execute(
    "CREATE TABLE simple_entries (giveaway_id TEXT, username TEXT, entered_at INTEGER)"
  );
  await client.execute(
    "CREATE TABLE simple_claims (giveaway_id TEXT NOT NULL, user_id TEXT NOT NULL, " +
    "round INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', " +
    "deadline_ms INTEGER NOT NULL DEFAULT 0, claimed_at INTEGER, skipped_by TEXT, " +
    "skipped_at INTEGER, created_at INTEGER NOT NULL DEFAULT 0, " +
    "PRIMARY KEY (giveaway_id, user_id, round))"
  );
  const now = Date.now();
  const rows = [
    ["gw_active", "active", now + 60000, null, 999],
    ["gw_stored", "ended", now - 2000, now - 2000, 12],
    ["gw_empty", "cancelled", now - 2500, now - 2500, 0],
    ["gw_fallback", "ended", now - 3000, now - 3000, null],
    ["gw_legacy", "ended", now - 4000, null, null],
    ["gw_tiea", "ended", now - 5000, now - 5000, null],
    ["gw_tieb", "ended", now - 5000, now - 5000, null],
  ];
  for (const [id, status, created, ended, entrantCount] of rows) {
    await client.execute({
      sql: "INSERT INTO simple_giveaways (id,guild_id,prize,winner_count,created_at,ends_at,ended_at,entrant_count,status) VALUES (?,?,?,?,?,?,?,?,?)",
      args: [id, "12345678901234567890", id, 2, created, created + 60000, ended, entrantCount, status],
    });
  }
  for (const [id, name] of [
    ["gw_active", "Active1"], ["gw_active", "Active2"],
    ["gw_empty", "Stale1"], ["gw_empty", "Stale2"],
    ["gw_fallback", "Old1"], ["gw_fallback", "Old2"], ["gw_fallback", "Old3"],
  ]) {
    await client.execute({ sql: "INSERT INTO simple_entries VALUES (?,?,?)", args: [id, name, now] });
  }
  for (const query of [
    { guild_id: "garbage" }, { guild_id: "1".repeat(21) }, { guild_id: "123", id: "../gw_active" },
    { id: "" }, { id: "gw_x;DROP" }, { id: ["gw_active"] }, { guild_id: ["123"] },
  ]) {
    assert.equal((await request(giveaways, "GET", {}, query)).statusCode, 400, JSON.stringify(query));
  }
  const publicList = await request(giveaways, "GET", {}, { guild_id: "12345678901234567890", previous_limit: 10 });
  assert.equal(publicList.statusCode, 200);
  assert.equal(publicList.body.live[0].entrants.count, 2, "live always uses current entries");
  assert.equal(publicList.body.previous.find((x) => x.id === "gw_stored").entrants.count, 12);
  assert.equal(publicList.body.previous.find((x) => x.id === "gw_empty").entrants.count, 0);
  assert.equal(publicList.body.previous.find((x) => x.id === "gw_fallback").entrants.count, 3);
  assert.equal(publicList.body.previous.find((x) => x.id === "gw_legacy").timer.ended_at, now - 4000);
  assert.deepEqual(publicList.body.previous.slice(-2).map((x) => x.id), ["gw_tieb", "gw_tiea"]);
  assert.equal((await request(giveaways, "GET", {}, { id: "gw_legacy" })).body.giveaway.timer.ended_at, now - 4000);
  console.log("giveaway filters, legacy ordering, archived counts ok");

  const listed = await post({ action: "list", limit: 2.9, offset: 1.9 });
  assert.equal(listed.statusCode, 200);
  assert.equal(listed.body.previous.length, 2);
  assert.deepEqual(listed.body.previous.map((x) => x.id), ["gw_empty", "gw_fallback"]);
  assert.equal(listed.body.previous[0].entrants.count, 0);
  assert.equal(listed.body.total, 6);
  assert.equal(listed.body.live, 1);
  assert.equal((await post({ action: "list", limit: Infinity, offset: Infinity })).body.previous.length, 6);
  assert.equal((await post({ action: "list", limit: 2, offset: 1e100 })).body.previous.length, 0);
  assert.equal((await post({ action: "list", limit: -10, offset: -50 })).body.previous[0].id, "gw_stored");
  assert.equal((await post({ action: "list", limit: -10, offset: -50 })).body.previous.length, 1);
  const active = await post({ action: "delete", ids: ["gw_active"] });
  assert.equal(active.statusCode, 409);
  assert.equal(active.body.deleted, 0);
  const bulk = await post({ action: "delete", ids: ["gw_fallback", "gw_active"] });
  assert.equal(bulk.statusCode, 200);
  assert.equal(bulk.body.deleted, 1);
  assert.equal(bulk.body.failed[0].id, "gw_active");
  assert.equal((await client.execute("SELECT COUNT(*) AS n FROM simple_entries WHERE giveaway_id='gw_active'")).rows[0].n, 2);
  assert.equal((await client.execute("SELECT COUNT(*) AS n FROM simple_entries WHERE giveaway_id='gw_fallback'")).rows[0].n, 0);
  assert.equal((await client.execute("SELECT COUNT(*) AS n FROM simple_giveaways WHERE id='gw_fallback'")).rows[0].n, 0);
  console.log("admin paging and transactional guarded deletion ok");
  client.close();
} finally {
  restore("ADMIN_PASSWORD", saved.password);
  restore("TURSO_DATABASE_URL", saved.url);
  restore("TURSO_AUTH_TOKEN", saved.token);
}
