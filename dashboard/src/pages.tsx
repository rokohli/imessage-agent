import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, type Draft, type Schedule, type Settings } from "./api";
import {
  Card,
  Empty,
  ErrorText,
  errorMessage,
  LoadState,
  Modal,
  Status,
  time,
  useData,
} from "./ui";
import { ScheduleForm } from "./ScheduleForm";

export function WorkerPanel() {
  const state = useData(api.worker);
  useEffect(() => {
    const timer = window.setInterval(() => void state.refresh(), 10000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <Card title="Background worker">
      <LoadState {...state} retry={state.refresh} />
      {state.data && !state.loading && !state.error && (
        <>
          <div className="list-row">
            <span>Worker</span>
            <Status value={state.data.status} />
          </div>
          <p>Transport: {state.data.transport ?? "Not configured"}</p>
          <p>Last heartbeat: {state.data.heartbeat === null ? "Never" : time(state.data.heartbeat)}</p>
          <p>Last successful poll: {state.data.last_poll === null ? "Never" : time(state.data.last_poll)}</p>
          <p>Next execution: {state.data.next_execution === null ? "None materialized" : time(state.data.next_execution)}</p>
          <p>Pending occurrences: {state.data.pending_count}</p>
          <p>Last outcome: {state.data.last_outcome ?? "None"}</p>
          {state.data.recent_errors.map((e, i) => (
            <p key={i} role="status">
              {time(e.timestamp)} · {e.category.replaceAll("_", " ")}
            </p>
          ))}
        </>
      )}
      <p className="note">
        Enable from Terminal once: run the LaunchAgent install command with your
        existing state path, then start it. Add --execute-live at installation only
        when you authorize execution of existing live schedules. See
        docs/background-agent.md for exact commands. The Mac must be awake with a
        Messages user session available.
      </p>
      <p className="note">
        Running requires a held worker lock, a fresh heartbeat and a fresh
        successful poll. Ollama is optional. UNKNOWN outcomes are never retried.
      </p>
    </Card>
  );
}

export function Overview() {
  const state = useData(api.summary);
  const health = useData(api.health);
  return (
    <>
      <div className="welcome">
        <div>
          <p className="eyebrow">YOUR LOCAL CONTROL CENTER</p>
          <h2>A little more intentional.</h2>
          <p>Plan your messages. Keep the final say.</p>
        </div>
        <Link className="button primary" to="/schedules?new=1">
          ＋ Schedule Message
        </Link>
      </div>
      <WorkerPanel />
      <LoadState {...state} retry={state.refresh} />
      {state.data && (
        <>
          <div className="stats">
            <Card>
              <p className="muted">Upcoming messages</p>
              <strong>{state.data.upcoming_count}</strong>
              <Link to="/schedules">View schedules ↗</Link>
            </Card>
            <Card>
              <p className="muted">Awaiting your approval</p>
              <strong>{state.data.pending_approvals}</strong>
              <Link to="/approvals">Review queue ↗</Link>
            </Card>
            <Card>
              <p className="muted">Saved AI drafts</p>
              <strong>{state.data.saved_drafts}</strong>
              <Link to="/drafts">Open drafts ↗</Link>
            </Card>
          </div>
          <div className="two-col">
            <Card
              title="Coming up"
              action={<Link to="/schedules">View all</Link>}
            >
              {state.data.upcoming.length ? (
                state.data.upcoming.map((o) => (
                  <div className="list-row" key={o.id}>
                    <div>
                      <b>{o.display_name || o.recipient}</b>
                      <p className="muted">
                        {time(o.send_at)} · {o.local_date}
                      </p>
                    </div>
                    <Status value={o.status} />
                  </div>
                ))
              ) : (
                <Empty>
                  No scheduled messages yet. Start with a message for later.
                </Empty>
              )}
            </Card>
            <Card title="Local services">
              <LoadState {...health} retry={health.refresh} />
              {health.data && (
                <>
                  <div className="list-row">
                    <span>Ollama</span>
                    <Status value={health.data.ollama} />
                  </div>
                  <div className="list-row">
                    <span>Scheduler</span>
                    <Status value={health.data.scheduler} />
                  </div>
                  <div className="list-row">
                    <span>Auto replies</span>
                    <Status value="disabled" />
                  </div>
                </>
              )}
              <p className="note">
                Background execution is managed separately through the per-user LaunchAgent.
              </p>
              <Link to="/settings">Service settings ↗</Link>
            </Card>
          </div>
          <Card title="Recent send attempts">
            {state.data.recent_attempts.length ? (
              state.data.recent_attempts.map((a) => (
                <div className="list-row" key={a.id}>
                  <b>{a.display_name || a.recipient}</b>
                  <span className="muted">{time(a.created_at)}</span>
                  <Status value={a.status} />
                </div>
              ))
            ) : (
              <Empty>No send attempts recorded.</Empty>
            )}
          </Card>
        </>
      )}
    </>
  );
}

export function Schedules() {
  const state = useData(async () => ({
    plans: (await api.schedules()).filter((p) => p.status !== "cancelled"),
    occurrences: await api.occurrences(),
    settings: await api.settings(),
    account: await api
      .accounts()
      .then((a) => a.selected)
      .catch(() => null),
  }));
  const [editing, setEditing] = useState<Schedule | "new" | null>(() =>
    new URLSearchParams(location.search).has("new") ? "new" : null,
  );
  const [selected, setSelected] = useState<Schedule>();
  const [cancel, setCancel] = useState<Schedule>();
  const [account, setAccount] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const open = async (s: Schedule | "new") => {
    setEditing(s);
    try {
      setAccount((await api.accounts()).selected);
    } catch {
      setAccount(null);
    }
  };
  const doCancel = async () => {
    if (!cancel) return;
    setBusy(true);
    try {
      await api.cancel(cancel.id);
      setCancel(undefined);
      await state.refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <div className="toolbar">
        <p className="muted">
          Exact times, flexible windows, thoughtful routines.
        </p>
        <button className="primary" onClick={() => void open("new")}>
          ＋ Schedule Message
        </button>
      </div>
      <LoadState {...state} retry={state.refresh} />
      <ErrorText error={error} />
      {state.data && (
        <>
          {!state.data.plans.length ? (
            <Card>
              <Empty>No schedules yet. Your first message starts here.</Empty>
            </Card>
          ) : (
            state.data.plans.map((p) => {
              const next = state.data!.occurrences.filter(
                (o) => o.plan_id === p.id && o.status === "pending",
              )[0];
              return (
                <Card key={p.id}>
                  <div className="card-heading">
                    <h2>{p.display_name || p.spec.recipient}</h2>
                    <Status value={p.status} />
                  </div>
                  <p className="message">{p.spec.body}</p>
                  <p className="muted">
                    {p.spec.recurrence} · {p.spec.timezone} · {p.spec.transport}
                    {p.spec.window ? " · randomized window" : " · exact time"}
                  </p>
                  <p>
                    {next
                      ? `Selected execution: ${time(next.send_at, p.spec.timezone)}`
                      : "No pending occurrence in the materialized horizon"}
                  </p>
                  <div className="actions">
                    <button onClick={() => setSelected(p)}>
                      Execution history
                    </button>
                    {p.status === "active" && (
                      <>
                        <button onClick={() => void open(p)}>Edit</button>
                        <button className="danger" onClick={() => setCancel(p)}>
                          Cancel schedule
                        </button>
                      </>
                    )}
                  </div>
                </Card>
              );
            })
          )}
          {editing && (
            <ScheduleForm
              schedule={editing === "new" ? undefined : editing}
              settings={state.data.settings}
              account={account ?? state.data.account}
              close={() => setEditing(null)}
              saved={() => {
                setEditing(null);
                void state.refresh();
              }}
            />
          )}
          {selected && (
            <Modal
              title="Execution history"
              close={() => setSelected(undefined)}
            >
              <p>
                {selected.display_name || selected.spec.recipient} · {selected.spec.timezone}
              </p>
              {state.data.occurrences
                .filter((o) => o.plan_id === selected.id)
                .map((o) => (
                  <div className="history-row" key={o.id}>
                    <span>{time(o.send_at, selected.spec.timezone)}</span>
                    <Status value={o.status} />
                    <small>
                      Deadline {time(o.deadline, selected.spec.timezone)} ·{" "}
                      {o.detail || "Awaiting worker"}
                    </small>
                  </div>
                ))}
            </Modal>
          )}
          {cancel && (
            <Modal
              title="Cancel schedule?"
              close={() => !busy && setCancel(undefined)}
            >
              <p>
                Cancel pending occurrences for <b>{cancel.display_name || cancel.spec.recipient}</b>?
                History remains available. An already claimed attempt may
                finish.
              </p>
              <ErrorText error={error} />
              <div className="actions">
                <button onClick={() => setCancel(undefined)} disabled={busy}>
                  Keep schedule
                </button>
                <button
                  className="danger"
                  onClick={() => void doCancel()}
                  disabled={busy}
                >
                  {busy ? "Cancelling…" : "Cancel schedule"}
                </button>
              </div>
            </Modal>
          )}
        </>
      )}
    </>
  );
}

export function Contacts() {
  const state = useData(() => api.contacts());
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const contacts = state.data?.filter((c) =>
    `${c.display_name} ${c.address} ${c.recipient_key}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <>
      <div className="toolbar">
        <input
          aria-label="Filter known recipients"
          placeholder="Filter saved recipients…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="muted">
          Known recipients · no address-book import
        </span>
      </div>
      <LoadState {...state} retry={state.refresh} />
      <ErrorText error={error} />
      {contacts?.length === 0 && (
        <Card>
          <Empty>
            No matching contacts. Contacts appear when registered with the
            agent.
          </Empty>
        </Card>
      )}
      <div className="two-col">
        {contacts?.map((c) => (
          <Card key={c.recipient_key}>
            <div className="card-heading">
              <h2>{c.display_name}</h2>
              <Status
                value={c.outbound_allowed ? "allowlisted" : "restricted"}
              />
            </div>
            <p>{c.address}</p>
            <p className="mono muted">{c.recipient_key}</p>
            <p className="muted">
              Available iMessage handles: {c.handles.join(", ")}
            </p>
            {c.profiles.length ? (
              c.profiles.map((p, i) => (
                <p key={i} className="note">
                  Style: {p.sample_count} examples · median {p.median_words}{" "}
                  words · {Math.round((p.lowercase_fraction ?? 0) * 100)}%
                  lowercase · {Math.round((p.emoji_fraction ?? 0) * 100)}% emoji
                </p>
              ))
            ) : (
              <p className="note">No contact style profile saved yet.</p>
            )}
            <label>
              Future auto-reply preference
              <select
                disabled={busy === c.recipient_key}
                value={c.future_auto_reply}
                onChange={async (e) => {
                  setBusy(c.recipient_key);
                  setError("");
                  try {
                    await api.preference(
                      c.recipient_key,
                      e.target.value as "disabled" | "ask_first",
                    );
                    await state.refresh();
                  } catch (e) {
                    setError(errorMessage(e));
                  } finally {
                    setBusy("");
                  }
                }}
              >
                <option value="disabled">Disabled</option>
                <option value="ask_first">
                  Ask me first (future preference only)
                </option>
              </select>
            </label>
            <small className="muted">
              Auto replies remain disabled for every contact.
            </small>
          </Card>
        ))}
      </div>
    </>
  );
}

function DraftCard({
  draft,
  approvals,
  refresh,
}: {
  draft: Draft;
  approvals: boolean;
  refresh: () => Promise<void>;
}) {
  const [edit, setEdit] = useState(false);
  const [body, setBody] = useState(draft.body);
  const [confirm, setConfirm] = useState<"approve" | "discard" | null>(null);
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const perform = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      setEdit(false);
      setConfirm(null);
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <div className="card-heading">
        <h2>{draft.display_name || draft.recipient}</h2>
        <Status value={draft.status} />
      </div>
      <p className="eyebrow">{time(draft.created_at)} · {draft.conversation_state?.replaceAll("_", " ")}</p>
      {edit ? (
        <label>
          Edit draft
          <textarea
            aria-label="Draft text"
            rows={4}
            maxLength={4000}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </label>
      ) : (
        <p className="message">
          {draft.body || "No reply generated. Edit to supply a message."}
        </p>
      )}
      <p className="note">
        Review reason: {draft.reason.replaceAll("_", " ")}. Human approval is
        required before any send.
      </p>
      {draft.context.length > 0 && (
        <details>
          <summary>Conversation context ({draft.context.length})</summary>
          {draft.context.map((c, i) => (
            <p className="context" key={i}>
              {c.session_start && <small>Session starts · </small>}
              {c.topic_boundary && <small>Topic transition · </small>}
              {c.historical && <small>Historical context · </small>}
              <time>{c.timestamp ?? (c.occurred_at ? time(c.occurred_at) : "Time unavailable")}</time>{" "}
              <b>{c.direction === "outbound" ? "You" : draft.display_name || "Contact"}:</b>{" "}
              {c.body ?? "[unavailable]"}
            </p>
          ))}
        </details>
      )}
      <ErrorText error={error} />
      <div className="actions">
        {edit ? (
          <>
            <button
              disabled={busy || !body.trim()}
              className="primary"
              onClick={() =>
                void perform(async () => {
                  const updated = await api.editDraft(draft, body);
                  if (approvals) await api.draftAction(updated, "submit");
                })
              }
            >
              {approvals ? "Save and resubmit" : "Save draft"}
            </button>
            <button disabled={busy} onClick={() => setEdit(false)}>
              Cancel edit
            </button>
          </>
        ) : (
          <>
            <button
              disabled={busy}
              onClick={() => {
                setBody(draft.body);
                setEdit(true);
              }}
            >
              Edit{approvals ? " and resubmit" : ""}
            </button>
            {approvals ? (
              <>
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => {
                    setConfirm("approve");
                    setChecked(false);
                  }}
                >
                  Approve…
                </button>
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() => void perform(() => api.reject(draft))}
                >
                  Reject
                </button>
              </>
            ) : (
              <>
                <button
                  disabled={busy}
                  onClick={() =>
                    void perform(() => api.draftAction(draft, "regenerate"))
                  }
                >
                  {busy ? "Working…" : "Regenerate with Ollama"}
                </button>
                <button
                  disabled={
                    busy || !draft.body.trim() || draft.status === "pending"
                  }
                  className="primary"
                  onClick={() =>
                    void perform(() => api.draftAction(draft, "submit"))
                  }
                >
                  Submit for approval
                </button>
                <button
                  disabled={busy}
                  className="danger"
                  onClick={() => setConfirm("discard")}
                >
                  Discard…
                </button>
              </>
            )}
          </>
        )}
      </div>
      {confirm && (
        <Modal
          title={
            confirm === "approve" ? "Approve exact message" : "Discard draft?"
          }
          close={() => !busy && setConfirm(null)}
        >
          <p>
            <b>{draft.display_name && draft.display_name !== draft.recipient ? draft.display_name : draft.recipient}</b>
            {draft.display_name && draft.display_name !== draft.recipient ? ` · ${draft.recipient}` : ""}
          </p>
          <p className="message">{draft.body}</p>
          <p>
            {confirm === "approve"
              ? "Approval binds only this recipient and exact text, expires in 24 hours, and does not send. Editing revokes it."
              : "This removes the draft from the active list and invalidates any unused approval."}
          </p>
          {confirm === "approve" && (
            <label className="check">
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => setChecked(e.target.checked)}
              />{" "}
              I approve this exact recipient and message.
            </label>
          )}
          <ErrorText error={error} />
          <div className="actions">
            <button
              disabled={busy || (confirm === "approve" && !checked)}
              className="primary"
              onClick={() =>
                void perform(() =>
                  confirm === "approve"
                    ? api.approve(draft)
                    : api.draftAction(draft, "discard"),
                )
              }
            >
              {busy
                ? "Saving…"
                : confirm === "approve"
                  ? "Approve message"
                  : "Discard draft"}
            </button>
          </div>
        </Modal>
      )}
    </Card>
  );
}
export function Drafts({ approvals = false }: { approvals?: boolean }) {
  const state = useData(approvals ? api.approvals : api.drafts);
  return (
    <>
      <p className="page-note">
        {approvals
          ? "Your final say. Review the exact recipient and text before granting approval."
          : "Private, local AI drafts. Editing and regeneration never send a message."}
      </p>
      <LoadState {...state} retry={state.refresh} />
      {state.data?.length === 0 && (
        <Card>
          <Empty>
            {approvals
              ? "All caught up. No drafts awaiting approval."
              : "No saved drafts yet. Use draft-preview to generate and save a local draft."}
          </Empty>
        </Card>
      )}
      {state.data?.map((d) => (
        <DraftCard
          key={`${d.id}:${d.revision}`}
          draft={d}
          approvals={approvals}
          refresh={state.refresh}
        />
      ))}
    </>
  );
}

export function Activity() {
  const state = useData(async () => ({
    occurrences: await api.occurrences(),
    attempts: await api.attempts(),
  }));
  const [contact, setContact] = useState("");
  const [outcome, setOutcome] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const rows = [
    ...(state.data?.occurrences.map((o) => ({
      id: `o:${o.id}`,
      recipient:
        o.display_name && o.display_name !== o.recipient
          ? `${o.display_name} · ${o.recipient}`
          : o.recipient,
      status: o.status,
      at: o.send_at,
      kind: "Scheduled execution",
    })) ?? []),
    ...(state.data?.attempts.map((a) => ({
      id: `a:${a.id}`,
      recipient:
        a.display_name && a.display_name !== a.recipient
          ? `${a.display_name} · ${a.recipient}`
          : a.recipient,
      status: a.status,
      at: a.created_at,
      kind: "Send attempt",
    })) ?? []),
  ]
    .filter(
      (r) =>
        r.recipient.toLowerCase().includes(contact.toLowerCase()) &&
        (!outcome || r.status === outcome) &&
        (!from || r.at >= new Date(from + "T00:00:00").getTime() / 1000) &&
        (!to || r.at <= new Date(to + "T23:59:59").getTime() / 1000),
    )
    .sort((a, b) => b.at - a.at);
  return (
    <>
      <div className="filters">
        <label>
          Contact
          <input
            placeholder="Filter by handle"
            value={contact}
            onChange={(e) => setContact(e.target.value)}
          />
        </label>
        <label>
          Outcome
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="">All outcomes</option>
            {[
              "pending",
              "accepted",
              "sent",
              "delivered",
              "failed",
              "partial",
              "unknown",
              "missed",
              "cancelled",
            ].map((o) => (
              <option key={o}>{o}</option>
            ))}
          </select>
        </label>
        <label>
          From
          <input
            type="date"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
        </label>
        <label>
          Through
          <input
            type="date"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
        </label>
      </div>
      <p className="note">
        ACCEPTED is transport acceptance, not delivery. UNKNOWN outcomes require
        manual reconciliation and are never automatically retried.
      </p>
      <LoadState {...state} retry={state.refresh} />
      <Card>
        {!rows.length ? (
          <Empty>No activity matches these filters.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Contact</th>
                  <th>Type</th>
                  <th>Time</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>{r.recipient}</td>
                    <td>{r.kind}</td>
                    <td>{time(r.at)}</td>
                    <td>
                      <Status value={r.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

export function SettingsPage() {
  const state = useData(api.settings);
  const health = useData(api.health);
  const accounts = useData(api.accounts);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [accountIndex, setAccountIndex] = useState("");
  const [confirmAccount, setConfirmAccount] = useState(false);
  const save = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setSaved(false);
    setError("");
    const data: Settings = {
      model: String(form.get("model")),
      timezone: String(form.get("timezone")),
      grace: Number(form.get("grace")),
      notifications: form.get("notifications") === "on",
    };
    try {
      await api.saveSettings(data);
      setSaved(true);
      await state.refresh();
      await health.refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <LoadState {...state} retry={state.refresh} />
      <ErrorText error={error} />
      {saved && (
        <p role="status" className="success">
          Settings saved locally.
        </p>
      )}
      <WorkerPanel />
      <ConversationPreferences />
      <div className="two-col">
        <Card title="Application preferences">
          {state.data && (
            <form
              key={JSON.stringify(state.data)}
              onSubmit={save}
              className="form"
            >
              <fieldset disabled={busy}>
                <label>
                  Local Ollama model
                  <input
                    name="model"
                    required
                    defaultValue={state.data.model}
                  />
                </label>
                <label>
                  Default timezone
                  <input
                    name="timezone"
                    required
                    defaultValue={state.data.timezone}
                  />
                </label>
                <label>
                  Scheduling grace (seconds)
                  <input
                    name="grace"
                    type="number"
                    min="0"
                    max="86400"
                    required
                    defaultValue={state.data.grace}
                  />
                </label>
                <label className="check">
                  <input
                    name="notifications"
                    type="checkbox"
                    defaultChecked={state.data.notifications}
                  />{" "}
                  Private macOS notifications
                </label>
                <p className="note">
                  Schedule defaults apply to new schedules. Notification changes apply on the next worker poll. Install the helper and grant notification permission using the runbook.
                </p>
                <button className="primary">
                  {busy ? "Saving…" : "Save preferences"}
                </button>
              </fieldset>
            </form>
          )}
        </Card>
        <Card
          title="Local diagnostics"
          action={
            <button
              disabled={health.loading}
              onClick={() => void health.refresh()}
            >
              Refresh
            </button>
          }
        >
          <LoadState {...health} retry={health.refresh} />
          {health.data && !health.loading && !health.error &&
            Object.entries(health.data).map(([key, value]) => (
              <div className="list-row" key={key}>
                <span>{key.replaceAll("_", " ")}</span>
                <Status
                  value={typeof value === "boolean" ? "disabled" : value}
                />
              </div>
            ))}
          <p className="note">
            Readiness does not prove a worker is running. No private paths or
            conversation text are included.
          </p>
        </Card>
        <Card title="iMessage account">
          <LoadState {...accounts} retry={accounts.refresh} />
          {accounts.data && (
            <>
              <p className="muted">
                Selected: {accounts.data.selected ?? "Not configured"}
              </p>
              <label>
                Available account
                <select
                  value={accountIndex}
                  onChange={(e) => setAccountIndex(e.target.value)}
                >
                  <option value="">Choose account</option>
                  {accounts.data.choices.map((id, i) => (
                    <option value={id} key={id}>
                      Account {i + 1} · {id}
                    </option>
                  ))}
                </select>
              </label>
              <button
                disabled={!accountIndex || busy}
                onClick={() => setConfirmAccount(true)}
              >
                Verify and select…
              </button>
              <p className="note">
                Changing the account invalidates old live plans at dispatch.
                Reauthorize those schedules for the new account.
              </p>
            </>
          )}
        </Card>
        <Card title="Auto replies">
          <Status value="disabled" />
          <p className="page-note">
            You stay in control. Autonomous AI replies are disabled. Contact
            preferences only record future intent.
          </p>
          <p className="muted">
            Everything stays on this Mac. No analytics or cloud account.
          </p>
        </Card>
      </div>
      {confirmAccount && (
        <Modal
          title="Change selected account?"
          close={() => !busy && setConfirmAccount(false)}
        >
          <p>
            Verify and select {accountIndex}? Existing live schedules keep their
            original account binding and must be reauthorized to use another
            account.
          </p>
          <ErrorText error={error} />
          <button
            className="primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await api.selectAccount(accountIndex);
                setConfirmAccount(false);
                await accounts.refresh();
                await health.refresh();
              } catch (e) {
                setError(errorMessage(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Confirm account
          </button>
        </Modal>
      )}
    </>
  );
}


function ConversationPreferences() {
  const thresholds = useData(api.thresholds);
  const [country, setCountry] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const perform = async (action: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  };
  return <div className="two-col">
    <Card title="macOS Contacts">
      <p>Type a name in the schedule recipient field and click Find contact. Only the recipient you choose for a saved schedule is stored; your address book is not imported.</p>
      <label>Country calling code for national numbers (optional)
        <input aria-label="Contact country calling code" value={country} maxLength={3}
          onChange={e => setCountry(e.target.value)} inputMode="numeric" />
      </label>
      <div className="actions">
        <button disabled={busy} onClick={() => void perform(async () => {
          const result = await api.authorizeContacts(country || null); setStatus(result.status);
        })}>Request Contacts access</button>
      </div>
      <p role="status">{status}</p><ErrorText error={error} />
    </Card>
    <Card title="Conversation timing">
      <LoadState {...thresholds} retry={thresholds.refresh} />
      {thresholds.data && <form onSubmit={e => {
        e.preventDefault(); const data = new FormData(e.currentTarget);
        void perform(async () => {
          await api.saveThresholds({gap_seconds: Number(data.get("gap")),
            day_gap_seconds: Number(data.get("day")), burst_seconds: Number(data.get("burst"))});
          await thresholds.refresh(); setStatus("Conversation timing saved");
        });
      }}>
        <label>Session gap (seconds)<input name="gap" type="number" min="1" max="604800" required defaultValue={thresholds.data.gap_seconds} /></label>
        <label>Gap across midnight (seconds)<input name="day" type="number" min="1" max="86400" required defaultValue={thresholds.data.day_gap_seconds} /></label>
        <label>Incoming burst settling (seconds)<input name="burst" type="number" min="1" max="300" required defaultValue={thresholds.data.burst_seconds} /></label>
        <p className="note">Timing and topic continuity are heuristics. They never authorize sending.</p>
        <button disabled={busy}>Save conversation timing</button>
      </form>}
    </Card>
  </div>;
}
