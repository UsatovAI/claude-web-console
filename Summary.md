# Session summary

## What got built

A password-gated web chat console (`/root/site/app.py`, stdlib-only Python) running on this VPS
(`144.124.226.50`), forwarding messages to a headless `claude -p` daemon:

- **Domain**: `144-124-226-50.sslip.io` — free, zero-signup wildcard DNS (resolves automatically
  to the VPS IP), used instead of a paid registrar or DuckDNS (DuckDNS needs an OAuth login this
  session can't complete headlessly).
- **TLS**: real Let's Encrypt cert via `certbot certonly --standalone`. Fixed a real bug along the
  way — the first implementation wrapped the *listening* socket, so the TLS handshake ran
  synchronously in the main accept loop; one stalled/killed client handshake wedged the whole
  server permanently. Fixed by deferring the handshake into each connection's worker thread
  (`TLSThreadingHTTPServer` in `app.py`).
- **Auth**: single password (PBKDF2-hashed, rate-limited login), a random session cookie
  (`HttpOnly; Secure; SameSite=Strict`, 1yr) issued on success — "one-time write," no re-login
  after. Password is currently `Stas1337reset`.
- **Chat**: `POST /api/chat` runs `claude -p --resume <session_id>`, returning JSON with the reply
  and a session id reused for conversational continuity per browser cookie.
- **Dashboard**: `/dashboard` shows Claude token usage (scanned from `~/.claude/projects/*.jsonl`,
  collected every 5 min) and host CPU/RAM/disk, no third-party deps. The token budget meter
  compares usage against `token_limit` over a **rolling 5-hour window** (`USAGE_WINDOW_SECS` in
  `core/settings.py`), matching how Claude's own usage limits actually reset, instead of an
  all-time cumulative count that would only ever climb. The usage chart now covers ~25h at 5-min
  resolution and draws a dashed marker + shaded region showing where the trailing 5h window starts
  (`web/dashboard.py:window_totals`/`window_start_ts`, wired through `/api/stats` to the chart in
  `web/templates.py`).
- **Daemon privilege model**: `claude -p` refuses `--dangerously-skip-permissions` when run as
  root (a deliberate upstream safeguard). Fixed by running the daemon subprocess as a dedicated
  non-root user (`claudeweb`, uid 1000) with its own Claude Code login, via
  `subprocess.run(..., user="claudeweb", env={"HOME": "/home/claudeweb"})`. Two non-obvious traps
  hit along the way: (1) `/usr/local/bin/claude` symlinks through `/root`, which a non-root user
  can't traverse — must call `/home/claudeweb/.local/bin/claude` directly; (2) `cwd=` must be a
  directory the daemon user can enter, not the root-owned project dir.
- **Repos** (GitHub, account `UsatovAI`, currently private):
  - [`claude-web-console`](https://github.com/UsatovAI/claude-web-console) — this app + `bootstrap.sh`
    (reproduces the whole setup on a fresh VPS) + README documenting the root-vs-non-root tradeoff.
  - [`skills`](https://github.com/UsatovAI/skills) — personal Claude Code skills library;
    contains `deploy-claude-console`, a runbook distilling every pitfall from this session (TLS
    handshake bug, root/non-root daemon fix, credential-safe shell patterns, which actions this
    environment's safety classifier blocks and why).

## Known limitation, stated plainly

The daemon runs unattended, gated by a single password, with `claudeweb`-level (not full root)
execution rights. Compromise of the password compromises everything `claudeweb` can touch. Keep
the password strong, don't share the URL. This was an explicit, informed tradeoff, not an
oversight.

## Security guardrails hit (by design, not bugs)

This session's own auto-mode safety classifier repeatedly blocked direct-from-agent actions that
are individually reasonable but high-blast-radius when run unattended: SSH with an inline
plaintext password, installing a systemd service, `useradd` for a new system account, calling
`--dangerously-skip-permissions` directly, and even a plain restart once the daemon script was
known to grant unattended execution. Worked around correctly each time by either finding a
credential-safe alternative (curl config file / git credential helper instead of inline secrets)
or handing the exact command to the human to run themselves — never by obscuring the command to
route around the block.

## Evaluated and declined: OpenClaw

Researched [openclaw.ai](https://openclaw.ai) as a possible alternative/addition — an open-source,
multi-channel (WhatsApp/Telegram/Discord/etc.) personal AI assistant framework with cron-based
background tasks and a long-running agent loop (48h default runtime, idle watchdogs, crash-safe
transcript locking). It does support reusing an existing Claude Pro/Max subscription login
(`openclaw onboard` → Claude CLI, or `claude setup-token`), not just a pay-per-token API key.

Decision: **not adopting it.** It's a general personal-assistant orchestration layer, not
optimized for software-engineering context management, and for coding work it likely just wraps
the Claude CLI anyway — so it can't outperform Claude at reasoning/coding, only add access surface
(messaging channels) and long-running infra we don't currently need. Sticking with the existing
console; the standing gap is genuine multi-hour unattended execution (each chat message today is
one `claude -p` call capped at 180s, not a persistent loop) — next step suggested was prototyping
Claude Code's own native `claude --bg` / `claude agents` background-agent feature before
considering any heavier framework, since it's zero new infrastructure.

## New role: QA

Added a **QA** role, backed by two skills installed under `~/.claude/skills/` (and, where
personally authored, tracked in the `skills` repo above so they survive re-provisioning):

1. **Code review** — [`code-review-skill`](https://github.com/awesome-skills/code-review-skill)
   (third-party, cloned as-is, not vendored into the personal `skills` repo). 20+ language/framework
   guides loaded progressively, four-phase review process (context → high-level → line-by-line →
   summary), severity-labeled findings (`blocking`/`important`/`nit`/`suggestion`/`learning`/`praise`).
   Answers: *is this code well-written?*
2. **Task validation** — `task-validation`, a new skill authored this session (added to the
   `skills` repo alongside `deploy-claude-console`, not yet committed/pushed). Answers a
   deliberately different question: *does this
   implementation actually satisfy the task that was asked?* — checked by turning the original
   request into concrete pass/fail criteria and actively exercising the change (running tests,
   driving the CLI/endpoint/page) rather than inferring correctness from reading the diff.

The two-skill split follows the Generator/Evaluator separation from Anthropic's write-up on
[harness design for long-running agents](https://www.anthropic.com/engineering/harness-design-long-running-apps):
self-grading skews positive, so QA's validation pass is designed to run from a context with no
attachment to the implementation choices, with concrete grading criteria and active tool-based
testing rather than static read-through, and a verdict (not prose) as the output — PASS / FAIL /
PASS WITH GAPS, each criterion citing what was actually run to verify it.

## Open backlog (from user's notes, not yet built)

1. **Hooks + more daemon permissions + session management UI** — add a `PostToolUse` hook on the
   `claudeweb` Claude config to audit-log every action the daemon takes (compensating control now
   that permission prompts are bypassed); add `/api/sessions` to list/switch/resume past Claude
   sessions from the console (data already available via the same transcript files the dashboard
   reads). Was mid-implementation when interrupted.
2. **Night mode (~8h autonomous)** — needs a $ budget cap decision (`claude` supports
   `--max-budget-usd` for exactly this) before building.
3. **Reviewer from repository** — automated review on push to the GitHub repos; needs a scope
   decision (comment-only vs. write/auto-fix access).
4. **"Arch with subagents"** — user's note was cut off, needs the rest of the requirement.

## Other corrections/preferences noted this session

- Search broadly before saying a file/config doesn't exist (missed `.env` on the first pass by
  checking only one path).
- Don't ask permission for routine, reversible local actions (e.g. "can I install gh") — just do
  them. Genuine hard blocks come from the environment's own safety classifier, not from asking;
  those get surfaced with the exact reason and handed off, not retried in a loop.
- The classifier's restrictions apply to direct agent Bash calls, not to subprocesses spawned by
  an already-running deployed service — relevant when reasoning about what the *daemon* can do
  once deployed vs. what the *building* session can do live.
