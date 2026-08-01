// The backend has no "list all clients" route (only upsert-one / get-one-by-
// id, see backend/api/routes_clients.py) -- a client picker has nothing
// server-side to enumerate. This is a purely local, best-effort convenience
// list of clients this browser has used before, not an authoritative source.

export interface RecentClient {
  client_id: string;
  name: string;
}

const KEY = "bi_recent_clients";
const MAX = 8;

export function loadRecentClients(): RecentClient[] {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as RecentClient[]) : [];
  } catch {
    return [];
  }
}

export function rememberClient(client: RecentClient): RecentClient[] {
  const existing = loadRecentClients().filter((c) => c.client_id !== client.client_id);
  const next = [client, ...existing].slice(0, MAX);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // storage unavailable (private mode, quota) -- the list just won't persist
  }
  return next;
}

export function forgetClient(clientId: string): RecentClient[] {
  const next = loadRecentClients().filter((c) => c.client_id !== clientId);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // ignore
  }
  return next;
}
