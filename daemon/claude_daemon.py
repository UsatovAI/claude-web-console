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
import subprocess

from core import settings

DAEMON_HOME = f"/home/{settings.DAEMON_USER}"
CLAUDE_BIN = f"{DAEMON_HOME}/.local/bin/claude"
# Credential-free (uses ${VAR} expansion, see mcp-config.json itself) --
# reuses the same jira MCP server/credentials already registered in root's
# own ~/.claude.json rather than a second Jira integration for claudeweb.
MCP_CONFIG_PATH = f"{DAEMON_HOME}/mcp-config.json"


def load_daemon_env(path=settings.ENV_FILE_PATH):
    """Parse a simple KEY=VALUE .env file. Read as root so the values can be
    handed to the daemon subprocess without that file being readable by it."""
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


_AUTH_FAILURE_MARKERS = ("oauth", "authenticate", "credentials", "session expired")
# Kept as a safety net even after switching claudeweb to API-key auth: it still
# catches a revoked/invalid/misconfigured ANTHROPIC_API_KEY (and covers any
# deployment that hasn't set one yet and is still running on a raw OAuth
# login). Remove only once API-key auth has been live long enough to be
# confident this path is unreachable in practice.


def _looks_like_auth_failure(error_text):
    lower = (error_text or "").lower()
    return any(marker in lower for marker in _AUTH_FAILURE_MARKERS)


def _run_once(prompt, *, session_id, timeout, extra_args, cwd, as_daemon_user):
    """One subprocess invocation, either dropped to the unprivileged daemon
    user (with --dangerously-skip-permissions, so it can act unattended) or
    run as root (no skip-permissions -- the CLI refuses that combination --
    so it can only reply conversationally, not use tools).

    `prompt` is piped over stdin rather than passed as a CLI argument (`claude
    -p` reads it from stdin when no positional prompt is given): the kernel
    caps any single argv entry at MAX_ARG_STRLEN (128KiB on Linux, well under
    the ~2MiB total ARG_MAX), which a short chat message never approaches but
    a PR diff embedded in a review prompt (see web/github_review.py) can blow
    through easily, raising OSError('Argument list too long') from execve --
    stdin has no such limit.
    """
    cmd = [CLAUDE_BIN if as_daemon_user else "claude", "-p", "--output-format", "json"]
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
        run_kwargs["user"] = settings.DAEMON_USER
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


def run_claude(prompt, *, session_id=None, session_owner=None, timeout, extra_args=None, cwd=None):
    """Run one `claude -p` turn, preferring the unprivileged daemon user.

    Returns (result_dict, error, owner) where owner is "claudeweb" or "root"
    -- the caller should persist it alongside session_id, since a session
    started under one identity can't be --resume'd under the other (separate
    ~/.claude/projects transcripts).

    Falls back to running as root (unattended tool use disabled, since the
    CLI refuses --dangerously-skip-permissions as root) when the daemon
    user's own auth is broken, so the chat keeps working in a degraded mode
    instead of hard-failing while that gets fixed out of band.
    """
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
