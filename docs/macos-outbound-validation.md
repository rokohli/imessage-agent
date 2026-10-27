# Explicit iMessage account setup and no-send validation

The operator explicitly selects one positively identified iMessage account.
Unrelated accounts with unreadable service types do not prevent that selection.
No account index, protocol guess, display name or fuzzy contact match is used for
sending. The previous UNKNOWN attempt remains recorded and must not be retried.

## One-time local setup

Run in the same macOS terminal/host and installed Python context you intend to
use for a later separately authorized supervised test:

```sh
cd /path/to/imessage-agent
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/src"
/opt/anaconda3/bin/python3 -m imessage_agent setup-imessage-account
```

Setup lists only accounts whose own service type positively returned iMessage,
using labels such as `1. iMessage account 1`. Enter the desired number. Account
IDs, names and descriptions are not displayed. A number is a temporary menu
choice, not a saved account index. The operator's selection authorizes that
specific positively verified account; it does not assert global uniqueness.
If there are several indistinguishable labels, stop unless you can confidently
identify your intended account in the local setup context. Do not guess.

Before saving, setup resolves the selected stable ID again and checks its exact
identity, iMessage service, enabled flag and connected status. Missing/duplicate
ID matches, changed identity, SMS/RCS service or unavailable metadata fail closed.
Unrelated service-type errors are ignored while listing candidates; Automation
authorization errors are reported rather than ignored. No recipient, chat,
message body, Messages database or outbound state is opened by setup.

The one selection is saved atomically in:

```text
~/.local/state/imessage-agent/imessage-account.json
```

The file contains a private stable account ID, not message contents. New files
are mode 0600 in an owner-only directory. Setup refuses an existing selection,
including symlinks; it never overwrites one. Reads reject malformed, permissive,
nonregular and multiply linked files. Use trusted private parent directories;
these protections do not isolate malicious same-user code. A changed account
requires a separately reviewed local reconfiguration, not an automatic fallback.
There are no account-related environment settings or additional config files.

Setup sends nothing and does not enable sending. The public adapter interface
remains `AppleScriptMessagesAdapter(enabled=False)` by default, with the same
trusted `Outbound.submit` approval gate. Explicit `enabled=True` remains required
for any later send. Importing the package never runs setup or AppleScript.

## Validate the saved account without sending

```sh
/opt/anaconda3/bin/python3 -m imessage_agent diagnose-applescript
```

The default diagnostic now resolves the saved selection, rather than requiring
all accounts to be classified. Expect `accounts: 1` and `imessage_accounts: 1` on
success: these count the saved ID match, not every Messages account.

The production resolver uses only an ID filter on the account collection,
requires exactly one match, confirms that account's exact ID, and checks only
that account's service type, enabled state and connection status. Unreadable
service-type properties on unrelated accounts are never requested. A missing or
invalid selection fails before any AppleScript request. No failed selected-account
check ever causes another account to be selected.

## Validate one explicitly authorized exact recipient without sending

```sh
/opt/anaconda3/bin/python3 -m imessage_agent diagnose-applescript --check-recipient
```

This flag authorizes checking one canonical fictional test address entered at a
hidden interactive prompt. The address stays out of argv, shell history, files,
and printed reports. Noninteractive/echoed prompt fallback is refused.
The check uses the dictionary's participant-ID example (`account-ID:handle`) to
request one exact participant directly. It then independently verifies that
participant's ID, case-sensitive handle and owning account ID against the saved
account. This avoids the filtered participant-collection query that returned
-10000 at `recipient_lookup` on the owner's Mac. It never creates a participant,
uses contact names, selects a chat/group or sends a message. Success adds
`recipient_matches: 1` only after all three checks pass. The dictionary gives an
ID example, not a guarantee across all macOS versions: an unsupported direct-ID
format fails closed, without trying display names, chats or another account.
The revised direct lookup has mocked and compile-only coverage; the operator
must repeat the no-send check to establish runtime compatibility. Share only the aggregate output or fixed error codes.

Existing `--probe-accounts` and `--correlate-accounts` remain optional historical
troubleshooting tools. Their global uniqueness assessment is not the explicit
selection policy and is not a prerequisite for using a verified saved account.
No additional account-diagnostic framework is needed for normal setup.

## Failures, permissions and routing limits

Exit 0 means the requested no-send checks succeeded. Exit 1 reports fixed errors;
exit 2 is sanitized CLI misuse. Important codes include:

- `account_selection_required`, `account_selection_unreadable`,
  `unsafe_account_selection`, `account_selection_already_exists`.
- `no_verified_imessage_accounts`, `invalid_account_choice`.
- `selected_account_missing`, `selected_account_ambiguous`,
  `selected_account_identity_mismatch`, `selected_account_not_imessage`.
- `imessage_account_disabled`, `imessage_account_disconnected`.
- `recipient_not_found`, `recipient_ambiguous`, `recipient_account_mismatch`.
- `automation_not_authorized`, plus staged numeric Apple event errors.

Diagnostics report only fixed stages/codes and counts. Service/property errors
are not mislabeled as disconnection. Setup's private child-process result may
contain candidate IDs in captured memory; those IDs are never forwarded to logs
or stdout, except into the explicitly authorized private selection file.

Messages scripting requires existing macOS **Automation** permission for the
responsible host to control Messages. This differs from database-read/Full Disk
Access. Permission in one terminal/IDE does not establish permission for another
host. No Contacts or Accessibility API is used. See Apple's
[Automation guidance](https://support.apple.com/en-au/guide/mac-help/mchl07817563/mac).
Do not change permissions during validation without separate authorization. Stop
if access is denied or a new permission prompt appears. No helper/daemon is
installed.

The transport requests only a participant owned by the selected, positively
verified iMessage account. There is no SMS/RCS selection, fallback branch, retry,
group destination or attachment API. AppleScript does not expose an API here to
prove downstream network routing or delivery. Before any later test, the operator
must confirm Messages' automatic SMS fallback is disabled under separately
authorized settings changes; do not change settings as part of this setup.
Do not treat transport acceptance as proof of iMessage delivery.

## One supervised live test (operator execution only)

The operator has verified the saved iMessage account and exact recipient with the
no-send command. That does not authorize a send by an agent. The command below
is for one deliberate human invocation only. It does not install a worker or
enable automatic sending. Do not run it in a retry wrapper, scheduler or loop.

Use the **same existing outbound state** that records the prior UNKNOWN attempt.
The path below is the earlier documented outbound test path; if your prior test
used another path, set the variable to that exact existing file instead. Do not
use the ingestion inbox or a new database. The command refuses missing files,
unsafe paths, unexpected schemas and state without an existing UNKNOWN attempt.
It does not migrate, clear, reset or reclassify previous attempt/approval records.
Keep other writers stopped during this supervised test.

Run manually from the project root, after separately authorizing one consenting
recipient and one NEW test message:

```sh
cd /path/to/imessage-agent
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/src"
OUTBOUND_STATE="$HOME/.local/state/imessage-agent-validation/outbound-test.sqlite3"
/opt/anaconda3/bin/python3 -m imessage_agent send-supervised-test --state "$OUTBOUND_STATE"
```

The interaction is deliberately sequential:

1. Enter the authorized canonical recipient at a hidden prompt.
2. Enter freshly composed single-line fictional text at a hidden prompt. Do not
   enter the prior UNKNOWN text. Previously attempted recipient/payloads and
   previously reserved generated text are rejected, before any new approval.
3. The command rechecks the saved account and recipient without sending.
4. To approve exactly that entered recipient/text for one attempt, type
   `SEND ONE NEW IMESSAGE` at the confirmation prompt. Anything else cancels.
5. Only then does it open state for writes, temporarily opt in the recipient,
   create one fresh 120-second exact-payload approval and fresh attempt ID, and
   call the existing `Outbound.submit` once with the real adapter explicitly
   enabled. It restores the previous contact opt-in afterward; a previously
   absent contact remains opted out.

The production adapter reads the saved account selection and uses its exact ID
match, iMessage/enabled/connected checks and direct participant ID/handle/account
ownership checks. It does not use the old global service-type filter. Atomic
claims, payload suppression and rate limits remain in force. Account changes,
a new state file or new attempt IDs must never be used to retry the old message.

Output is JSON containing `status` (`ACCEPTED`, `FAILED` or `UNKNOWN`) and a fixed
`diagnostic`, with fixed stage/numeric fields for a failed no-send preflight.
It prints no recipient, body, account ID, state path or attempt/approval ID.
Exit codes are 0 accepted, 1 failed/cancelled, 2 CLI misuse, 3 unknown. Nonzero
exit is **not** permission to retry. A `cleanup_error` requires local review of
contact/state cleanup; it never authorizes redispatch.

A timeout, interruption or failure persisting the result can follow an actual
send. These return UNKNOWN and leave the durable reservation/consumed approval
for reconciliation. If the process is killed before it can print output, also
treat the attempt as unresolved: do not rerun the command. The earlier UNKNOWN
record and suppression rows are never changed by this command. An uncatchable
process death can prevent temporary contact restoration, but cannot restore a
consumed approval or start an automatic retry. This trusted local terminal flow
is not an authenticated multi-user approval service.

### Verify the single result

After the one invocation, inspect Messages locally in the intended individual
conversation. Confirm that exactly one copy of the newly entered text appears
from the expected sending identity, and check any iMessage/Delivered/Not Delivered
indicators without sharing screenshots, contents or identifiers. Confirm with
the consenting recipient that they received exactly one matching iMessage.

ACCEPTED means only that the AppleScript command returned; it is not a delivery
receipt. Manual sender and recipient observations are separate evidence and do
not automatically change the stored outcome to SENT or DELIVERED. A FAILED
result can be a no-send policy/preflight denial; do not attempt to work around it
with another account, message or state file. Stop on UNKNOWN, missing output,
unexpected routing, duplicate copies, delivery failure or recipient nonreceipt.
Do not resend, even when nothing is visible immediately.

## Implementation evidence

Offline tests mock AppleScript and use temporary synthetic account files/state.
They cover one-shot confirmation, fresh-body rejection, one-dispatch limits,
timeouts/interruption/result-write failures and prior-history preservation, plus
explicit selection, revalidation, unrelated-account failures, exact
recipient ownership, missing/stale/wrong-service selections, privacy and durable
UNKNOWN suppression across account changes. Setup and both no-send validation
scripts pass compile-only checks against the local AppleScript compiler. No
setup against personal accounts, recipient-specific live query or real send was
executed during implementation. Actual account/recipient resolution must be
validated by the operator using the commands above.
