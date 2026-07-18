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

## Current known limitation: root-privileged daemon

The deployed daemon currently runs as **root** and calls `claude -p` with
`--dangerously-skip-permissions` so it can act (edit files, run shell commands) without an
interactive approval prompt (there's no TTY on a background web request).

The Claude Code CLI refuses to combine `--dangerously-skip-permissions` with root/sudo — by
design, as a safeguard against exactly this shape of setup. The correct fix is to run the whole
service as a dedicated **non-root** system user (e.g. `claudeweb`) with its own copy of the Claude
Code credentials, so the daemon can act without prompts but is capped at that user's own
permissions instead of full root. That migration is not done yet — creating the system user was
blocked by the Claude Code host's own safety classifier when attempted from within a session, and
needs to be done with explicit operator sign-off (see the running conversation for the exact
commands: `useradd claudeweb`, copy `~/.claude/.credentials.json`, move this directory somewhere
`claudeweb` can read, then drop privileges in `app.py` after the TLS cert is loaded and the socket
is bound to port 443, before serving requests).

Until that migration happens, treat this deployment as **root-equivalent remote code execution
gated by a single password** — keep the password strong, don't share the URL, and be aware that
compromise of the password compromises the whole server.

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

Python stdlib only, no dependencies, flat modules (no package):

- `app.py` — entry point: starts the token-usage collector thread and the HTTP server.
- `settings.py` — config constants (paths, TLS, daemon user, timeouts).
- `storage.py` — `config.json` / `sessions.json` persistence.
- `auth.py` — password hashing/check, session cookies, login rate limiting.
- `claude_daemon.py` — runs `claude -p` as the daemon user, injecting `.env` credentials
  into just that subprocess's environment. Shared by the chat handler and night mode.
- `server.py` — the `Handler` (HTTP routing) and the TLS server class.
- `templates.py` — the login/chat/dashboard HTML pages.
- `night_control.py` — web-side glue for the `/night` chat command (start/status/stop).
- `night_mode.py` — the actual ~8h autonomous overnight run loop (see below).
- `dashboard.py` — token-usage + host-stats collection for `/dashboard`.
- `bootstrap.sh` — fresh-VPS setup script.
- `config.json` / `sessions.json` / `token_usage.json` / `night_task.txt` /
  `night_mode.log` / `night_mode.pid` — local secrets/state, gitignored.

## Night mode

Send `/night <task>` in the chat to kick off an unattended ~8h `claude -p` loop on that task
(auto-resuming the session after every call — success, error, or timeout — until the task
says it's done or the time budget runs out). `/night status` shows whether it's running plus
a log tail; `/night stop` sends it SIGTERM. Budget is `NIGHT_MODE_BUDGET_SECS` (default 8h),
per-call cap is `NIGHT_MODE_MAX_CALL_SECS` (default 1h).
