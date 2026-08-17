// API calls for the backend's mail-alerting settings resource
// (backend/api/settings_routes.py).
import { json, post, url } from "./httpClient";

export interface MailSettings {
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  // GET never returns the real password, only whether one is stored.
  smtp_pass_set: boolean;
  alert_emails: string[];
  alert_from: string;
}

export interface MailSettingsInput {
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  // blank leaves the currently-saved password untouched, see backend
  // dto/settings_dto.py::MailSettingsIn.
  smtp_pass: string;
  alert_emails: string[];
  alert_from: string;
}

export const settingsApi = {
  getMailSettings: () => fetch(url("/settings/mail")).then(json<MailSettings>),
  updateMailSettings: (body: MailSettingsInput) =>
    fetch(url("/settings/mail"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(json<MailSettings>),
  sendTestEmail: () => post("/settings/mail/test", {}).then(json<{ sent: boolean; detail: string }>),
};
