// API calls for the backend's profiles module (backend/api/profile_routes.py).
import { json, url } from "./httpClient";
import type { Profile, ProfilePatch } from "./types";

export const profilesApi = {
  profiles: (q: {
    client_id: string;
    status?: string;
    phase?: string;
    platform?: string;
    limit?: number;
    offset?: number;
  }) => {
    const p = new URLSearchParams({ client_id: q.client_id });
    if (q.status) p.set("status", q.status);
    if (q.phase) p.set("phase", q.phase);
    if (q.platform) p.set("platform", q.platform);
    p.set("limit", String(q.limit ?? 100));
    p.set("offset", String(q.offset ?? 0));
    return fetch(url(`/profiles?${p}`)).then(json<{ items: Profile[]; total: number }>);
  },
  profile: (id: string) => fetch(url(`/profiles/${id}`)).then(json<Profile>),
  patchProfile: (id: string, fields: ProfilePatch) =>
    fetch(url(`/profiles/${id}`), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    }).then(json<Profile>),
};
