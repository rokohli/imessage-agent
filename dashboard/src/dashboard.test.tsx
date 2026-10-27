import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, type Draft, type ScheduleInput } from "./api";
import {
  Activity,
  Contacts,
  Drafts,
  Overview,
  Schedules,
  SettingsPage,
} from "./pages";
import { ScheduleForm, validateSchedule } from "./ScheduleForm";
import { App } from "./main";
vi.mock("./api", () => ({
  api: {
    summary: vi.fn(),
    health: vi.fn(),
    schedules: vi.fn(),
    occurrences: vi.fn(),
    settings: vi.fn(),
    accounts: vi.fn(),
    contacts: vi.fn(),
    preference: vi.fn(),
    drafts: vi.fn(),
    approvals: vi.fn(),
    editDraft: vi.fn(),
    draftAction: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    attempts: vi.fn(),
    saveSchedule: vi.fn(),
    cancel: vi.fn(),
    saveSettings: vi.fn(),
  },
}));
const draft: Draft = {
  id: "synthetic",
  recipient: "+12025550101",
  recipient_key: "phone:+12025550101",
  body: "Synthetic draft",
  status: "draft",
  reason: "preview_only",
  revision: "v1",
  created_at: 1,
  context: [{ direction: "inbound", body: "Synthetic incoming" }],
  approval_id: null,
};
const settings = {
  model: "qwen2.5:3b",
  timezone: "UTC",
  grace: 300,
  notifications: false,
};
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.settings).mockResolvedValue(settings);
  vi.mocked(api.accounts).mockResolvedValue({
    selected: null,
    choices: [],
    valid: false,
  });
  vi.mocked(api.health).mockResolvedValue({
    messages: "not_configured",
    account: "not_configured",
    ollama: "unavailable",
    scheduler: "ready",
    worker: "not_managed_by_dashboard",
    auto_reply: false,
  });
});
describe("schedule validation", () => {
  const valid: ScheduleInput = {
    recipient: "+12025550101",
    body: "Synthetic",
    start_date: "2099-01-01",
    start_time: "13:00",
    timezone: "UTC",
    confirmed: true,
  };
  it("accepts exact time and rejects empty input, reversed windows, weekdays and live without account", () => {
    expect(validateSchedule(valid)).toBe("");
    for (const change of [
      { body: " " },
      { end_time: "12:00" },
      { recurrence: "weekdays", weekdays: [] },
      { transport: "live" },
      { grace: -1 },
    ])
      expect(
        validateSchedule({ ...valid, ...change } as ScheduleInput),
      ).not.toBe("");
  });
  it("requires exact confirmation before creating a schedule", async () => {
    render(
      <ScheduleForm
        settings={settings}
        account={null}
        close={vi.fn()}
        saved={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("Exact iMessage handle"), {
      target: { value: valid.recipient },
    });
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: valid.body },
    });
    fireEvent.change(screen.getByLabelText("Start date"), {
      target: { value: valid.start_date },
    });
    fireEvent.change(screen.getByLabelText("Exact time / window start"), {
      target: { value: valid.start_time },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review schedule" }));
    expect(api.saveSchedule).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Authorize schedule" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByLabelText(
        "I authorize this exact recipient, message and schedule.",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Authorize schedule" }));
    await waitFor(() =>
      expect(api.saveSchedule).toHaveBeenCalledWith(
        expect.objectContaining(valid),
        undefined,
      ),
    );
  });
});
it("overview uses real data and offers scheduling", async () => {
  vi.mocked(api.summary).mockResolvedValue({
    upcoming_count: 0,
    upcoming: [],
    pending_approvals: 2,
    saved_drafts: 3,
    recent_attempts: [],
    scheduler: "ready",
  });
  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>,
  );
  expect(
    await screen.findByText("No send attempts recorded."),
  ).toBeInTheDocument();
  expect(screen.getByText("2")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "＋ Schedule Message" }),
  ).toHaveAttribute("href", "/schedules?new=1");
});
it("shows backend errors and allows reload", async () => {
  vi.mocked(api.summary)
    .mockRejectedValueOnce(new Error("Local backend unavailable"))
    .mockResolvedValue({
      upcoming_count: 0,
      upcoming: [],
      pending_approvals: 0,
      saved_drafts: 0,
      recent_attempts: [],
      scheduler: "ready",
    });
  render(
    <MemoryRouter>
      <Overview />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Local backend unavailable",
  );
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByText("No send attempts recorded.");
});
it("edits a draft without approving or sending", async () => {
  vi.mocked(api.drafts).mockResolvedValue([draft]);
  vi.mocked(api.editDraft).mockResolvedValue({
    ...draft,
    body: "Edited",
    revision: "v2",
  });
  render(<Drafts />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Draft text"), {
    target: { value: "Edited" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() =>
    expect(api.editDraft).toHaveBeenCalledWith(draft, "Edited"),
  );
  expect(api.approve).not.toHaveBeenCalled();
  expect(api.draftAction).not.toHaveBeenCalled();
});
it("regeneration stays separate from approval", async () => {
  vi.mocked(api.drafts).mockResolvedValue([draft]);
  vi.mocked(api.draftAction).mockResolvedValue(draft);
  render(<Drafts />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Regenerate with Ollama" }),
  );
  await waitFor(() =>
    expect(api.draftAction).toHaveBeenCalledWith(draft, "regenerate"),
  );
  expect(api.approve).not.toHaveBeenCalled();
});
it("approval requires review of exact recipient and content", async () => {
  const pending = { ...draft, status: "pending" };
  vi.mocked(api.approvals).mockResolvedValue([pending]);
  vi.mocked(api.approve).mockResolvedValue({ ...pending, status: "approved" });
  render(<Drafts approvals />);
  fireEvent.click(await screen.findByRole("button", { name: "Approve…" }));
  expect(
    screen.getByRole("button", { name: "Approve message" }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByLabelText("I approve this exact recipient and message."),
  );
  fireEvent.click(screen.getByRole("button", { name: "Approve message" }));
  await waitFor(() => expect(api.approve).toHaveBeenCalledWith(pending));
});
it("activity filters unknown outcomes and never offers retries", async () => {
  vi.mocked(api.occurrences).mockResolvedValue([]);
  vi.mocked(api.attempts).mockResolvedValue([
    { id: "a", recipient: draft.recipient, status: "unknown", created_at: 1 },
    { id: "b", recipient: "+12025550102", status: "sent", created_at: 2 },
  ]);
  render(<Activity />);
  await screen.findByText("+12025550102");
  fireEvent.change(screen.getByLabelText("Outcome"), {
    target: { value: "unknown" },
  });
  expect(screen.queryByText("+12025550102")).not.toBeInTheDocument();
  expect(screen.getByText("+12025550101")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /retry/i }),
  ).not.toBeInTheDocument();
});
it("contacts search displays canonical identities", async () => {
  vi.mocked(api.contacts).mockResolvedValue([
    {
      recipient_key: draft.recipient_key,
      display_name: "Synthetic Person",
      address: draft.recipient,
      kind: "phone",
      outbound_allowed: true,
      handles: [draft.recipient],
      profiles: [],
      future_auto_reply: "disabled",
    },
  ]);
  render(<Contacts />);
  await screen.findByText("Synthetic Person");
  expect(screen.getByText(draft.recipient_key)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Search contacts"), {
    target: { value: "Absent" },
  });
  expect(screen.queryByText("Synthetic Person")).not.toBeInTheDocument();
});
it("schedule page has a useful empty state", async () => {
  vi.mocked(api.schedules).mockResolvedValue([]);
  vi.mocked(api.occurrences).mockResolvedValue([]);
  render(<Schedules />);
  expect(
    await screen.findByText(
      "No schedules yet. Your first message starts here.",
    ),
  ).toBeInTheDocument();
});
it("settings keeps automatic replies disabled", async () => {
  render(<SettingsPage />);
  expect(
    await screen.findByText(/Autonomous AI replies are disabled/),
  ).toBeInTheDocument();
  expect(await screen.findByLabelText("Local Ollama model")).toHaveValue(
    "qwen2.5:3b",
  );
});
it("sidebar routes render and appearance toggles", async () => {
  vi.mocked(api.drafts).mockResolvedValue([]);
  render(
    <MemoryRouter initialEntries={["/drafts"]}>
      <App />
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("heading", { name: "AI Drafts" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "☾ Dark appearance" }));
  expect(
    screen.getByRole("button", { name: "☀ Light appearance" }),
  ).toBeInTheDocument();
});
