"""One-time (idempotent) setup for night mode: workspace dir, the Stop-hook
script that enforces a minimum session duration, an audit-log PreToolUse hook
(compensating control for running under --dangerously-skip-permissions), and
copying the QA skills/command from root's Claude config into claudeweb's, so
the `/qa` role the executor/QA prompts refer to actually resolves for the
daemon user.

Runs as root (night_mode.py is started by the root-owned web server process),
so it can freely create files under claudeweb's home and chown them --
claudeweb itself can't reach anything under /root, so this is one-directional.
"""
import os
import pwd
import shutil

from core import settings

DAEMON_HOME = f"/home/{settings.DAEMON_USER}"
NIGHT_HOME = f"{DAEMON_HOME}/night_mode"
WORKSPACE_DIR = f"{NIGHT_HOME}/workspace"
HOOKS_DIR = f"{NIGHT_HOME}/hooks"
MARKERS_DIR = f"{HOOKS_DIR}/.markers"

ROOT_CLAUDE_DIR = "/root/.claude"
QA_SKILLS = ["task-validation", "code-review-skill"]

MIN_DURATION_HOOK = """#!/usr/bin/env python3
\"\"\"Stop hook: blocks a night-mode session from ending until it has run for
at least NIGHT_MIN_SESSION_SECS. Keyed by session_id so a single claude -p
invocation that gets blocked and continues re-checks against the same start
time on every subsequent Stop attempt.\"\"\"
import json
import os
import sys
import time

MIN_SECONDS = int(os.environ.get("NIGHT_MIN_SESSION_SECS", "600"))
MARKER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".markers")


def main():
    data = json.load(sys.stdin)
    session_id = data.get("session_id", "unknown")
    os.makedirs(MARKER_DIR, exist_ok=True)
    marker = os.path.join(MARKER_DIR, f"{session_id}.start")

    now = time.time()
    if not os.path.exists(marker):
        with open(marker, "w") as f:
            f.write(str(now))
        start = now
    else:
        start = float(open(marker).read().strip())

    elapsed = now - start
    if elapsed < MIN_SECONDS:
        remaining = int(MIN_SECONDS - elapsed)
        print(json.dumps({
            "decision": "block",
            "reason": (
                f"Minimum {MIN_SECONDS // 60}-minute session not yet reached "
                f"({remaining}s remaining). Keep making concrete progress: deepen "
                f"verification, handle more edge cases, improve tests or docs for "
                f"the feature you're on. Do not just wait."
            ),
        }))
    else:
        try:
            os.remove(marker)
        except OSError:
            pass
        sys.exit(0)


if __name__ == "__main__":
    main()
"""

AUDIT_LOG_HOOK = """#!/usr/bin/env python3
\"\"\"PreToolUse hook: append-only audit log of every tool call a night-mode
session makes. Never blocks -- compensating control for running under
--dangerously-skip-permissions with nobody watching in real time.\"\"\"
import json
import os
import sys
import time

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.log")


def main():
    data = json.load(sys.stdin)
    tool = data.get("tool_name", "?")
    tool_input = json.dumps(data.get("tool_input", {}))[:300]
    session_id = data.get("session_id", "?")[:8]
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{session_id}] {tool} {tool_input}\\n"
    with open(LOG_PATH, "a") as f:
        f.write(line)
    sys.exit(0)


if __name__ == "__main__":
    main()
"""


def _chown_recursive(path, uid, gid):
    os.chown(path, uid, gid)
    for root, dirs, files in os.walk(path):
        for d in dirs:
            os.chown(os.path.join(root, d), uid, gid)
        for f in files:
            os.chown(os.path.join(root, f), uid, gid)


def _write_hook(path, content, uid, gid):
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o755)
    os.chown(path, uid, gid)


def ensure_workspace():
    """Create the claudeweb-owned workspace/hooks dirs and hook scripts.
    Safe to call every run -- overwrites hook scripts so they stay in sync
    with this file, but never touches workspace contents."""
    pw = pwd.getpwnam(settings.DAEMON_USER)
    uid, gid = pw.pw_uid, pw.pw_gid

    for d in (NIGHT_HOME, WORKSPACE_DIR, HOOKS_DIR, MARKERS_DIR):
        os.makedirs(d, exist_ok=True)
        os.chown(d, uid, gid)

    _write_hook(os.path.join(HOOKS_DIR, "min_duration.py"), MIN_DURATION_HOOK, uid, gid)
    _write_hook(os.path.join(HOOKS_DIR, "audit_log.py"), AUDIT_LOG_HOOK, uid, gid)

    audit_log = os.path.join(HOOKS_DIR, "audit.log")
    if not os.path.exists(audit_log):
        open(audit_log, "a").close()
        os.chown(audit_log, uid, gid)

    _ensure_qa_skills(uid, gid)
    return WORKSPACE_DIR


def _ensure_qa_skills(uid, gid):
    """Mirror the QA skills + /qa command from root's Claude config into
    claudeweb's, so night-mode QA sessions (which run as claudeweb) can
    actually invoke them. Only copies if missing -- doesn't clobber a
    daemon-side customization if one shows up later."""
    dest_skills = f"{DAEMON_HOME}/.claude/skills"
    dest_commands = f"{DAEMON_HOME}/.claude/commands"
    os.makedirs(dest_skills, exist_ok=True)
    os.makedirs(dest_commands, exist_ok=True)

    for skill in QA_SKILLS:
        src = os.path.join(ROOT_CLAUDE_DIR, "skills", skill)
        dst = os.path.join(dest_skills, skill)
        if os.path.isdir(src) and not os.path.isdir(dst):
            shutil.copytree(src, dst)

    src_cmd = os.path.join(ROOT_CLAUDE_DIR, "commands", "qa.md")
    dst_cmd = os.path.join(dest_commands, "qa.md")
    if os.path.isfile(src_cmd) and not os.path.isfile(dst_cmd):
        shutil.copy2(src_cmd, dst_cmd)

    _chown_recursive(dest_skills, uid, gid)
    _chown_recursive(dest_commands, uid, gid)


def hook_settings_json(min_session_secs):
    """Return the --settings JSON (as a dict) wiring both hooks in."""
    min_hook = os.path.join(HOOKS_DIR, "min_duration.py")
    audit_hook = os.path.join(HOOKS_DIR, "audit_log.py")
    return {
        "hooks": {
            "Stop": [
                {
                    "type": "command",
                    "command": f"NIGHT_MIN_SESSION_SECS={min_session_secs} python3 {min_hook}",
                    "timeout": 15,
                }
            ],
            "PreToolUse": [
                {"matcher": "*", "hooks": [{"type": "command", "command": f"python3 {audit_hook}", "timeout": 15}]}
            ],
        }
    }
