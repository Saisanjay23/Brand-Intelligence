// Shared low-level HTTP plumbing every api/*Api.ts module builds on: where
// the backend lives, and the fetch/error-shape boilerplate every call needs.
//
// No Authorization header is attached today -- this backend has no auth
// layer (see backend/docs/adr/0005-no-auth-layer.md) -- but this is the one
// place it would be added if that ever changes, so every API module picks
// it up for free instead of each hand-rolling its own fetch wrapper.
//
// Empty by default (same-origin relative calls); set VITE_API_BASE_URL to
// point this app at a backend hosted elsewhere. Trailing slash stripped so
// `${API_BASE}/clients` never doubles up.
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

export const url = (path: string) => `${API_BASE}${path}`;

export async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const d = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(d.detail ?? `request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export const post = (path: string, body: unknown) =>
  fetch(url(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
