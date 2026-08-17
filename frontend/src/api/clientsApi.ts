// API calls for the backend's clients module (backend/api/client_routes.py).
// A client's org id/name/domain/keyword config is curated up front here,
// discovery no longer upserts a client as a side effect (see discoveryApi.ts).
import { json, post, url } from "./httpClient";
import type { Client } from "./types";

export const clientsApi = {
  listClients: () => fetch(url("/clients")).then(json<{ items: Client[] }>),
  upsertClient: (body: {
    client_id: string;
    name: string;
    domain?: string;

    name_keywords?: string[];
    domain_keywords?: string[];
    asset_name_individual_keywords?: string[];
    asset_name_domain_keywords?: string[];
    platform_limits_individual?: Record<string, number>;
    platform_limits_domain?: Record<string, number>;
    // { [platform]: { [tab]: { [keywordType]: number } } }, e.g.
    // { facebook: { people: { individual: 5, domain: 20 }, ... } }
    platform_tab_limits?: Record<string, Record<string, Record<string, number>>>;
    cron?: string | null;
  }) =>
    post("/clients", {
      domain: "",

      name_keywords: [],
      domain_keywords: [],
      asset_name_individual_keywords: [],
      asset_name_domain_keywords: [],
      platform_limits_individual: {},
      platform_limits_domain: {},
      platform_tab_limits: {},
      ...body,
    }).then(json<Client>),
  getClient: (clientId: string) => fetch(url(`/clients/${encodeURIComponent(clientId)}`)).then(json<Client>),
  deleteClient: (clientId: string) =>
    fetch(url(`/clients/${encodeURIComponent(clientId)}`), { method: "DELETE" }).then(
      json<
        Client & {
          deleted_profiles: number; deleted_incidents: number;
          deleted_published_incidents: number; deleted_evidence: number;
        }
      >,
    ),
};
