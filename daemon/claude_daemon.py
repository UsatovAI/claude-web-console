"""Runs the `claude` CLI as the unprivileged daemon user.

Shared by the web chat handler (server.py) and the overnight autonomous loop
(night_mode.py) so the subprocess-invocation and credential-injection logic
lives in exactly one place.

Auth: claudeweb authenticates via a `claude setup-token` long-lived token
(written to ~/.claude/.credentials.json by that command, run once as
claudeweb) rather than a plain browser-style OAuth login -- same credential
file, but a token meant to survive unattended/headless use instead of one
tied to a refresh cycle that can silently go stale (see
_looks_like_auth_failure below, added for exactly that failure mode).
ANTHROPIC_API_KEY (pay-per-token API billing, bypasses this file entirely)
was considered and rejected here since this deployment runs on a Claude
subscription, not API billing.
"""
import json
import os
import shutil
import subprocess

from core import settings

DAEMON_HOME = f"/home/{settings.DAEMON_USER}"
_PER_USER_CLAUDE_BIN = f"{DAEMON_HOME}/.local/bin/claude"
# Prefer a per-user install if claudeweb has one, else fall back to whatever
# `claude` resolves to on PATH (e.g. a system-wide npm -g install shared by
# all users) -- avoids requiring a redundant second CLI install just for
# this one account.
CLAUDE_BIN = _PER_USER_CLAUDE_BIN if os.path.exists(_PER_USER_CLAUDE_BIN) else (shutil.which("claude") or "claude")
# Credential-free (uses ${VAR} expansion, see mcp-config.json itself) --
# reuses the same jira MCP server/credentials already registered in root's
# own ~/.claude.json rather than a second Jira integration for claudeweb.
MCP_CONFIG_PATH = f"{DAEMON_HOME}/mcp-config.json"


def load_daemon_env(path=settings.ENV_FILE_PATH):
    """Parse a simple KEY=VALUE .env file, owned by and readable only by the
    daemon user itself (the whole app runs as that unprivileged user now --
    see bootstrap.sh -- so there's no longer a root/subprocess privilege
    split to defend across; the file just needs mode 600 against other
    unrelated users on the box)."""
    env = {}
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return env
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            env[key] = value
    return env


_AUTH_FAILURE_MARKERS = ("oauth", "authenticate", "credentials", "session expired", "not logged in")
# Kept as a safety net even after switching claudeweb to API-key auth: it still
# catches a revoked/invalid/misconfigured ANTHROPIC_API_KEY (and covers any
# deployment that hasn't set one yet and is still running on a raw OAuth
# login). Remove only once API-key auth has been live long enough to be
# confident this path is unreachable in practice.


def _looks_like_auth_failure(error_text):
    lower = (error_text or "").lower()
    return any(marker in lower for marker in _AUTH_FAILURE_MARKERS)


def _run_once(prompt, *, session_id, timeout, extra_args, cwd, as_daemon_user, model=None):
    """One subprocess invocation. Both branches run as the same unprivileged
    daemon user (the whole app does, see bootstrap.sh) -- the distinction is
    only whether --dangerously-skip-permissions is passed: with it (using
    the daemon user's own Claude Code login and MCP config, cwd'd to its
    home) the CLI can use tools unattended; without it, invoked with no
    special env/cwd, it can only reply conversationally.

    `prompt` is piped over stdin rather than passed as a CLI argument (`claude
    -p` reads it from stdin when no positional prompt is given): the kernel
    caps any single argv entry at MAX_ARG_STRLEN (128KiB on Linux, well under
    the ~2MiB total ARG_MAX), which a short chat message never approaches but
    a PR diff embedded in a review prompt (see web/github_review.py) can blow
    through easily, raising OSError('Argument list too long') from execve --
    stdin has no such limit.
    """
    # Always CLAUDE_BIN, not a bare "claude" for the non-daemon-user branch:
    # a bare command name relies on PATH resolution at exec time, and this
    # process's PATH can (and on this deployment, does) contain an entry
    # that resolves to a symlink into another user's home directory --
    # /usr/local/bin/claude -> /root/.local/bin/claude here, unreachable by
    # the unprivileged daemon user this whole app runs as. That's not a
    # FileNotFoundError PATH search would gracefully continue past; execvp
    # stops at the first EACCES, so it surfaced as an unhandled
    # PermissionError instead of the intended graceful degraded-mode reply.
    # CLAUDE_BIN is already resolved once at import time to something this
    # process can actually reach, so there's no reason for this branch to
    # re-derive a different, less reliable path.
    cmd = [CLAUDE_BIN, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if as_daemon_user:
        cmd += ["--dangerously-skip-permissions"]
    if session_id:
        cmd += ["--resume", session_id]
    if extra_args:
        cmd += extra_args
    if as_daemon_user and os.path.exists(MCP_CONFIG_PATH):
        cmd += ["--mcp-config", MCP_CONFIG_PATH]
    run_kwargs = dict(input=prompt, capture_output=True, text=True, timeout=timeout)
    if as_daemon_user:
        run_kwargs["cwd"] = cwd or DAEMON_HOME
        # No user= here: the whole app already runs as the daemon user (see
        # bootstrap.sh), so this used to be a redundant self-setuid -- which
        # subprocess.run() still attempts even when uid already matches, and
        # NoNewPrivileges=true in the systemd unit blocks that setresuid
        # call outright, breaking every daemon-user invocation with a
        # confusing PermissionError blamed on the cwd. If a future
        # deployment goes back to a privileged parent process, this needs
        # user= restored.
        run_kwargs["env"] = {**os.environ, "HOME": DAEMON_HOME, **load_daemon_env()}
    try:
        proc = subprocess.run(cmd, **run_kwargs)
    except subprocess.TimeoutExpired:
        return None, "timeout"

    # The CLI's JSON result (when it produced one) carries a much more useful
    # error message than a bare exit code -- e.g. "OAuth session expired and
    # could not be refreshed" -- so prefer it over stderr even on a nonzero
    # exit, and check `is_error` even on a zero exit (seen returning 0 with
    # is_error:true depending on how the process's privileges were dropped).
    try:
        result = json.loads(proc.stdout)
    except Exception:
        result = None

    if result is not None and result.get("is_error"):
        return None, result.get("result") or f"is_error with no message (exit {proc.returncode})"
    if proc.returncode != 0:
        if result is not None:
            return None, f"exit {proc.returncode}: {json.dumps(result)[:300]}"
        return None, f"exit {proc.returncode}: {proc.stderr[:300]}"
    if result is None:
        return None, f"unparsable output: {proc.stdout[:300]}"
    return result, None


def run_claude(prompt, *, session_id=None, session_owner=None, timeout, extra_args=None, cwd=None, restricted=False):
    """Run one `claude -p` turn, preferring the unprivileged daemon user.

    Returns (result_dict, error, owner) where owner is "claudeweb", "root",
    or "restricted" -- the caller should persist it alongside session_id,
    since a session started under one identity can't be --resume'd under
    another (separate ~/.claude/projects transcripts, and restricted
    sessions are kept in their own namespace deliberately -- see below).

    restricted=True is not the auth-failure fallback below -- it's an
    unconditional, caller-requested mode (see web/server.py's
    _is_public_restricted()) for traffic that must never get tool access
    regardless of whether the daemon user's own login is healthy: no
    --dangerously-skip-permissions, no daemon cwd/env/MCP config, and
    settings.PUBLIC_CHAT_MODEL instead of the default. Its own owner value
    ("restricted") keeps its sessions from ever being --resume'd as a
    claudeweb/root one or vice versa, so a restricted conversation can never
    inherit or hand off into privileged tool access mid-session.

    NOTE: since the whole app now runs as the daemon user (not root -- see
    bootstrap.sh), the "root" fallback below is no longer a genuinely
    separate identity/credential set, just the same OS user invoked without
    --dangerously-skip-permissions and without the daemon-home cwd/env
    override. It still degrades gracefully (conversational reply, no tool
    use) if the daemon user's Claude Code login is broken/expired, but it no
    longer provides the credential isolation the original root/non-root
    split gave it -- a broken daemon-user login now means both branches hit
    the same underlying auth. Revisit if that isolation matters again.
    """
    if restricted:
        resume_id = session_id if session_owner == "restricted" else None
        result, error = _run_once(
            prompt, session_id=resume_id, timeout=timeout, extra_args=extra_args, cwd=cwd,
            as_daemon_user=False, model=settings.PUBLIC_CHAT_MODEL)
        return result, error, "restricted"

    owner = session_owner or "claudeweb"
    resume_id = session_id if owner == "claudeweb" else None
    result, error = _run_once(
        prompt, session_id=resume_id, timeout=timeout, extra_args=extra_args, cwd=cwd, as_daemon_user=True)
    if error and _looks_like_auth_failure(error):
        resume_id = session_id if owner == "root" else None
        result, error = _run_once(
            prompt, session_id=resume_id, timeout=timeout, extra_args=extra_args, cwd=cwd, as_daemon_user=False)
        if not error:
            return result, None, "root"
        return None, f"{error} (daemon-user auth also broken, root fallback used)", "root"
    return result, error, "claudeweb"
