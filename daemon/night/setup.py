"""One-time (idempotent) setup for night mode: workspace dir, and copying the
QA skills/command from root's Claude config into claudeweb's, so the `/qa`
role the executor/QA prompts refer to actually resolves for the daemon user.

Used to also write a Stop-hook script (min-duration enforcement) and a
PreToolUse audit-log hook here, wired in via `claude -p --settings`. Both were
removed: confirmed empirically that neither ever fires under headless
`claude -p` (a real 8h run's audit-log hook logged nothing across dozens of
tool calls, and the Stop hook's marker file -- written on its very first
invocation -- was never created). The min-duration floor is now enforced by
night_mode.py itself (see _run_executor_with_min_duration there), which
resumes the executor's session and re-prompts it until real elapsed time is
met, instead of relying on a hook nobody honors.

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

ROOT_CLAUDE_DIR = "/root/.claude"
QA_SKILLS = ["task-validation", "code-review-skill"]


def _chown_recursive(path, uid, gid):
    os.chown(path, uid, gid)
    for root, dirs, files in os.walk(path):
        for d in dirs:
            os.chown(os.path.join(root, d), uid, gid)
        for f in files:
            os.chown(os.path.join(root, f), uid, gid)


def ensure_workspace():
    """Create the claudeweb-owned workspace dir. Safe to call every run."""
    pw = pwd.getpwnam(settings.DAEMON_USER)
    uid, gid = pw.pw_uid, pw.pw_gid

    for d in (NIGHT_HOME, WORKSPACE_DIR):
        os.makedirs(d, exist_ok=True)
        os.chown(d, uid, gid)

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
