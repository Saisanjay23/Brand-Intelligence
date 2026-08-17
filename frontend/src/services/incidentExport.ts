/**
 * Flattens a Profile's `incident` preview (backend/services/incident_publisher.py's
 * build_incident_doc, see api/types.ts's PublishedIncidentPreview) into the
 * one row shape CSV/JSON/Excel export all share for the analysis view,
 * the exact column set/order/labels the client-facing takedown-report
 * Excel template expects. A discovery-phase Profile has no `incident` and
 * is not exportable through this path (see ResultsGrid.tsx's handleExport,
 * which still exports raw Profile fields for discovery).
 */
import type { Profile } from "../api/types";

export const INCIDENT_EXPORT_COLUMNS = [
  "OrgId",
  "Domain",
  "AssetType",
  "AssetName",
  "Source",
  "RiskScore",
  "ThirdParty YES/NO",
  "Date (DD-MM-YYYY) (Optional)",
  "Title",
  "Description",
  "Active (Yes/No)",
  "Name (Yes/No)",
  "Logo (Yes/No)",
  "Location",
  "Number of Followers",
  "Last Post (DD-MM-YYYY) (Optional)",
] as const;

export type IncidentExportColumn = (typeof INCIDENT_EXPORT_COLUMNS)[number];
export type IncidentExportRow = Record<IncidentExportColumn, string>;

// "2026-07-16" -> "16-07-2026". Passes through unrecognised/empty input
// rather than guessing, a blank date must stay blank, not become "NaN".
function toDDMMYYYY(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}-${m[2]}-${m[1]}` : iso;
}

const yesNo = (v: boolean | null | undefined) => (v ? "Yes" : "No");

export function toIncidentExportRow(r: Profile): IncidentExportRow | null {
  const inc = r.incident;
  if (!inc) return null;
  const s = inc.socialProfileInfo;
  return {
    OrgId: inc.orgId,
    Domain: inc.domain,
    AssetType: inc.assetType,
    AssetName: inc.assetName,
    Source: inc.source,
    RiskScore: inc.riskRating,
    "ThirdParty YES/NO": inc.thirdParty ? "YES" : "NO",
    "Date (DD-MM-YYYY) (Optional)": toDDMMYYYY(inc.date),
    Title: inc.title,
    Description: inc.description,
    "Active (Yes/No)": yesNo(s.isActive),
    "Name (Yes/No)": yesNo(s.isSimilarName),
    "Logo (Yes/No)": yesNo(s.isSimilarLogo),
    Location: s.location ?? "",
    "Number of Followers": s.numberOfFollowers != null ? String(s.numberOfFollowers) : "",
    "Last Post (DD-MM-YYYY) (Optional)": toDDMMYYYY(s.lastPostDate),
  };
}

export function toIncidentExportRows(rows: Profile[]): IncidentExportRow[] {
  const out: IncidentExportRow[] = [];
  for (const r of rows) {
    const row = toIncidentExportRow(r);
    if (row) out.push(row);
  }
  return out;
}
