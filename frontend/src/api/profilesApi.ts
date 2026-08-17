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
    // loaded, while total/pagination still came from the unfiltered query,
    // so filtering across 500 rows showed only the matches inside page 1
    // and the pager still said 500.
    priority?: string;
    match_level?: "high" | "medium" | "low" | "";
    keyword_match_type?: "individual" | "domain" | "";
    search?: string;
    // Analysis-phase Published/Unpublished tab. Omitted -> both.
    published?: boolean;
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
    if (q.published !== undefined) p.set("published", String(q.published));
    p.set("limit", String(q.limit ?? 100));
    p.set("offset", String(q.offset ?? 0));
    // This is the analyst tool, always see a freshly analysed result
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
  // "did we actually check everything?", see backend profile_service.coverage
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
  publishAllProfiles: (client_id: string, platform?: string, mode: "all" | "recent" | "2days" | "week" = "all") =>
    fetch(url("/profiles/publish-all"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id, platform: platform || null, mode }),
    }).then(json<{ published: number }>),
  // No logo/username arguments: validating a profile IS the match
  // confirmation, applied server-side (backend scoring.resolve_match), and
  // corrections are made in the analysis view per profile.
  bulkPatch: (profileIds: string[], status: Status) =>
    post("/profiles/bulk-patch", { profile_ids: profileIds, status }).then(
      json<{ succeeded: string[]; failed: string[] }>,
    ),
  // Hard delete, irreversible, unlike a status patch. See
  // backend/services/profile_service.py::delete_profiles.
  deleteProfiles: (profileIds: string[]) =>
    post("/profiles/bulk-delete", { profile_ids: profileIds }).then(
      json<{ deleted: number; requested: number }>,
    ),
  // Hard delete of every profile, evidence screenshot, and published
  // incident for one client + platform (both Discovery and Analysis phase
  // rows), irreversible. See
  // backend/services/profile_service.py::delete_for_client_platform.
  deletePlatformData: (clientId: string, platform: string) =>
    post("/profiles/delete-platform-data", { client_id: clientId, platform }).then(
      json<{ deleted_profiles: number; deleted_evidence: number; deleted_published_incidents: number }>,
    ),
  exportXlsx: (filename: string, rows: Record<string, unknown>[]) =>
    post("/profiles/export-xlsx", { filename, rows }).then(blob),
  // A profile URL an analyst already has in hand (a tip, a report, a link
  // an earlier sweep never turned up), created straight at "approved"
  // and auto-queued for analysis, no keyword search needed. See
  // backend/services/profile_service.py::add_manual_urls.
  //
  // `individualUrls`/`domainUrls` are the Executive/Domain boxes on the Add
  // URLs modal, explicit intent, so a URL for an executive not yet in the
  // client's own configured keywords still lands in the individual bucket
  // instead of silently publishing as Brand Infringement (see the backend
  // docstring). `urls` is the untyped legacy path (best-effort keyword
  // match); all three may be sent together.
  addManualUrls: (client_id: string, opts: { urls?: string[]; individualUrls?: string[]; domainUrls?: string[] }) =>
    post("/profiles/add-urls", {
      client_id,
      urls: opts.urls ?? [],
      individual_urls: opts.individualUrls ?? [],
      domain_urls: opts.domainUrls ?? [],
    }).then(
      json<{ added: number; by_platform: Record<string, number>; skipped: string[] }>,
    ),
};
