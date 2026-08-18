// Operational incidents -- what the pipeline itself is struggling with
// (backend/api/incident_routes.py). Distinct from published incidents,
// which are the client deliverable.
import { json, url } from "./httpClient";

export interface Incident {
  id: string;
  ts: string;
  platform: string;
  kind: string;
  scope: string;
  job_id: string;
  error_type: string;
  severity: "critical" | "warning" | "info" | string;
  message: string;
  cause: string;
  fix: string;
  // the precise file:line blame trail from the extraction strategy chain
  where: string;
  url: string;
}

export const incidentsApi = {
  list: (limit = 50, severity = "", platform = "") =>
    fetch(
      url(
        `/incidents?limit=${limit}` +
          (severity ? `&severity=${encodeURIComponent(severity)}` : "") +
          (platform ? `&platform=${encodeURIComponent(platform)}` : ""),
      ),
    ).then(json<{ items: Incident[]; counts: Record<string, number> }>),
};
