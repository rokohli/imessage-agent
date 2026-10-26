# Contributing

Use Python 3.10 or newer and work from the repository root. The project uses the
standard library only; development does not require dependencies, API keys,
macOS permissions, an upstream checkout, or a real Messages account.

Run all offline tests without installing the package:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
git diff --check
```

Tests must create synthetic data in temporary directories and clean it up. Never
discover or open real Messages/Contacts databases, execute messaging automation,
contact AI APIs, or require network access. Use fictional phone numbers and
example-domain addresses. Do not introduce a live-mode toggle as part of a routine
change. Package installation/build validation is separate from source-based tests
and may require build tooling.

Keep pull requests small and explain the behavior changed, relevant limitations,
and test command/results. Add focused regression coverage for non-trivial changes.
Preserve default-deny policy, exact payload approvals, atomic cursor/attempt
updates, and explicit unknown outcomes. Distinguish implemented features from
plans in documentation.

Before committing, review `git diff`, `git diff --cached`, and the staged file
list. Never include personal messages, contacts, local state, credentials, logs,
or configuration. See [SECURITY.md](SECURITY.md) for vulnerability reporting.
Retain upstream copyright notices and update [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
when adapting third-party code. Contributions are distributed under this project's
[MIT license](LICENSE).
