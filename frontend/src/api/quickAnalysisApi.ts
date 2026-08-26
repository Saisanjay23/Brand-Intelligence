import { blob, json, post, url } from "./httpClient";

export interface QuickAnalysisItemData {
  id: string;
  url: string;
  platform: string;
  platform_name: string;
  entity_id: string;
  status: "pending" | "running" | "done" | "error";
  error?: string;
  analysed_at?: string;
  profile_name?: string;
  followers?: number | null;
  location?: string;
  bio?: string;
  last_post_date?: string;
  is_active?: boolean | null;
  has_logo?: boolean | null;
  has_name_match?: boolean | null;
  name_score?: number;
  risk_score?: number;
  priority?: string;
  profile_image_url?: string;
  verified?: boolean | null;
  comments?: string;
  has_screenshot?: boolean;
  incident_row: Record<string, any>;
  legacy_row: Record<string, any>;
}

export interface PlatformProgressData {
  status: "pending" | "running" | "done" | "failed";
  total: number;
  completed: number;
  displayName: string;
}

export interface QuickAnalysisJobResponse {
  id: string;
  status: "queued" | "running" | "done" | "cancelled" | "failed";
  target_name?: string;
  official_feed?: string;
  total: number;
  completed: number;
  message?: string;
  platform_progress: Record<string, PlatformProgressData>;
  items: QuickAnalysisItemData[];
}

export interface QuickAnalysisStartResponse {
  job_id: string;
  skipped: Array<{ url: string; reason: string }>;
  status: string;
}

export const quickAnalysisApi = {
  start: async (urls: string[], targetName?: string, officialFeed?: string): Promise<QuickAnalysisStartResponse> => {
    const res = await post("/quick-analysis/start", {
      urls,
      target_name: targetName || "",
      official_feed: officialFeed || "",
    });
    return json<QuickAnalysisStartResponse>(res);
  },

  getJob: async (jobId: string): Promise<QuickAnalysisJobResponse> => {
    const res = await fetch(url(`/quick-analysis/job/${jobId}`));
    return json<QuickAnalysisJobResponse>(res);
  },

  cancelJob: async (jobId: string): Promise<{ cancelled: boolean }> => {
    const res = await post(`/quick-analysis/cancel/${jobId}`, {});
    return json<{ cancelled: boolean }>(res);
  },

  getScreenshotUrl: (jobId: string, itemId: string): string => {
    return url(`/quick-analysis/screenshot/${jobId}/${itemId}`);
  },

  exportXlsx: async (filename: string, rows: Record<string, any>[]): Promise<Blob> => {
    const res = await post("/quick-analysis/export-xlsx", { filename, rows });
    return blob(res);
  },
};
