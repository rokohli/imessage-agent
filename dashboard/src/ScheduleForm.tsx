import { useRef, useState, type FormEvent } from "react";
import { api, type Schedule, type ScheduleInput, type Settings } from "./api";
import type { components } from "./schema";
import { ErrorText, errorMessage, Modal } from "./ui";
export function validateSchedule(data: ScheduleInput) {
  if (!data.recipient.trim() || !data.body.trim())
    return "Enter an exact recipient and message.";
  if (!/^\+[1-9][0-9]{1,14}$/.test(data.recipient) && !/^[^\s@]+@[^\s@]+$/.test(data.recipient))
    return "Select a matching contact or enter an international number or email.";
  if (!data.start_date || !data.start_time || !data.timezone)
    return "Choose a date, time and timezone.";
  if (data.end_time && data.end_time <= data.start_time)
    return "The window must end later on the same day.";
  if (data.recurrence === "weekdays" && !data.weekdays?.length)
    return "Select at least one weekday.";
  if (
    data.until &&
    (data.recurrence === "once" || data.until < data.start_date)
  )
    return "Choose a valid recurrence end date.";
  if ((data.grace ?? 0) < 0 || (data.grace ?? 0) > 86400)
    return "Grace must be between 0 and 86400 seconds.";
  if (data.transport === "live" && !data.account_id)
    return "Select and verify an iMessage account in Settings first.";
  return "";
}
export function ScheduleForm({
  schedule,
  settings,
  account,
  close,
  saved,
}: {
  schedule?: Schedule;
  settings: Settings;
  account: string | null;
  close: () => void;
  saved: () => void;
}) {
  const s = schedule?.spec;
  const [data, setData] = useState<ScheduleInput>({
    recipient: s?.recipient ?? "",
    recipient_name:
      schedule?.display_name && schedule.display_name !== s?.recipient
        ? schedule.display_name
        : null,
    body: s?.body ?? "",
    start_date: s?.start_date ?? "",
    start_time: s?.start_time ?? "",
    timezone: s?.timezone ?? settings.timezone ?? "America/Chicago",
    end_time: s?.window ? s.end_time : null,
    recurrence: (s?.recurrence as ScheduleInput["recurrence"]) ?? "once",
    weekdays: s?.weekdays ?? [],
    until: s?.until ?? null,
    fold: (s?.fold as ScheduleInput["fold"]) ?? "reject",
    grace: s?.grace ?? settings.grace ?? 300,
    transport: (s?.transport as ScheduleInput["transport"]) ?? "mock",
    account_id: s?.account_id ?? null,
    confirmed: true,
    authorize_recipient: false,
  });
  const [recipientQuery, setRecipientQuery] = useState(schedule?.display_name || s?.recipient || "");
  const [matches, setMatches] = useState<components["schemas"]["RecipientMatch"][]>([]);
  const [searching, setSearching] = useState(false);
  const [lookupStatus, setLookupStatus] = useState("");
  const searchRevision = useRef(0);
  const search = async () => {
    const revision = ++searchRevision.current;
    setSearching(true); setError(""); setLookupStatus(""); setMatches([]);
    try {
      const result = await api.searchRecipients(recipientQuery);
      if (revision !== searchRevision.current) return;
      setMatches(result.matches);
      setLookupStatus(result.status === "authorized"
        ? (result.matches.length ? "Choose the exact number or email below." : "No matching recipients found.")
        : "Name lookup unavailable. Allow Contacts access in Settings or enter a number or email.");
    } catch (e) { if (revision === searchRevision.current) setError(errorMessage(e)); }
    finally { if (revision === searchRevision.current) setSearching(false); }
  };
  const [review, setReview] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const change = <K extends keyof ScheduleInput>(
    key: K,
    value: ScheduleInput[K],
  ) => {
    setData({ ...data, [key]: value });
    setReview(false);
    setConfirm(false);
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    const invalid = validateSchedule(data);
    if (invalid) {
      setError(invalid);
      return;
    }
    if (!review) {
      setReview(true);
      return;
    }
    if (!confirm) {
      setError("Confirm the exact recipient, content and schedule.");
      return;
    }
    setBusy(true);
    try {
      await api.saveSchedule(data, schedule?.id);
      saved();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={schedule ? "Edit schedule" : "Schedule a message"}
      close={() => !busy && close()}
    >
      <form onSubmit={submit} className="form">
        <fieldset disabled={busy}>
          {!review ? (
            <>
              <label>
                Recipient
                <input
                  required
                  value={recipientQuery}
                  onChange={(e) => {
                    searchRevision.current++;
                    setSearching(false); setMatches([]); setLookupStatus("");
                    setRecipientQuery(e.target.value);
                    setData({...data, recipient: e.target.value, recipient_name: null});
                    setReview(false); setConfirm(false);
                  }}
                  placeholder="Name, international number, or email"
                />
              </label>
              <button type="button" disabled={searching || recipientQuery.trim().length < 2}
                onClick={() => void search()}>{searching ? "Searching…" : "Find contact"}</button>
              <p className="note">Searches macOS Contacts only when you click Find contact. No address book is imported.</p>
              {lookupStatus && <p role="status">{lookupStatus}</p>}
              {matches.length > 0 && <ul aria-label="Matching recipients">
                {matches.map((match) => <li key={match.display_name + match.recipient_key}>
                  <button type="button" onClick={() => {
                    searchRevision.current++;
                    setData({...data, recipient: match.recipient, recipient_name: match.display_name});
                    setRecipientQuery(match.display_name); setMatches([]); setLookupStatus("");
                    setReview(false); setConfirm(false);
                  }}>{match.display_name} · {match.recipient}</button>
                </li>)}
              </ul>}
              {data.recipient_name && <p role="status">Selected: {data.recipient_name} · {data.recipient}</p>}
              <label>
                Message
                <textarea
                  required
                  maxLength={4000}
                  rows={4}
                  value={data.body}
                  onChange={(e) => change("body", e.target.value)}
                />
              </label>
              <div className="form-grid">
                <label>
                  Start date
                  <input
                    required
                    type="date"
                    value={data.start_date}
                    onChange={(e) => change("start_date", e.target.value)}
                  />
                </label>
                <label>
                  Exact time / window start
                  <input
                    required
                    type="time"
                    step="1"
                    value={data.start_time}
                    onChange={(e) => change("start_time", e.target.value)}
                  />
                </label>
              </div>
              <label>
                Timezone
                <input
                  required
                  list="zones"
                  value={data.timezone}
                  onChange={(e) => change("timezone", e.target.value)}
                />
                <datalist id="zones">
                  {[
                    "America/Chicago",
                    "America/New_York",
                    "America/Los_Angeles",
                    "Europe/London",
                    "UTC",
                  ].map((z) => (
                    <option key={z}>{z}</option>
                  ))}
                </datalist>
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={!!data.end_time}
                  onChange={(e) =>
                    change("end_time", e.target.checked ? "23:59:00" : null)
                  }
                />{" "}
                Randomize inside a window
              </label>
              {data.end_time && (
                <label>
                  Window end
                  <input
                    required
                    type="time"
                    step="1"
                    value={data.end_time}
                    onChange={(e) => change("end_time", e.target.value)}
                  />
                </label>
              )}
              <div className="form-grid">
                <label>
                  Repeat
                  <select
                    value={data.recurrence}
                    onChange={(e) => {
                      setData({
                        ...data,
                        recurrence: e.target
                          .value as ScheduleInput["recurrence"],
                        weekdays: [],
                        until: null,
                      });
                    }}
                  >
                    <option value="once">One time</option>
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly on start weekday</option>
                    <option value="weekdays">Selected weekdays</option>
                  </select>
                </label>
                <label>
                  Grace period (seconds)
                  <input
                    type="number"
                    min={0}
                    max={86400}
                    value={data.grace}
                    onChange={(e) => change("grace", Number(e.target.value))}
                  />
                </label>
              </div>
              {data.recurrence === "weekdays" && (
                <div className="weekdays">
                  {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(
                    (day, i) => (
                      <label key={day}>
                        <input
                          type="checkbox"
                          checked={data.weekdays?.includes(i)}
                          onChange={(e) =>
                            change(
                              "weekdays",
                              e.target.checked
                                ? [...(data.weekdays ?? []), i]
                                : data.weekdays?.filter((d) => d !== i),
                            )
                          }
                        />
                        {day}
                      </label>
                    ),
                  )}
                </div>
              )}
              {data.recurrence !== "once" && (
                <label>
                  Repeat until (optional)
                  <input
                    type="date"
                    value={data.until ?? ""}
                    onChange={(e) => change("until", e.target.value || null)}
                  />
                </label>
              )}
              <div className="form-grid">
                <label>
                  Daylight saving ambiguity
                  <select
                    value={data.fold}
                    onChange={(e) =>
                      change("fold", e.target.value as ScheduleInput["fold"])
                    }
                  >
                    <option value="reject">Reject ambiguous time</option>
                    <option value="earlier">Earlier instant</option>
                    <option value="later">Later instant</option>
                  </select>
                </label>
                <label>
                  Transport
                  <select
                    value={data.transport}
                    onChange={(e) => {
                      const transport = e.target.value as "mock" | "live";
                      setData({
                        ...data,
                        transport,
                        account_id: transport === "live" ? account : null,
                      });
                    }}
                  >
                    <option value="mock">Mock · no real sending</option>
                    <option value="live">
                      iMessage · separate worker required
                    </option>
                  </select>
                </label>
              </div>
              <label className="check">
                <input
                  type="checkbox"
                  checked={data.authorize_recipient}
                  onChange={(e) =>
                    change("authorize_recipient", e.target.checked)
                  }
                />{" "}
                Authorize this recipient for outbound messages if needed
              </label>
            </>
          ) : (
            <div className="review">
              <p className="eyebrow">Review exact authorization</p>
              <h3>{data.recipient_name || data.recipient}</h3>
              {data.recipient_name && <p>{data.recipient}</p>}
              <p className="message">{data.body}</p>
              <dl>
                <dt>Schedule</dt>
                <dd>
                  {data.start_date} at {data.start_time}
                  {data.end_time && ` – ${data.end_time} (random)`}
                </dd>
                <dt>Timezone</dt>
                <dd>{data.timezone}</dd>
                <dt>Recurrence</dt>
                <dd>
                  {data.recurrence}
                  {data.weekdays?.length
                    ? ` · ${data.weekdays.map((i) => ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][i]).join(", ")}`
                    : ""}
                  {data.until && ` · until ${data.until}`}
                </dd>
                <dt>Grace / DST</dt>
                <dd>
                  {data.grace}s / {data.fold}
                </dd>
                <dt>Transport / account</dt>
                <dd>
                  {data.transport} {data.account_id}
                </dd>
                <dt>Recipient permission</dt>
                <dd>
                  {data.authorize_recipient
                    ? "Grant outbound permission"
                    : "Require existing allowlist permission"}
                </dd>
              </dl>
              {schedule && (
                <p>
                  The old plan is cancelled and its history retained. This
                  replacement needs fresh authorization.
                </p>
              )}
              <p>
                Random execution times are selected once and shown after
                authorization. This dashboard does not run a worker.
              </p>
              <label className="check">
                <input
                  type="checkbox"
                  checked={confirm}
                  onChange={(e) => setConfirm(e.target.checked)}
                />{" "}
                I authorize this exact recipient, message and schedule.
              </label>
            </div>
          )}
          <ErrorText error={error} />
          <div className="actions">
            {review && (
              <button
                type="button"
                onClick={() => {
                  setReview(false);
                  setConfirm(false);
                }}
              >
                Back to edit
              </button>
            )}
            <button className="primary" disabled={busy || (review && !confirm)}>
              {busy
                ? "Saving…"
                : review
                  ? "Authorize schedule"
                  : "Review schedule"}
            </button>
          </div>
        </fieldset>
      </form>
    </Modal>
  );
}
