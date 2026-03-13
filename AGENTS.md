# AGENTS.md

## Project Scope

- This repository is a single-user utility for monitoring mail via IMAP IDLE and sending Pushover notifications.
- Prefer stability, observability, and simple deployment over abstraction or architectural expansion.
- Unless the user explicitly asks for it, do not turn this into a multi-account, multi-service, or highly modular system.

## Working Style

- Prefer small, local edits over broad refactors.
- Keep the main implementation in `mail_monitor.py` unless the current file shape becomes clearly unmanageable.
- Do not add dependencies unless there is a concrete operational need.
- Preserve the existing Docker-based workflow unless the user asks to change deployment.

## Behavioral Constraints

- New mail detection is UID-based incremental processing. Do not revert to scanning all `UNSEEN` messages by default.
- `DRY_RUN=true` must remain safe: it may connect and log, but must not send notifications or modify mail flags.
- Be careful when changing IMAP IDLE handling, proxy logic, or reconnect behavior. These are the highest-risk parts of the project.
- For this project, a simpler and more predictable implementation is preferred to a clever one.

## Validation

- After code changes, run at least `python3 -m py_compile mail_monitor.py`.
- If behavior touches live mail delivery or read-state changes, prefer validating with `DRY_RUN=true` first.
- Do not claim end-to-end IMAP or Pushover verification unless real credentials and live execution were actually used.

## Secrets And Local Data

- Never commit `.env`, `config.json`, logs, credentials, or real mailbox details.
- Use placeholder values in examples and documentation.
- Avoid printing secrets in logs, diffs, or summaries.

## Git Conventions

- Use the repository's configured local Git identity.
- Keep GPG signing enabled for commits.
- Do not rewrite history or force-push unless the user explicitly asks for it.
- Do not push changes unless the user explicitly asks for it.
