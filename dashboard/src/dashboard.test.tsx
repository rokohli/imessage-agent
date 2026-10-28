import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, type Draft, type Schedule, type Occurrence, type ScheduleInput } from "./api";
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
    styleEdits: vi.fn(),
    deleteStyleEdit: vi.fn(),
    clearStyleEdits: vi.fn(),
    thresholds: vi.fn(),
    saveThresholds: vi.fn(),
    authorizeContacts: vi.fn(),
    searchRecipients: vi.fn(),
    worker: vi.fn(),
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
  vi.mocked(api.styleEdits).mockResolvedValue([]);
  vi.mocked(api.thresholds).mockResolvedValue({gap_seconds: 14400, day_gap_seconds: 1800, burst_seconds: 15});
  vi.mocked(api.worker).mockResolvedValue({status: "stopped", heartbeat: null, last_poll: null, pending_count: 0, next_execution: null, recent_errors: [], last_outcome: null, transport: null});
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
    fireEvent.change(screen.getByLabelText("Recipient"), {
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
  fireEvent.change(screen.getByLabelText("Filter known recipients"), {
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
it("hides cancelled schedules after loading and cancellation while retaining activity", async () => {
  const plan: Schedule = {
    id: "active-plan",
    status: "active",
    replaces: null,
    spec: {
      recipient: draft.recipient,
      body: "Synthetic scheduled message",
      start_date: "2099-01-01",
      start_time: "13:00",
      end_time: "13:00",
      window: false,
      timezone: "UTC",
      recurrence: "once",
      weekdays: [],
      until: null,
      fold: "reject",
      grace: 300,
      transport: "mock",
      account_id: null,
    },
  };
  const occurrence: Occurrence = {
    id: "execution",
    plan_id: plan.id,
    recipient: plan.spec.recipient,
    local_date: "2099-01-01",
    window_start: 1,
    window_end: 1,
    send_at: 1,
    deadline: 301,
    status: "pending",
    detail: "",
    claimed_at: null,
    transport_status: null,
  };
  const cancelled = {
    ...plan,
    id: "cancelled-plan",
    status: "cancelled",
    spec: { ...plan.spec, body: "Previously cancelled message" },
  };
  vi.mocked(api.schedules)
    .mockResolvedValueOnce([plan, cancelled])
    .mockResolvedValue([{ ...plan, status: "cancelled" }, cancelled]);
  vi.mocked(api.occurrences)
    .mockResolvedValueOnce([occurrence])
    .mockResolvedValue([{ ...occurrence, status: "cancelled" }]);
  vi.mocked(api.cancel).mockResolvedValue({ ok: true });
  vi.mocked(api.attempts).mockResolvedValue([]);
  const schedules = render(<Schedules />);
  await screen.findByText(plan.spec.body);
  expect(screen.queryByText(cancelled.spec.body)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel schedule" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel schedule" }));
  await screen.findByText("No schedules yet. Your first message starts here.");
  expect(api.cancel).toHaveBeenCalledWith(plan.id);
  expect(screen.queryByText(plan.spec.body)).not.toBeInTheDocument();
  schedules.unmount();
  render(<Activity />);
  expect(await screen.findByText(plan.spec.recipient)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Outcome"), {
    target: { value: "cancelled" },
  });
  expect(screen.getByText(plan.spec.recipient)).toBeInTheDocument();
  expect(screen.getByText("Scheduled execution")).toBeInTheDocument();
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


it("shows stale worker attention, heartbeat, next execution and sanitized errors", async () => {
  vi.mocked(api.worker).mockResolvedValue({status: "attention", heartbeat: 1, last_poll: 1, pending_count: 2, next_execution: 100, recent_errors: [{timestamp: 2, category: "sqlite_unavailable"}], last_outcome: "unknown", transport: "mock"});
  const { WorkerPanel } = await import("./pages");
  render(<WorkerPanel />);
  expect(await screen.findByText("attention")).toBeInTheDocument();
  expect(screen.getByText(/Last heartbeat:/)).toBeInTheDocument();
  expect(screen.getByText(/Next execution:/)).toBeInTheDocument();
  expect(screen.getByText(/sqlite unavailable/)).toBeInTheDocument();
  expect(screen.getByText(/Last outcome: unknown/)).toBeInTheDocument();
});


it("hides previous worker health on failed or pending refresh and restores fresh status", async () => {
  vi.useFakeTimers();
  try {
    const health = {
      status: "running", heartbeat: 1, last_poll: 1, pending_count: 0,
      next_execution: null, recent_errors: [], last_outcome: null, transport: "mock",
    };
    vi.mocked(api.worker).mockResolvedValue(health);
    const { WorkerPanel } = await import("./pages");
    let unmount = () => {};
    await act(async () => { unmount = render(<WorkerPanel />).unmount; });
    expect(screen.getByText("running")).toBeInTheDocument();
    vi.mocked(api.worker).mockRejectedValueOnce(new Error("Local status unavailable"));
    await act(async () => { vi.advanceTimersByTime(10000); });
    expect(screen.getByRole("alert")).toHaveTextContent("Local status unavailable");
    expect(screen.queryByText("running")).not.toBeInTheDocument();
    let finish!: (value: Awaited<ReturnType<typeof api.worker>>) => void;
    vi.mocked(api.worker).mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    fireEvent.click(screen.getByRole("button", {name: "Try again"}));
    expect(screen.queryByText("running")).not.toBeInTheDocument();
    expect(screen.getByText("Loading local data…")).toBeInTheDocument();
    await act(async () => { finish({...health, status: "stopped"}); });
    expect(screen.getByText("stopped")).toBeInTheDocument();
    unmount();
  } finally {
    vi.useRealTimers();
  }
});


it("shows contact labels, session boundaries and timestamps while retaining the canonical approval recipient", async () => {
  vi.mocked(api.drafts).mockResolvedValue([{...draft, display_name: "Synthetic Alice", conversation_state: "NEW_SESSION",
    context: [{direction: "inbound", body: "Dinner plans", timestamp: "2026-10-27T18:00:00-05:00", session_start: true},
      {direction: "outbound", body: "Old homework", historical: true, topic_boundary: true}]}]);
  render(<Drafts />);
  expect(await screen.findByRole("heading", {name: "Synthetic Alice"})).toBeInTheDocument();
  expect(screen.getByText(/NEW SESSION/)).toBeInTheDocument();
  expect(screen.getByText(/Session starts/)).toBeInTheDocument();
  expect(screen.getByText(/Historical context/)).toBeInTheDocument();
  expect(screen.getByText("2026-10-27T18:00:00-05:00")).toBeInTheDocument();
});

it("requests Contacts permission only on an explicit click without importing contacts", async () => {
  vi.mocked(api.authorizeContacts).mockResolvedValue({status: "denied"});
  render(<SettingsPage />);
  expect(api.authorizeContacts).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", {name: "Request Contacts access"}));
  await waitFor(() => expect(api.authorizeContacts).toHaveBeenCalledWith(null));
  expect(await screen.findByText("denied")).toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "Refresh contact names"})).not.toBeInTheDocument();
  expect(api.searchRecipients).not.toHaveBeenCalled();
});


it("looks up a typed name only on request and requires choosing an exact handle", async () => {
  vi.mocked(api.searchRecipients).mockResolvedValue({status: "authorized", matches: [
    {display_name: "Alex", recipient: "+12025550101", recipient_key: "phone:+12025550101"},
    {display_name: "Alex", recipient: "alex@example.com", recipient_key: "email:alex@example.com"},
  ]});
  render(<ScheduleForm settings={settings} account={null} close={vi.fn()} saved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Recipient"), {target: {value: "Alex"}});
  expect(api.searchRecipients).not.toHaveBeenCalled();
  expect(validateSchedule({recipient: "Alex", body: "hello", start_date: "2099-10-27", start_time: "13:00", timezone: "UTC", confirmed: true})).toMatch(/Select a matching contact/);
  fireEvent.click(screen.getByRole("button", {name: "Find contact"}));
  fireEvent.click(await screen.findByRole("button", {name: "Alex · alex@example.com"}));
  expect(screen.getByText("Selected: Alex · alex@example.com")).toBeInTheDocument();
  expect(api.saveSchedule).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Message"), {target: {value: "hello"}});
  fireEvent.change(screen.getByLabelText("Start date"), {target: {value: "2099-10-27"}});
  fireEvent.change(screen.getByLabelText("Exact time / window start"), {target: {value: "13:00"}});
  fireEvent.click(screen.getByRole("button", {name: "Review schedule"}));
  expect(screen.getByRole("heading", {name: "Alex"})).toBeInTheDocument();
  expect(screen.getByText("alex@example.com")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("I authorize this exact recipient, message and schedule."));
  fireEvent.click(screen.getByRole("button", {name: "Authorize schedule"}));
  await waitFor(() => expect(api.saveSchedule).toHaveBeenCalledWith(expect.objectContaining({recipient: "alex@example.com", recipient_name: "Alex"}), undefined));
});

it("discards outdated name results after the recipient field changes", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.searchRecipients>>) => void;
  vi.mocked(api.searchRecipients).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  render(<ScheduleForm settings={settings} account={null} close={vi.fn()} saved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Recipient"), {target: {value: "Alice"}});
  fireEvent.click(screen.getByRole("button", {name: "Find contact"}));
  fireEvent.change(screen.getByLabelText("Recipient"), {target: {value: "Bob"}});
  await act(async () => finish({status: "authorized", matches: [{display_name: "Alice", recipient: "+12025550101", recipient_key: "phone:+12025550101"}]}));
  expect(screen.queryByRole("button", {name: "Alice · +12025550101"})).not.toBeInTheDocument();
  expect(screen.getByLabelText("Recipient")).toHaveValue("Bob");
});

it("leaves direct handle entry available when name lookup is denied", async () => {
  vi.mocked(api.searchRecipients).mockResolvedValue({status: "denied", matches: []});
  render(<ScheduleForm settings={settings} account={null} close={vi.fn()} saved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Recipient"), {target: {value: "Alice"}});
  fireEvent.click(screen.getByRole("button", {name: "Find contact"}));
  expect(await screen.findByText(/Name lookup unavailable/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Recipient"), {target: {value: "+12025550101"}});
  expect(screen.getByLabelText("Recipient")).toHaveValue("+12025550101");
  expect(api.saveSchedule).not.toHaveBeenCalled();
});


it("shows wording modes and lets the owner delete learned edits", async () => {
  vi.mocked(api.styleEdits).mockResolvedValue([{id: "edit-one", draft_id: "draft-one", recipient_key: "phone:+12025550101",
    original: "Absolutely", edited: "yea", created_at: 1, accepted_at: 2}]);
  vi.mocked(api.deleteStyleEdit).mockResolvedValue({});
  render(<SettingsPage />);
  expect(await screen.findByLabelText("Draft wording mode")).toHaveValue("single_pass");
  expect(screen.getByText("Original AI wording: Absolutely")).toBeInTheDocument();
  expect(screen.getByText("Your wording: yea")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: "Delete edit"}));
  await waitFor(() => expect(api.deleteStyleEdit).toHaveBeenCalledWith("edit-one"));
});
