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

## Files

- `app.py` — the server (Python stdlib only, no dependencies).
- `bootstrap.sh` — fresh-VPS setup script.
- `config.json` / `sessions.json` — local secrets/state, gitignored.
