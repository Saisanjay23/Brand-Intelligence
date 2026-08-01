// API calls for the backend's sessions module (backend/api/session_routes.py).
// One call per platform id (facebook/twitter/instagram/youtube/telegram/
// linkedin) -- the backend has no per-platform route, `platform` is always
// a parameter here, not a separate resource, so this one file covers every
// platform's session/credential management rather than one file each.
import { json, post, url } from "./httpClient";
import type { SessionInfo } from "./types";

// No bulk "all sessions" route -- one call per known platform id (get the
// id list from healthApi.platformsHealth()).
export const sessionsApi = {
  sessionStatus: (platform: string) => fetch(url(`/sessions/${platform}`)).then(json<SessionInfo>),
  saveCookies: (platform: string, blob: string, identifier = "") =>
    post(`/sessions/${platform}/cookies`, { blob, identifier }).then(json<SessionInfo>),
  saveApiKey: (platform: string, key: string) =>
    post(`/sessions/${platform}/api-key`, { key }).then(json<SessionInfo>),
  launchLogin: (platform: string, timeoutS = 300, identifier = "") =>
    post(`/sessions/${platform}/login`, { timeout_s: timeoutS, identifier }).then(
      json<{ platform: string; status: string; message: string; started: string; finished: string }>,
    ),
  checkSessionNow: (platform: string) =>
    post(`/sessions/${platform}/check`, {}).then(json<{ ok: boolean; detail: string }>),
  setSessionProxy: (
    platform: string,
    sessionId: string,
    proxy: { server: string; username?: string; password?: string; timezone_id?: string },
  ) =>
    fetch(url(`/sessions/${platform}/${sessionId}/proxy`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proxy }),
    }).then(json<SessionInfo>),
  // Backend has no separate DELETE-proxy route -- clearing is PUT with
  // proxy: null (see backend/controllers/session_controller.py::set_proxy).
  clearSessionProxy: (platform: string, sessionId: string) =>
    fetch(url(`/sessions/${platform}/${sessionId}/proxy`), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proxy: null }),
    }).then(json<SessionInfo>),
  deleteSessionItem: (platform: string, sessionId: string) =>
    fetch(url(`/sessions/${platform}/${sessionId}`), { method: "DELETE" }).then(json<SessionInfo>),
  deleteSessionPool: (platform: string) =>
    fetch(url(`/sessions/${platform}`), { method: "DELETE" }).then(json<SessionInfo>),

  // Telegram's MTProto login is multi-step (code, then optionally a 2FA
  // password) so it can't reuse launchLogin's single-shot headful-browser
  // shape -- see backend/services/telegram_login_service.py.
  telegramLoginStart: (apiId: number, apiHash: string, phone: string) =>
    post("/sessions/telegram/login/start", { api_id: apiId, api_hash: apiHash, phone }).then(
      json<{ status: string; phone: string }>,
    ),
  telegramLoginCode: (code: string) =>
    post("/sessions/telegram/login/code", { code }).then(
      json<{ status: "need_password" | "saved"; message?: string }>,
    ),
  telegramLoginPassword: (password: string) =>
    post("/sessions/telegram/login/password", { password }).then(
      json<{ status: "saved"; message: string }>,
    ),
  telegramLoginCancel: () =>
    post("/sessions/telegram/login/cancel", {}).then(json<{ status: string }>),
};
