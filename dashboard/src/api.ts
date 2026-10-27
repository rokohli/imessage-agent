// Response types are generated from FastAPI OpenAPI; run scripts/dashboard_types.py after API changes.
import type { components } from "./schema";
export type Schedule = components["schemas"]["ScheduleView"];
export type ScheduleInput = components["schemas"]["ScheduleInput"];
export type Occurrence = components["schemas"]["Occurrence"];
export type Draft = components["schemas"]["DraftView"];
export type Contact = components["schemas"]["ContactView"];
export type Settings = components["schemas"]["Settings"];
export type Health = components["schemas"]["Health"];
export type Summary = components["schemas"]["Summary"];
export type Attempt = components["schemas"]["AttemptView"];
export type Accounts = components["schemas"]["AccountView"];
let token: string | undefined;
export async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  if (method !== "GET" && !token) {
    const session = await request<{ token: string }>("/session");
    token = session.token;
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      cache: "no-store",
      headers:
        method === "GET"
          ? {}
          : { "Content-Type": "application/json", "X-Dashboard-Token": token! },
      body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
    });
  } catch {
    throw new Error(
      "Cannot reach the local backend. Start the dashboard service, then try again.",
    );
  }
  if (!response.ok) {
    if (response.status === 403) token = undefined;
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.error === "string"
        ? error.error.replaceAll("_", " ")
        : `Request failed (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}
export const api = {
  refreshContacts: (authorize: boolean, country_code: string | null) =>
    request<{status: string}>("/contacts/refresh", "POST", {authorize, country_code}),
  clearContacts: () => request("/contacts/cache", "DELETE"),
  thresholds: () => request<components["schemas"]["ConversationThresholds"]>("/conversation-thresholds"),
  saveThresholds: (data: components["schemas"]["ConversationThresholds"]) =>
    request("/conversation-thresholds", "PUT", data),
  worker: () => request<components["schemas"]["WorkerHealth"]>("/worker"),
  summary: () => request<Summary>("/summary"),
  health: () => request<Health>("/health"),
  schedules: () => request<Schedule[]>("/schedules"),
  occurrences: () => request<Occurrence[]>("/occurrences"),
  saveSchedule: (data: ScheduleInput, id?: string) =>
    request<Schedule>(
      `/schedules${id ? `/${id}` : ""}`,
      id ? "PUT" : "POST",
      data,
    ),
  cancel: (id: string) => request(`/schedules/${id}`, "DELETE"),
  contacts: (q = "") =>
    request<Contact[]>(`/contacts?q=${encodeURIComponent(q)}`),
  preference: (key: string, value: "disabled" | "ask_first") =>
    request(`/contacts/${encodeURIComponent(key)}/preferences`, "PUT", {
      future_auto_reply: value,
    }),
  drafts: () => request<Draft[]>("/drafts"),
  approvals: () => request<Draft[]>("/approvals"),
  editDraft: (draft: Draft, body: string) =>
    request<Draft>(`/drafts/${draft.id}`, "PUT", {
      revision: draft.revision,
      body,
    }),
  draftAction: (draft: Draft, action: "submit" | "discard" | "regenerate") =>
    request<Draft>(`/drafts/${draft.id}/${action}`, "POST", {
      revision: draft.revision,
    }),
  approve: (draft: Draft) =>
    request<Draft>(`/approvals/${draft.id}/approve`, "POST", {
      revision: draft.revision,
      recipient: draft.recipient,
      body: draft.body,
      confirmed: true,
    }),
  reject: (draft: Draft) =>
    request<Draft>(`/approvals/${draft.id}/reject`, "POST", {
      revision: draft.revision,
    }),
  attempts: () => request<Attempt[]>("/attempts"),
  settings: () => request<Settings>("/settings"),
  saveSettings: (settings: Settings) =>
    request<Settings>("/settings", "PUT", settings),
  accounts: () => request<Accounts>("/accounts"),
  selectAccount: (account_id: string) =>
    request("/accounts", "PUT", { account_id, confirmed: true }),
};
