// API calls for the backend's clients module (backend/api/client_routes.py).
import { json, post, url } from "./httpClient";
import type { Client } from "./types";

// No GET /clients list route exists on this backend -- only upsert-one and
// fetch-one-by-id. A client picker has nothing server-side to enumerate;
// callers are expected to know their own client_id (their own SaaS's
// customer/org id), same as the caller this engine was actually built for.
export const clientsApi = {
  upsertClient: (body: { client_id: string; name: string; keywords?: string[]; cron?: string | null }) =>
    post("/clients", { keywords: [], ...body }).then(json<Client>),
  getClient: (clientId: string) =>
    fetch(url(`/clients/${encodeURIComponent(clientId)}`)).then(json<Client>),
};
