"""Runs the `claude` CLI as the unprivileged daemon user.

Shared by the web chat handler (server.py) and the overnight autonomous loop
(night_mode.py) so the subprocess-invocation and credential-injection logic
lives in exactly one place.
"""
import json
import os
import subprocess

import settings

DAEMON_HOME = f"/home/{settings.DAEMON_USER}"
CLAUDE_BIN = f"{DAEMON_HOME}/.local/bin/claude"


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


def run_claude(prompt, *, session_id=None, timeout, extra_args=None):
    """Run one `claude -p` turn as the daemon user.

    Returns (result_dict, None) on success, or (None, error_message) on
    timeout / nonzero exit / unparsable output.
    """
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--dangerously-skip-permissions"]
    if session_id:
        cmd += ["--resume", session_id]
    if extra_args:
        cmd += extra_args
    env = {**os.environ, "HOME": DAEMON_HOME, **load_daemon_env()}
    try:
        proc = subprocess.run(
            cmd, cwd=DAEMON_HOME, user=settings.DAEMON_USER, env=env,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    if proc.returncode != 0:
        return None, f"exit {proc.returncode}: {proc.stderr[:300]}"
    try:
        result = json.loads(proc.stdout)
    except Exception:
        return None, f"unparsable output: {proc.stdout[:300]}"
    return result, None
