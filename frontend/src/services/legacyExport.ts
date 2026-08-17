/**
 * The tool's original raw-Profile-field export layout, reintroduced
 * alongside the newer published-incident export (services/incidentExport.ts),
 * not in place of it, as a second column set an analyst can pick between.
 * Distinct from the incident format: these are the scraped/analysed fields
 * directly (target, followers, risk_score, ...), not the client-facing
 * takedown-report shape built from them.
 *
 * A cell for data this tool doesn't have for a given row (Created Date is
 * never captured by any scraper today; Original Name/Original feed are
 * blank on the large majority of profiles) is left blank rather than
 * guessed or omitted, the column always appears, the value doesn't.
 */
import type { Profile } from "../api/types";
import { displayedRisk, logoMatchOf, riskLabel, usernameMatchOf } from "./resultsFilter";

export const LEGACY_EXPORT_COLUMNS = [
  "Original Name",
  "Original feed",
  "IMPERSONATED",
  "Profile name",
  "Created Date",
  "Logo (Yes / No)",
  "Followers",
  "Active (Yes / No)",
  "Name (Yes / No)",
  "Location",
  "Last Post (DD-MM-YYYY) (Optional)",
  "Risk Score",
  "priority",
  "Date",
  "Comments",
] as const;

export type LegacyExportColumn = (typeof LEGACY_EXPORT_COLUMNS)[number];
export type LegacyExportRow = Record<LegacyExportColumn, string>;

// "2026-07-16..." -> "16-07-2026". Passes through unrecognised/empty input
// rather than guessing, a blank date must stay blank, not become "NaN".
function toDDMMYYYY(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : iso;
}

// Yes/No/blank, a tri-state field's `null`/`undefined` means the check
// never ran, which is not the same as "confirmed No" and must not render
// as one (has_name_match, is_active are both this shape).
function triYesNo(v: boolean | null | undefined): string {
  return v === true ? "Yes" : v === false ? "No" : "";
}

export function toLegacyExportRow(r: Profile): LegacyExportRow {
  return {
    "Original Name": r.target ?? "",
    "Original feed": r.official_feed ?? "",
    IMPERSONATED: r.url ?? "",
    "Profile name": r.profile_name || r.username || "",
    // never captured by any scraper today, always blank, on purpose
    "Created Date": "",
    // Resolved exactly the way the table, the risk score and the published
    // incident resolve it (resultsFilter.ts::logoMatchOf, mirroring the
    // backend's scoring.resolve_match): an analyst's explicit call wins,
    // then a validated profile counts as matched, then the scraper's own
    // signal. Reading the raw scraper fields here used to mean a match
    // undone in the table never showed up in this export.
    "Logo (Yes / No)": triYesNo(logoMatchOf(r)),
    Followers: r.followers != null ? String(r.followers) : "",
    "Active (Yes / No)": triYesNo(r.is_active),
    "Name (Yes / No)": triYesNo(usernameMatchOf(r)),
    Location: r.location ?? "",
    "Last Post (DD-MM-YYYY) (Optional)": toDDMMYYYY(r.last_post_date),
    // Numbers only, the High/Low label goes under `priority` below
    // instead, per analyst request.
    "Risk Score": String(displayedRisk(r)),
    // The table's own Risk badge label (resultsFilter.ts::riskLabel, shared
    // with ResultsGrid.tsx::getRiskBadgeDetails so the two can't drift
    // apart), not the raw `r.priority` field, the raw field is a
    // separate, independently-computed rubric (see
    // profile_repository.py::compute_priority) that doesn't track the
    // table's visible Risk badge, which is what an analyst reading this
    // column actually expects to see.
    priority: riskLabel(r.incident?.riskRating ?? r.risk_score),
    // when this row was actually analysed, distinct from Last Post
    // (the impersonating account's own most recent activity)
    Date: toDDMMYYYY(r.analysed_at),
    Comments: r.comments ?? "",
  };
}

export function toLegacyExportRows(rows: Profile[]): LegacyExportRow[] {
  return rows.map(toLegacyExportRow);
}
