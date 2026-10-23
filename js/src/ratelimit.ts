import type { RateLimitInfo } from "./types.js";

type Params = Record<string, string | number | boolean>;
interface Item {
  name: string;
  params: Params;
}

// Minimal parser for the structured-field lists used by the IETF RateLimit
// headers: `"pending";q=100;qu="concurrent-requests"`, possibly several
// comma-separated items. Lenient: anything unparseable is skipped.
function parseList(header: string | null): Item[] {
  if (!header) return [];
  const items: Item[] = [];
  for (const raw of splitOutsideQuotes(header, ",")) {
    const [first, ...rest] = splitOutsideQuotes(raw, ";").map((s) => s.trim());
    if (!first) continue;
    const params: Params = {};
    for (const p of rest) {
      const eq = p.indexOf("=");
      const key = (eq === -1 ? p : p.slice(0, eq)).trim();
      if (!key) continue;
      params[key] = eq === -1 ? true : parseValue(p.slice(eq + 1).trim());
    }
    items.push({ name: String(parseValue(first)), params });
  }
  return items;
}

function splitOutsideQuotes(s: string, sep: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]!;
    if (ch === "\\" && quoted && i + 1 < s.length) {
      cur += ch + s[++i];
      continue;
    }
    if (ch === '"') quoted = !quoted;
    if (ch === sep && !quoted) {
      out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

function parseValue(v: string): string | number {
  if (v.startsWith('"') && v.endsWith('"') && v.length >= 2) {
    return v.slice(1, -1).replace(/\\(.)/g, "$1");
  }
  const n = Number(v);
  return v !== "" && Number.isFinite(n) ? n : v;
}

const num = (v: unknown): number | undefined => (typeof v === "number" ? v : undefined);

/**
 * Parse the IETF `RateLimit-Policy` and `RateLimit` response headers. Returns
 * `undefined` when neither is present.
 */
export function parseRateLimitHeaders(headers: Headers): RateLimitInfo | undefined {
  const policies = parseList(headers.get("ratelimit-policy"));
  const limits = parseList(headers.get("ratelimit"));
  const current = limits[0];
  const policy = (current && policies.find((p) => p.name === current.name)) ?? policies[0];
  if (!current && !policy) return undefined;

  const info: RateLimitInfo = { policy: current?.name ?? policy!.name };
  const limit = num(policy?.params.q);
  const remaining = num(current?.params.r);
  const reset = num(current?.params.t);
  const unit = policy?.params.qu;
  if (limit !== undefined) info.limit = limit;
  if (remaining !== undefined) info.remaining = remaining;
  if (reset !== undefined) info.resetSeconds = reset;
  if (typeof unit === "string") info.quotaUnit = unit;
  return info;
}

/** `Retry-After` as seconds (accepts delta-seconds or an HTTP date). */
export function parseRetryAfter(value: string | null, now = Date.now()): number | undefined {
  if (!value) return undefined;
  const trimmed = value.trim();
  if (/^\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
  const date = Date.parse(trimmed);
  if (Number.isNaN(date)) return undefined;
  return Math.max(0, Math.ceil((date - now) / 1000));
}
