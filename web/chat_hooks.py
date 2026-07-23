"""Stop-hook wiring that makes the chat console's timeout-tier selector a
*minimum* working duration, not just a maximum wait. Selecting "5m" should
mean Claude actually works for close to 5 minutes on the prompt -- doubling
back, checking edge cases, verifying claims -- not just "allowed up to 5
minutes, free to reply in 5 seconds if it wants."

Same Stop-hook-blocks-until-elapsed mechanism as daemon/night/setup.py's
min-duration hook for the night-mode executor, kept as an independent copy
here (not shared) so this endpoint's behavior can't be perturbed by a future
change made for night mode's very different role, and vice versa.

Written to /tmp rather than claudeweb's or root's home directory because a
single chat request can end up running as either identity depending on
which one currently has working auth (see claude_daemon.run_claude's
root-fallback) -- /tmp is the one location both can reliably read, execute,
and write marker files in regardless of which one actually runs the call.
"""
import os

HOOKS_DIR = "/tmp/claude_console_hooks"
MARKERS_DIR = os.path.join(HOOKS_DIR, ".markers")
HOOK_SCRIPT_PATH = os.path.join(HOOKS_DIR, "min_duration.py")

MIN_DURATION_HOOK = '''#!/usr/bin/env python3
"""Stop hook: blocks a chat reply from finishing until CHAT_MIN_SESSION_SECS
have elapsed since this session's first Stop attempt. Keyed by session_id so
repeated blocks against the same session keep comparing to the same start."""
import json
import os
import sys
import time

MIN_SECONDS = int(os.environ.get("CHAT_MIN_SESSION_SECS", "0"))
MARKER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".markers")


def main():
    data = json.load(sys.stdin)
    session_id = data.get("session_id", "unknown")
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
                f"This conversation is set to a minimum working time of "
                f"{MIN_SECONDS // 60}m{MIN_SECONDS % 60:02d}s and {remaining}s of that "
                f"remain. Keep working on the user's request: double-check your answer, "
                f"consider edge cases or alternative interpretations, improve the "
                f"explanation, or verify claims -- don't just wait idly."
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
'''


def ensure_hook_script():
    os.makedirs(MARKERS_DIR, exist_ok=True)
    os.chmod(HOOKS_DIR, 0o755)
    os.chmod(MARKERS_DIR, 0o777)  # written by either claudeweb or root depending on which identity runs the call
    with open(HOOK_SCRIPT_PATH, "w") as f:
        f.write(MIN_DURATION_HOOK)
    os.chmod(HOOK_SCRIPT_PATH, 0o755)
    return HOOK_SCRIPT_PATH


def settings_json(floor_secs):
    script = ensure_hook_script()
    return {
        "hooks": {
            "Stop": [
                {
                    "type": "command",
                    "command": f"CHAT_MIN_SESSION_SECS={floor_secs} python3 {script}",
                    "timeout": 15,
                }
            ]
        }
    }
