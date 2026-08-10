/**
 * "Universal" proxy input: accepts a proxy string in whichever shape the
 * analyst's provider actually handed them, and normalizes it into the
 * {server, username, password} shape Playwright's own context-launch
 * option expects (see backend/stealth/proxy.py::build_proxy_config, which
 * passes `server` straight through unvalidated -- backend/sessions/
 * manager.py::_validate_proxy is the server-side backstop for whatever
 * gets past this).
 *
 * Every proxy-list format an analyst is realistically handed reduces to one
 * of these:
 *   host:port
 *   host:port:username:password        (the common bulk-proxy-list shape)
 *   username:password@host:port
 *   scheme://host:port
 *   scheme://username:password@host:port
 *
 * `scheme` defaults to "http" when omitted -- most proxy providers serve
 * plain HTTP(S) proxies, and Playwright's `server` option requires SOME
 * scheme, so a bare `host:port` can't be passed through as-is.
 */

export interface ParsedProxy {
  server: string; // "scheme://host:port" -- exactly what Playwright wants
  username?: string;
  password?: string;
}

export const ALLOWED_PROXY_SCHEMES = ["http", "https", "socks4", "socks5", "socks5h"] as const;
const ALLOWED_SCHEME_SET = new Set<string>(ALLOWED_PROXY_SCHEMES);
const DEFAULT_SCHEME = "http";

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s; // not percent-encoded -- use it literally rather than throwing
  }
}

/** "host:port" (nothing else) -> "host:port" with the port range-checked,
 * or null. Deliberately simple (no IPv6 bracket support) -- proxy list
 * providers essentially never hand out IPv6 endpoints; a value that needs
 * it can still be entered via the explicit scheme:// form with brackets,
 * which this function is not responsible for validating port-wise. */
function parseHostPort(raw: string): string | null {
  const s = raw.trim().replace(/\/+$/, "");
  const m = s.match(/^([^:\s@]+):(\d{1,5})$/);
  if (!m) return null;
  const port = Number(m[2]);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  return `${m[1]}:${port}`;
}

function splitAuth(authPart: string): { username: string; password?: string } | null {
  const idx = authPart.indexOf(":");
  if (idx < 0) {
    const username = safeDecode(authPart);
    return username ? { username } : null;
  }
  const username = safeDecode(authPart.slice(0, idx));
  const password = safeDecode(authPart.slice(idx + 1));
  return username ? { username, password: password || undefined } : null;
}

/**
 * Parses ANY of the supported shapes (see module docstring). Returns null
 * for anything that doesn't match one of them -- callers should show
 * `PROXY_FORMAT_EXAMPLES` as a hint rather than guessing at a partial parse.
 */
export function parseProxyString(raw: string): ParsedProxy | null {
  const s = (raw || "").trim();
  if (!s) return null;

  // scheme://[user[:pass]@]host:port
  const schemeMatch = s.match(/^([a-zA-Z][a-zA-Z0-9+.-]*):\/\/(.+)$/);
  if (schemeMatch) {
    const scheme = schemeMatch[1].toLowerCase();
    if (!ALLOWED_SCHEME_SET.has(scheme)) return null;
    const rest = schemeMatch[2];
    const atIdx = rest.lastIndexOf("@");
    const authPart = atIdx >= 0 ? rest.slice(0, atIdx) : "";
    const hostPart = atIdx >= 0 ? rest.slice(atIdx + 1) : rest;
    const hostPort = parseHostPort(hostPart);
    if (!hostPort) return null;
    const result: ParsedProxy = { server: `${scheme}://${hostPort}` };
    if (authPart) {
      const auth = splitAuth(authPart);
      if (!auth) return null;
      result.username = auth.username;
      if (auth.password) result.password = auth.password;
    }
    return result;
  }

  // username[:password]@host:port  (scheme omitted -> defaults to http)
  const atIdx = s.lastIndexOf("@");
  if (atIdx >= 0) {
    const authPart = s.slice(0, atIdx);
    const hostPart = s.slice(atIdx + 1);
    const hostPort = parseHostPort(hostPart);
    if (!hostPort) return null;
    const auth = splitAuth(authPart);
    if (!auth) return null;
    const result: ParsedProxy = { server: `${DEFAULT_SCHEME}://${hostPort}`, username: auth.username };
    if (auth.password) result.password = auth.password;
    return result;
  }

  // host:port  OR  host:port:username:password  (colon-separated, no @ or ://)
  const parts = s.split(":");
  if (parts.length === 2) {
    const hostPort = parseHostPort(s);
    return hostPort ? { server: `${DEFAULT_SCHEME}://${hostPort}` } : null;
  }
  if (parts.length === 4) {
    const [host, port, username, password] = parts;
    const hostPort = parseHostPort(`${host}:${port}`);
    if (!hostPort || !username.trim()) return null;
    const result: ParsedProxy = { server: `${DEFAULT_SCHEME}://${hostPort}`, username: username.trim() };
    if (password.trim()) result.password = password.trim();
    return result;
  }

  return null;
}

/** Example strings for the tab's format-help panel, one per supported shape. */
export const PROXY_FORMAT_EXAMPLES: { label: string; example: string }[] = [
  { label: "Host and port", example: "203.0.113.10:8080" },
  { label: "Host, port, and credentials", example: "203.0.113.10:8080:myuser:mypass" },
  { label: "Credentials before the host", example: "myuser:mypass@203.0.113.10:8080" },
  { label: "Explicit scheme", example: "socks5://203.0.113.10:1080" },
  { label: "Explicit scheme with credentials", example: "http://myuser:mypass@203.0.113.10:8080" },
];

/** A short human-readable summary of what parsed, for a confirmation line
 * under the input ("Facebook-style hosts, socks5, port 1080, auth: myuser").
 * Never includes the password. */
export function describeParsedProxy(p: ParsedProxy): string {
  const scheme = p.server.split("://")[0];
  const hostPort = p.server.split("://")[1] || "";
  const auth = p.username ? `, auth: ${p.username}` : ", no auth";
  return `${scheme} · ${hostPort}${auth}`;
}
