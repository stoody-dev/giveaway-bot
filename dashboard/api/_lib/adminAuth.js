import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { db } from "./turso.js";

/** How long a login lasts. The panel asks for the password again after this. */
export const SESSION_TTL_MS = 8 * 60 * 60 * 1000;

/** Failed logins allowed per client inside WINDOW_MS before it is locked out. */
export const MAX_FAILS = 5;
export const WINDOW_MS = 15 * 60 * 1000;
export const LOCK_MS = 15 * 60 * 1000;

/**
 * Key that signs session tokens.
 *
 * ADMIN_SESSION_SECRET wins when set. Otherwise the key is derived from
 * ADMIN_PASSWORD, so changing the password also signs every open session out.
 */
function secret() {
  const explicit = process.env.ADMIN_SESSION_SECRET;
  if (explicit) return explicit;
  return createHash("sha256")
    .update(`gw-admin-session:${process.env.ADMIN_PASSWORD ?? ""}`)
    .digest("hex");
}

function sign(body) {
  return createHmac("sha256", secret()).update(body).digest("base64url");
}

/** A signed, expiring session token: base64url(payload).base64url(hmac). */
export function signToken(now = Date.now()) {
  const body = Buffer.from(JSON.stringify({ iat: now, exp: now + SESSION_TTL_MS })).toString("base64url");
  return `${body}.${sign(body)}`;
}

/** True only for a token this server signed that has not expired. */
export function verifyToken(token, now = Date.now()) {
  if (typeof token !== "string") return false;
  const parts = token.split(".");
  if (parts.length !== 2) return false;
  const [body, sig] = parts;
  const expected = Buffer.from(sign(body));
  const given = Buffer.from(sig);
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) return false;
  try {
    const { exp } = JSON.parse(Buffer.from(body, "base64url").toString("utf8"));
    return Number.isFinite(exp) && exp > now;
  } catch {
    return false;
  }
}

/** The token from an `Authorization: Bearer <token>` header, or "". */
export function bearerToken(req) {
  const header = req.headers?.authorization ?? req.headers?.Authorization ?? "";
  const match = /^Bearer\s+(\S+)\s*$/i.exec(String(header));
  return match ? match[1] : "";
}

/**
 * Stable per-client key for the lockout table. Only a salted hash is stored,
 * never the address itself. Vercel sets x-forwarded-for to the real client
 * address, so the first hop cannot be chosen by the caller.
 */
export function clientKey(req) {
  const forwarded = String(req.headers?.["x-forwarded-for"] ?? "").split(",")[0].trim();
  const ip = forwarded || String(req.headers?.["x-real-ip"] ?? "").trim() || "unknown";
  return createHash("sha256").update(`${secret()}|${ip}`).digest("hex");
}

let tableReady = null;

/** Create the attempts table once per serverless instance. The bot never touches it. */
function ensureTable() {
  tableReady ??= db()
    .execute(
      `CREATE TABLE IF NOT EXISTS admin_login_attempts (
         ip_hash TEXT PRIMARY KEY,
         fails INTEGER NOT NULL,
         window_start INTEGER NOT NULL,
         locked_until INTEGER NOT NULL
       )`,
    )
    .catch((err) => {
      tableReady = null;
      throw err;
    });
  return tableReady;
}

/** Milliseconds this client must still wait, or 0 when it may try. */
export async function lockedFor(req, now = Date.now()) {
  await ensureTable();
  const rs = await db().execute({
    sql: "SELECT locked_until FROM admin_login_attempts WHERE ip_hash = ?",
    args: [clientKey(req)],
  });
  const until = Number(rs.rows?.[0]?.locked_until ?? 0) || 0;
  return until > now ? until - now : 0;
}

/**
 * Count one failed login and lock the client when it reaches MAX_FAILS inside
 * the window. One upsert, so concurrent failures cannot undercount. Returns the
 * wait in ms when this failure caused a lock, else 0.
 */
export async function recordFailure(req, now = Date.now()) {
  await ensureTable();
  const key = clientKey(req);
  // A fresh window starts when the old one ran out or a lock has expired.
  const fresh = "(? - window_start > ? OR (locked_until != 0 AND locked_until <= ?))";
  const freshArgs = [now, WINDOW_MS, now];
  await db().execute({
    sql: `INSERT INTO admin_login_attempts (ip_hash, fails, window_start, locked_until)
          VALUES (?, 1, ?, 0)
          ON CONFLICT(ip_hash) DO UPDATE SET
            fails = CASE WHEN ${fresh} THEN 1 ELSE fails + 1 END,
            window_start = CASE WHEN ${fresh} THEN ? ELSE window_start END,
            locked_until = CASE WHEN ${fresh} THEN 0
                                WHEN fails + 1 >= ? THEN ?
                                ELSE locked_until END`,
    args: [
      key, now,
      ...freshArgs,
      ...freshArgs, now,
      ...freshArgs, MAX_FAILS, now + LOCK_MS,
    ],
  });
  return lockedFor(req, now);
}

/** A good login clears the client's failures. */
export async function recordSuccess(req) {
  await ensureTable();
  await db().execute({
    sql: "DELETE FROM admin_login_attempts WHERE ip_hash = ?",
    args: [clientKey(req)],
  });
}

/** Test hook: forget that the table was created (the in-memory DB is recreated). */
export function resetForTests() {
  tableReady = null;
}
