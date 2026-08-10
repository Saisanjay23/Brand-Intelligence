// API calls for the backend's profiles module (backend/api/profile_routes.py).
import { blob, json, post, url } from "./httpClient";
import type { Coverage, Profile, ProfilePatch, Status } from "./types";

export const profilesApi = {
  profiles: (q: {
    client_id: string;
    status?: string;
    phase?: string;
    platform?: string;
    keyword?: string;
    entity_type?: string;
    // Every filter below is applied by the SERVER, before limit/offset.
    // They were previously applied in the browser over whatever page was
    // loaded, while total/pagination still came from the unfiltered query --
    // so filtering across 500 rows showed only the matches inside page 1
    // and the pager still said 500.
    priority?: string;
    match_level?: "high" | "low" | "";
    keyword_match_type?: "individual" | "domain" | "";
    search?: string;
    limit?: number;
    offset?: number;
  }) => {
    const p = new URLSearchParams({ client_id: q.client_id });
    if (q.status) p.set("status", q.status);
    if (q.phase) p.set("phase", q.phase);
    if (q.platform) p.set("platform", q.platform);
    if (q.keyword) p.set("keyword", q.keyword);
    if (q.entity_type) p.set("entity_type", q.entity_type);
    if (q.priority) p.set("priority", q.priority);
    if (q.match_level) p.set("match_level", q.match_level);
    if (q.keyword_match_type) p.set("keyword_match_type", q.keyword_match_type);
    if (q.search && q.search.trim()) p.set("search", q.search.trim());
    p.set("limit", String(q.limit ?? 100));
    p.set("offset", String(q.offset ?? 0));
    // This is the analyst tool -- always see a freshly analysed result
    // even while it's on its publish hold (see backend/docs/adr/0007-publish-hold.md);
    // the client-facing default (used by anything that omits this) hides it.
    p.set("include_held", "true");
    return fetch(url(`/profiles?${p}`)).then(
      json<{
        items: Profile[];
        total: number;
        limit?: number;
        offset?: number;
        counts?: { platforms?: Record<string, number>; statuses?: Record<string, number>; keywords?: Record<string, number> };
      }>,
    );
  },
  // Absolute URL for a profile's evidence capture. The Profile carries a
  // server-relative path; this applies API_BASE so it still resolves when
  // the UI is pointed at a backend on another origin.
  screenshotUrl: (p: Profile, opts?: { download?: boolean }) =>
    p.screenshot_url ? url(p.screenshot_url) + (opts?.download ? "?download=true" : "") : "",
  // "did we actually check everything?" -- see backend profile_service.coverage
  coverage: (client_id: string, platform?: string) => {
    const p = new URLSearchParams({ client_id });
    if (platform) p.set("platform", platform);
    return fetch(url(`/profiles/coverage?${p}`)).then(json<Coverage>);
  },
  profile: (id: string) => fetch(url(`/profiles/${id}`)).then(json<Profile>),
  patchProfile: (id: string, fields: ProfilePatch) =>
    fetch(url(`/profiles/${id}`), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    }).then(json<Profile>),
  publishProfile: (id: string) =>
    fetch(url(`/profiles/${id}/publish`), { method: "POST" }).then(json<Profile>),
  publishAllProfiles: (client_id: string, platform?: string) =>
    fetch(url("/profiles/publish-all"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id, platform: platform || null }),
    }).then(json<{ published: number }>),
  bulkPatch: (profileIds: string[], status: Status, logoMatch?: boolean, usernameMatch?: boolean) =>
    post("/profiles/bulk-patch", {
      profile_ids: profileIds, status,
      logo_match: logoMatch ?? null, username_match: usernameMatch ?? null,
    }).then(json<{ succeeded: string[]; failed: string[] }>),
  exportXlsx: (filename: string, rows: Record<string, unknown>[]) =>
    post("/profiles/export-xlsx", { filename, rows }).then(blob),
  // A profile URL an analyst already has in hand (a tip, a report, a link
  // an earlier sweep never turned up) -- created straight at "approved"
  // and auto-queued for analysis, no keyword search needed. See
  // backend/services/profile_service.py::add_manual_urls.
  addManualUrls: (client_id: string, urls: string[]) =>
    post("/profiles/add-urls", { client_id, urls }).then(
      json<{ added: number; by_platform: Record<string, number>; skipped: string[] }>,
    ),
  // one aggregate query instead of firing separate list_profiles calls per
  // status/platform just to show summary counts (see DashboardView.tsx)
  stats: (client_id: string, platform?: string) => {
    const p = new URLSearchParams({ client_id });
    if (platform) p.set("platform", platform);
    return fetch(url(`/profiles/stats?${p}`)).then(
      json<{ total: number; pending: number; approved: number; rejected: number; high: number; medium: number; low: number; analysed: number }>,
    );
  },
};
