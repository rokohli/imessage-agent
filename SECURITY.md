# Security and privacy

## Current scope

Version 0.1.0 is an offline, mock-only foundation. There is no live Messages or
Contacts reader, AppleScript transport, AI client, background worker, or
randomized scheduler. The adapter factory rejects live mode, and the outbound
gate accepts only the concrete mock adapter. No macOS permission is required.
Future live integration needs a separate security review; these controls are not
evidence that autonomous replies are safe to deploy.

## Trust model

Incoming message content must be treated as untrusted data. Contact administration
and approval creation are trusted local operations, not AI tools. Each mock
dispatch requires an exact allowlisted recipient and an unused, unexpired approval
bound to the recipient and body. Approval consumption and attempt reservation are
atomic. Unknown outcomes are retained without automatic retry; the system cannot
guarantee exactly-once delivery for a future transport.

There is no authenticated human approval UI yet. Python APIs do not isolate
malicious code running under the same user. Do not expose the store or outbound
API to untrusted code or a model. Real transport would require process/capability
isolation, rate limits, loop prevention, reconciliation, and a kill switch.

## Data handling

The store opens an explicit agent-state path, never discovers Apple databases,
and creates new database files with mode 0600. It rejects file symlinks and
permissive database files. Use a trusted, private parent directory; this does not
defend against another process with the same user's privileges.

SQLite state is plaintext and can contain message bodies, recipient addresses,
contacts, approvals, and schedules. Payload hashes are not anonymization.
Encryption, retention, secure deletion, backup exclusions, and log redaction are
not implemented. Do not ingest personal data into this foundation. The package
configures no telemetry, but a caller can still expose data through its own logs
or backups.

Tests use synthetic messages, fictional phone fixtures, and example-domain email
addresses. Keep real conversations, contacts, credentials, databases, local
configuration, and crash dumps out of commits and issue reports. `.gitignore`
reduces accidental inclusion but is not a security boundary; inspect staged files
before every commit. The attributed-body decoder is best effort and has only
synthetic fixture coverage, not real macOS compatibility proof.

## Reporting a vulnerability

Do not post credentials, personal conversations, or exploit details in a public
issue. If GitHub's private vulnerability reporting is available, use the
repository's **Security → Advisories → Report a vulnerability** control. Otherwise,
open a public issue containing only a request for a private reporting channel,
without sensitive details, and wait for a maintainer to arrange one.

Include the affected commit/version, a minimal synthetic reproduction, expected
and observed behavior, and impact in the private report. This early project has
no guaranteed response time or supported production deployment; fixes target the
current development version.
