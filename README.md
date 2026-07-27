# claude-web-console

Password-gated web chat front-end for a headless Claude Code daemon running on a VPS.

## What it does

- Single-password login (`POST /login`), PBKDF2-hashed at rest, rate-limited (5 attempts / 5 min).
- On success, sets a random `HttpOnly` + `Secure` session cookie (1 year) — enter the password once,
  the browser stays signed in after ("one-time write" auth).
- Authenticated chat (`POST /api/chat`) forwards each message to `claude -p` on the host and returns
  the reply, resuming the same Claude session for conversational continuity.
- Serves over TLS using a Let's Encrypt certificate for a free `sslip.io` hostname that resolves
  automatically to the server's IP (no domain purchase or registrar signup required).
- `/dashboard` (same session cookie as the chat) shows a live view of Claude token usage
  (input/output tokens vs a configurable limit) and host CPU/RAM/disk usage.

## Deploy on a fresh VPS

```bash
PASSWORD='your-password' EMAIL='you@example.com' ./bootstrap.sh
```

Installs certbot, requests a cert for `<ip-with-dashes>.sslip.io` (or `$DOMAIN` if set), generates
`config.json` (password hash) and `sessions.json`, installs and starts the `claude-web` systemd service.

`config.json` and `sessions.json` hold local secrets/state and are gitignored — never commit them.

## Privilege model: runs entirely as a non-root user

Both the web server itself and the `claude -p --dangerously-skip-permissions` calls it makes run
as an unprivileged system user (`daemon_user` in `config.yaml`, default `claudeweb`) — not root.
`bootstrap.sh` creates that user with no sudo rights and no privileged group membership, and the
systemd unit runs the whole process as it, granted only `CAP_NET_BIND_SERVICE` (so it can still
bind port 443 without being root) plus standard systemd sandboxing
(`ProtectSystem=strict`, `ProtectHome`, `NoNewPrivileges`, `PrivateTmp`, restricted capability set).

This matters even on a single-purpose VPS with nothing else on it: root vs. a properly-sandboxed
non-root user isn't about protecting other tenants here, it's about **recoverability**. Root could
rewrite `authorized_keys`, disable `sshd`, flush firewall rules, or otherwise lock out the operator
if the daemon misbehaves or the login credential leaks; a no-sudo, no-privileged-group user cannot
do any of that — however badly it acts, there's always a way back in from outside it.

What this does *not* remove: `--dangerously-skip-permissions` still means no per-action
confirmation for whatever `daemon_user` itself can touch, and the `/night` autonomous loop still
runs unattended for hours with no human review in between. Both are accepted, scoped tradeoffs of
this design, not fixed by the privilege drop — treat this deployment as **remote code execution,
scoped to the daemon user's own permissions, gated by a single password** (see "Session lifetime"
below for how long that password's session lasts once entered).

The daemon user needs its own Claude Code login (never copy another account's
`~/.claude/.credentials.json` onto this box) — run once, interactively, after bootstrap:

```bash
su - claudeweb -c 'claude setup-token'
```

## Session lifetime

A logged-in session is valid for `session_max_age_secs` in `config.yaml` (default 7 days), enforced
server-side in `core/auth.py` — not just as the cookie's own `Max-Age`, so a copied/replayed
session token stops working once it ages out even if the client holding it ignores expiry.

## Dashboard

`/dashboard` reads token counts from local Claude Code session transcripts
(`~/.claude/projects/**/*.jsonl`, both root's and `$DAEMON_USER`'s) — no extra
instrumentation on the chat path. A background thread in `app.py` rescans them
every 5 minutes and appends a snapshot to `token_usage.json` (gitignored, holds
up to a week of 5-minute samples). The page polls `GET /api/stats` every 15s for
the latest totals plus live CPU/RAM/disk stats (read from `/proc` and
`statvfs`, no `psutil`/third-party dependency).

Set `"token_limit"` in `config.json` to the token budget you want the usage
meter measured against (input + output tokens); set it to `0` to disable the
meter and just show raw counts.

## Files

Python stdlib only, no dependencies. Grouped into packages so no directory holds more than
a handful of files:

- `app.py` — entry point: starts the token-usage collector thread and the HTTP server.
- `core/` — config and persistence, no HTTP or subprocess concerns.
  - `settings.py` — config constants (paths, TLS, daemon user, timeouts).
  - `storage.py` — `config.json` / `sessions.json` persistence.
  - `auth.py` — password hashing/check, session cookies, login rate limiting.
- `daemon/` — everything that talks to the `claude` CLI as the daemon user.
  - `claude_daemon.py` — runs `claude -p`, injecting `.env` credentials into just that
    subprocess's environment. Shared by the chat handler and night mode.
  - `night_mode.py` — the ~8h autonomous overnight run loop (see below).
  - `night_control.py` — web-side glue for the `/night` chat command (start/status/stop).
- `web/` — the HTTP layer.
  - `server.py` — the `Handler` (HTTP routing) and the TLS server class.
  - `templates.py` — the login/chat/dashboard HTML pages.
  - `dashboard.py` — token-usage + host-stats collection for `/dashboard`.
- `var/state/`, `var/log/` — runtime state and logs, gitignored (`config.json`,
  `sessions.json`, `token_usage.json`, `night_task.txt`, `night_mode.pid`,
  `app.log`, `night_mode.log`, `night_mode_cron.log`).
- `bootstrap.sh` — fresh-VPS setup script (creates `var/state` and `var/log`).

## Night mode

Send `/night <task>` in the chat to kick off an unattended ~8h `claude -p` loop on that task
(auto-resuming the session after every call — success, error, or timeout — until the task
says it's done or the time budget runs out). `/night status` shows whether it's running plus
a log tail; `/night stop` sends it SIGTERM. Budget is `NIGHT_MODE_BUDGET_SECS` (default 8h),
per-call cap is `NIGHT_MODE_MAX_CALL_SECS` (default 1h).
