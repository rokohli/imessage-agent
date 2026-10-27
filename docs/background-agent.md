# Phase 6: per-user background agent

The background worker calls the existing Phase 4 `Scheduler.tick`. No model,
second scheduler, transport, automatic AI reply or cloud service is introduced.
It runs independently of Terminal and the dashboard after explicit installation
and start. Nothing in development or the dashboard installs or starts it.

## Install and manage

Use Python 3.10+ and an existing environment with this project installed. For
notifications, installation requires Apple's Command Line Tools (`xcrun swiftc`
and `codesign`); no Python dependency or sudo is added. Install from a stable
checkout and keep its interpreter/virtual environment available. The installer
captures the actual absolute interpreter (including its venv path), source path,
working directory and state path. Reinstall after moving these paths.

From the project directory, use the existing **agent state**, never Apple's
Messages database. If your dashboard uses another state path, use that exact path.
The state must already exist, be private, and be outside a repository.

```sh
cd /Users/rohankohli/imessage-agent
PY="$PWD/.venv/bin/python"
STATE="$HOME/.local/state/imessage-agent/state.sqlite3"
"$PY" -m imessage_agent launchagent install --state "$STATE"
"$PY" -m imessage_agent launchagent start
"$PY" -m imessage_agent launchagent status
"$PY" -m imessage_agent launchagent stop
"$PY" -m imessage_agent launchagent restart
"$PY" -m imessage_agent launchagent uninstall
```

Installation writes files only; it **does not bootstrap or activate** the job.
The default worker executes only mock schedules. To enable execution of previously
authorized live schedules, stop and uninstall a mock installation, then explicitly
install with `--execute-live` and start it. This permits execution of **all due,
previously authorized live plans in that state**. Review the plans first:

```sh
"$PY" -m imessage_agent schedule-list --state "$STATE"
"$PY" -m imessage_agent launchagent install --state "$STATE" --execute-live
"$PY" -m imessage_agent launchagent start
```

Do not enable live execution until you explicitly authorize it. Installation flags
are captured at install time; adding a flag to `start` does not alter the plist.
Only `install` accepts configuration changes (uninstall and reinstall to change
transport, state or interval). Use `--interval 10` at installation to set 1–60
seconds between polls. Never run a foreground scheduler alongside validation.

The job is `io.github.rokohli.imessage-agent` in `gui/$(id -u)` and the plist is
`~/Library/LaunchAgents/io.github.rokohli.imessage-agent.plist`. `start` uses
`bootstrap` and `kickstart`; `stop` uses `bootout` so KeepAlive cannot undo it.
A stopped but installed job loads again at next login; uninstall prevents that.
Unexpected termination restarts with a 15-second launchd throttle. Normal SIGTERM
shutdown exits successfully. Stop allows the in-flight bounded AppleScript to
finish; no new occurrence begins after shutdown is requested.

The Mac must be **awake**, logged into the appropriate macOS GUI user session,
and Messages must have the selected iMessage account available. launchd does not
wake the Mac, keep it awake or provide a Messages login. Automation permissions
that worked in Terminal may not carry over to a LaunchAgent's Python executable.
Validate that separately; do not bypass TCC or enable broader permissions blindly.
No Messages history access is required by this worker. The dashboard and Ollama
are never configured to start automatically.

## Native notifications

Installation compiles and ad-hoc signs a small `Notifier.app` in
`~/Library/Application Support/iMessage Agent/`. It uses Apple's
`UNUserNotificationCenter` with a stable bundle identity and launches through
Launch Services (`open`), independently of Terminal. It does not use AppleScript
notification banners or invoke a shell. Request OS permission explicitly:

```sh
"$PY" -m imessage_agent launchagent notifications-authorize
"$PY" -m imessage_agent launchagent notifications-test
```

Allow notifications for **iMessage Agent** in System Settings > Notifications.
Enable **Private macOS notifications** in dashboard Settings and save. This
preference defaults off and is read on each worker poll. OS permission and the
local preference are separate. Focus mode/system settings can suppress banners;
`notifications-test` requests presentation, it cannot prove you saw it.

Events cover accepted/sent/delivered, failed, partial, UNKNOWN, missed window,
pending draft approval, and worker attention. ACCEPTED means the Messages
transport accepted the request; it is **not** delivery confirmation. Events contain
no message body, handle, contact name, history or account ID. No notification
click runs a command, grants approval or retries a message.

Notification observation is recorded durably *before* presentation. A crash or
notification failure may lose a banner, never cause a resend. Old observed events
are not replayed when notifications are enabled. Event keys survive restart and
uninstall alongside send state; do not clear them to troubleshoot delivery.
Worker attention errors are coalesced until the category changes or polling
recovers. Failure to build the native helper prevents installation, leaving no
new plist. OS delivery from the LaunchAgent is a separate manual acceptance check.

Apple references: [notification authorization](https://developer.apple.com/documentation/usernotifications/unusernotificationcenter/requestauthorization(options:completionhandler:))
and [launchd property definitions](https://github.com/apple-oss-distributions/launchd/blob/main/man/launchd.plist.5).

## Health, privacy and recovery

`GET /api/worker` on the existing protected loopback dashboard reports status,
heartbeat, last successful poll, pending occurrence count, next materialized
execution, recent sanitized errors, transport and last reserved occurrence outcome.
The overview and Settings refresh this status every ten seconds. `launchagent
status` reports installation/loading only; a loaded job is not proof of health.

Running requires the per-state OS lock, a heartbeat and successful scheduler poll
no older than 90 seconds, and no current poll/notification error. Stale/future
heartbeat, blocked poll or held lock without fresh health means **attention**.
No lock means **stopped**, even if a heartbeat is recent. A failed health API read
shows an error, never a healthy indicator. A historical failed send does not mean
the process stopped; review Recent errors and Activity. Pending/next values include
all authorized transports; the displayed worker transport identifies what it will
execute. Far-future recurring occurrences are materialized within Phase 4's
seven-day horizon, so next execution may say None materialized.

A per-state `flock` is held for the entire process lifetime. A second background
worker exits without opening state or executing. The lock file is never unlinked;
the OS releases the lock on process exit/crash. Existing Phase 4 atomic claims also
protect against independently started foreground scheduler processes. SQLite
errors retry the **poll**, after the configured interval, not a reserved send.
Each poll opens/closes its own connection; startup database locks recover too.

Phase 4 still reserves UNKNOWN before dispatch. A crash between reservation and
outcome recording leaves UNKNOWN and is never retried, even when no dispatch can
be proven. Existing attempt/approval/account/recipient verification remains intact.
Failed account/permission checks surface as failed or UNKNOWN according to the
existing transport; they never turn into automatic resend attempts. If outcome
persistence fails, reserved state remains conservative. Expired unclaimed
occurrences become MISSED; random windows never extend, exact-time grace is
preserved. Recurrences retain their unique plan/date key and persisted random time.
Long backlogs advance at most 366 calendar days per tick, as in Phase 4.

Logs are private JSON lines in
`~/Library/Application Support/iMessage Agent/logs/worker.jsonl`: timestamp,
lifecycle/event, plan ID, occurrence ID, outcome and fixed error category only.
Rotation retains the active file plus three backups of about 1 MiB each. No raw
exception strings, payloads, account/recipient data or credentials are logged.
launchd's unbounded stdout/stderr streams go to `/dev/null`; unexpected crashes
show through restart/stale health, not a private traceback in an unbounded file.
Stop/uninstall never delete agent SQLite, approvals, claims, lock file, or logs.
State upgrades to v6 add only notification observation keys and retain older state.
Use this version for read-only replay/diagnostics against v6; older tools may
correctly refuse an unfamiliar schema. Back up state while workers are stopped.

## Supervised local acceptance — requires explicit live-send confirmation

These steps are instructions only. Automated tests never send iMessages or
activate launchd. Before proceeding, explicitly confirm that you authorize this
live test and execution of the reviewed active live schedules. Stop other workers;
inspect/cancel unwanted pending plans. Do not use an UNKNOWN occurrence as a test.

1. Set `PY` and `STATE` as above. Confirm the selected account with the existing
   no-send diagnostic. If none is selected, run interactive setup first:

   ```sh
   "$PY" -m imessage_agent diagnose-applescript
   # Only if no account is configured:
   "$PY" -m imessage_agent setup-imessage-account
   ```

2. Install with `--execute-live` (uninstall any prior installation first). Start
   the job. Run `launchagent status` and check `/api/worker` in the dashboard:
   running, fresh heartbeat/poll, transport live. Resolve attention before creating
   a live test. If macOS requests Automation access, verify the responsible process.
3. Grant native notification permission using the commands above; visually verify
   the test notification. Enable dashboard notification settings and save.
4. Close Terminal and the dashboard/browser. Wait at least two poll intervals.
   Reopen Terminal, check `launchagent status`; reopen the dashboard and confirm
   heartbeat/poll advanced while both were closed. The background worker must
   remain running.
5. Create one harmless message to **your own verified canonical iMessage handle**
   interactively, at least five minutes in the future. The CLI hides the input,
   verifies the recipient/account, displays exact authorization and requires
   typing `AUTHORIZE LIVE SCHEDULE`. That is the final live-send gate. Example
   commands choose a future local date/time without putting the handle or text in
   shell history:

   ```sh
   TEST_DATE=$(date -v+5M +%Y-%m-%d)
   TEST_TIME=$(date -v+5M +%H:%M:%S)
   "$PY" -m imessage_agent schedule-create --state "$STATE" \
     --date "$TEST_DATE" --time "$TEST_TIME" --timezone America/Chicago \
     --transport live --grace 300
   ```

   Use the timezone matching the `date` output; change both if your Mac uses a
   different timezone. Record the returned schedule ID as `PLAN`. Close Terminal
   and dashboard again before the scheduled instant; keep the Mac awake.
6. Observe exactly one new message on your own conversation/device and a native
   notification. An accepted record alone is not proof of delivery. Reopen Terminal:

   ```sh
   PLAN='the-returned-schedule-id'
   "$PY" -m imessage_agent schedule-history --state "$STATE" --id "$PLAN"
   ```

   Verify exactly one occurrence and durable transport status. If UNKNOWN or
   PARTIAL, stop and investigate manually; **never retry that occurrence**.
7. Run `launchagent restart`. Wait two polls and check the same history and your
   conversation again. The occurrence/attempt must remain unchanged, with no
   duplicate send or duplicate outcome notification. Verify last outcome and
   heartbeat in the dashboard. This step must not create a replacement schedule.
8. Run `launchagent stop`, confirm dashboard stopped, then `launchagent uninstall`.
   Confirm `launchagent status` says installed false/loaded false and heartbeat
   stops advancing after more than two intervals. Confirm the plist and Notifier
   app are removed, while state/history/logs remain. A normal stop during an
   in-flight send may finish that one attempt before shutdown.

Separate supervised checks still needed: sleep across a due window (expect MISSED
after expiry); sleep within grace (at most one send); unavailable account;
revoked Automation permission; process kill after reservation; actual notification
permission/Focus behavior and notification delivery with Terminal closed. Use
synthetic/mock plans for recovery checks unless separately authorizing real sends.

## Before Phase 3B

Offline implementation is not macOS operational proof. Complete the supervised
launchd/Terminal-close, permission attribution, one-send/restart, notification and
clean-uninstall checks above on the target Mac before relying on unattended live
schedules. Broader sleep/wake and account-disconnection checks remain operational
acceptance items. Autonomous replies stay disabled; Phase 3B needs its own scope
and approval policy. Never use Phase 6 readiness to authorize AI-generated sends.
