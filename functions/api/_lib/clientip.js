/* functions/api/_lib/clientip.js — who is asking: the rate-limit key and the origin pin.
 * Spec: live-sim-demo.md §4.1 (per-IP windows), §4.3 (the origin pin). */

/**
 * Normalize an address into a RATE-LIMIT KEY — not a canonical IP.
 *
 * IPv4 gives a subscriber one address, so `ip -> bucket` is `person -> bucket`. IPv6 does
 * not: a subscriber is delegated a whole prefix, so keyed on the raw string one visitor
 * could rotate source addresses per request and make every per-IP window infinite. The key
 * is therefore the /56 (three hextets and the high byte of the fourth), the prefix a
 * residential line is commonly delegated: keyed by the /64, one such line held 256 buckets,
 * and the hermetic grief simulation spent a colo's whole day budget from two /64s of one
 * /56. Coarser than an address, so a household — or neighbours who share a /56 — share a
 * bucket, exactly as IPv4 NAT already does; that is the conservative direction.
 *
 *   `203.0.113.9`              IPv4                  -> unchanged
 *   `1.2.3.4:5678`             IPv4 with a port      -> `1.2.3.4`
 *   `2001:db8:1:2:3:4:5:6`     full IPv6             -> `2001:db8:1:0::/56`
 *   `2001:db8:1:2ff:3:4:5:6`   the same /56          -> `2001:db8:1:200::/56`
 *   `2001:db8::1`              elided IPv6           -> `2001:db8:0:0::/56`
 *   `::1`                      loopback              -> `0:0:0:0::/56`
 *   `fe80::1%eth0`             a zone index          -> `fe80:0:0:0::/56`
 *   `[2001:db8::1]:443`        bracketed, with port  -> `2001:db8:0:0::/56`
 *   `::ffff:1.2.3.4`           IPv4-MAPPED           -> `1.2.3.4`   (NOT a /56)
 *   `::ffff:102:304`           the same address      -> `1.2.3.4`
 *
 * The IPv4-mapped rows matter most: every mapped address shares the `0:0:0:ffff` prefix,
 * so truncating would collapse the whole IPv4 internet into one bucket. They are unmapped
 * back to v4 instead. Anything with `:` that does not parse keys as `unknown` — a malformed
 * address is a bug or a forgery and has not earned a bucket of its own.
 */
export function ipKey(raw) {
  let s = String(raw == null ? "" : raw).trim();
  if (!s) return "unknown";
  if (!s.includes(":")) return s; // plain IPv4 or a hostname: keyed as-is

  // IPv4 with a port. Not tried on bare IPv6, where a trailing `:n` is a hextet.
  const v4port = /^(\d{1,3}(?:\.\d{1,3}){3}):\d{1,5}$/.exec(s);
  if (v4port) return v4port[1];

  if (s.startsWith("[")) { // `[addr]` or `[addr]:port`
    const close = s.indexOf("]");
    if (close < 0) return "unknown";
    s = s.slice(1, close);
  }
  // A zone index names an interface on the RECEIVING host; it is not the sender's identity.
  const pct = s.indexOf("%");
  if (pct >= 0) s = s.slice(0, pct);

  const groups = expandV6(s);
  if (!groups) return "unknown";

  if (groups[0] === 0 && groups[1] === 0 && groups[2] === 0 && groups[3] === 0 &&
      groups[4] === 0 && groups[5] === 0xffff) { // IPv4-mapped: unmap, do not truncate
    return [groups[6] >> 8, groups[6] & 0xff, groups[7] >> 8, groups[7] & 0xff].join(".");
  }
  // The /56: three hextets, and the fourth with its low byte cleared. Lower-case hex, no
  // leading zeros, a fixed `::/56` tail — one address has exactly one key, and no IPv4 key
  // (dotted) or the `unknown` bucket can spell one.
  return [groups[0], groups[1], groups[2], groups[3] & 0xff00].map((g) => g.toString(16)).join(":") + "::/56";
}

/** Expand an IPv6 literal to eight 16-bit groups, or `null`. Hand-written rather than
 *  delegated to `new URL()`, whose normalisation differs across runtimes — a rate-limit
 *  key must not change when workerd updates. */
function expandV6(input) {
  const s = String(input).toLowerCase();
  if (!s || s.length > 45 || !/^[0-9a-f:.]+$/.test(s)) return null;
  if (s.indexOf(":::") >= 0) return null;

  const halves = s.split("::");
  if (halves.length > 2) return null; // at most ONE elision, RFC 4291 §2.2
  const elided = halves.length === 2;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = elided ? (halves[1] ? halves[1].split(":") : []) : [];
  if (!elided && head.length !== 8) return null;

  const parts = head.concat(tail);
  const out = [];
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (p.indexOf(".") >= 0) {
      // A dotted quad is legal only as the LAST element, where it stands for two hextets.
      if (i !== parts.length - 1) return null;
      const q = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(p);
      if (!q) return null;
      const b = [Number(q[1]), Number(q[2]), Number(q[3]), Number(q[4])];
      if (b.some((n) => n > 255)) return null;
      out.push((b[0] << 8) | b[1], (b[2] << 8) | b[3]);
      continue;
    }
    if (!/^[0-9a-f]{1,4}$/.test(p)) return null;
    out.push(parseInt(p, 16));
  }
  if (out.length > 8) return null;
  if (!elided) return out.length === 8 ? out : null;

  // Splice the zeros back in at the elision. `::` must stand for AT LEAST one group.
  const headLen = head.reduce((n, p) => n + (p.indexOf(".") >= 0 ? 2 : 1), 0);
  const fill = 8 - out.length;
  if (fill < 1) return null;
  return out.slice(0, headLen).concat(new Array(fill).fill(0), out.slice(headLen));
}

/**
 * The visitor's IP, as a rate-limit key (§4.1).
 *
 * `CF-Connecting-IP` is the source of truth: Cloudflare overwrites whatever the client sent.
 * `X-Forwarded-For` is client-writable, so trusting it lets one process rotate the header
 * and hold unlimited buckets; it is honoured only with an explicit `DEMO_TRUST_XFF`, which
 * MUST STAY UNSET IN PRODUCTION (a proxy or tunnel in front would silently open the hole).
 *
 * Everyone unidentifiable shares ONE `unknown` bucket, on purpose: they are throttled as a
 * group rather than each handed a free lane.
 *
 * @param {Request} request
 * @param {{trustXff?: boolean}} [cfg] absent ⇒ XFF is not trusted.
 */
export function clientIp(request, cfg) {
  const h = request && request.headers;
  if (!h) return "unknown";
  const cf = h.get("CF-Connecting-IP");
  if (cf) return ipKey(cf);
  if (cfg && cfg.trustXff) {
    const xff = h.get("X-Forwarded-For");
    if (xff) return ipKey(String(xff).split(",")[0]);
  }
  return "unknown";
}

/**
 * Pin the request to this deployment's own origin (§4.3).
 *
 * **THIS STOPS BROWSER HOTLINKING ONLY. `curl` FORGES THESE HEADERS TRIVIALLY.** It is a
 * cheap first filter; nothing that bounds cost depends on it (Turnstile is the bot control).
 *
 * The default allowlist is the request's OWN origin, so a fork on any domain works with no
 * configuration and nothing here needs to know a deployment hostname. `Sec-Fetch-Site` must
 * be `same-origin` when present; absent is allowed only with a matching `Origin`/`Referer`.
 */
export function checkOrigin(request, cfg) {
  let self;
  try {
    self = new URL(request.url).origin;
  } catch {
    return { ok: false, reason: "forbidden_origin" };
  }
  const allowed = [self, ...(cfg.allowedOrigins || [])];
  const h = request.headers;
  const site = h.get("Sec-Fetch-Site");
  if (site && site !== "same-origin") return { ok: false, reason: "forbidden_origin" };

  let origin = h.get("Origin") || "";
  if (!origin) {
    const ref = h.get("Referer");
    if (ref) {
      try {
        origin = new URL(ref).origin;
      } catch {
        origin = "";
      }
    }
  }
  if (!origin) {
    // Our own page always sends `Origin` on a POST, so this is a non-browser caller —
    // allowed only when fetch metadata explicitly vouched for it.
    return site === "same-origin" ? { ok: true, reason: null } : { ok: false, reason: "forbidden_origin" };
  }
  return allowed.includes(origin) ? { ok: true, reason: null } : { ok: false, reason: "forbidden_origin" };
}
