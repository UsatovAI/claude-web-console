"""Web-console glue for starting/checking/stopping the night_mode background run."""
import os
import signal
import subprocess
import threading

from core import settings
from . import night_mode

USAGE = (
    "Usage:\n"
    "/night <task> — start an ~8h autonomous run on that task\n"
    "/night status — check whether it's running\n"
    "/night stop — stop the current run"
)


def _pid():
    if not os.path.exists(night_mode.PID_PATH):
        return None
    try:
        pid = int(open(night_mode.PID_PATH).read().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


def _tail_lines(path, n):
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return "".join(f.readlines()[-n:]).strip()


def handle_command(rest):
    """Handle the text after `/night` from a chat message; return the reply."""
    sub = rest.lower()
    pid = _pid()

    if sub == "status":
        if pid:
            log_tail = _tail_lines(night_mode.LOG_PATH, 15)
            return f"Night mode is running (pid {pid}).\n\nRecent log:\n{log_tail}"
        return "Night mode is not running."

    if sub == "stop":
        if pid:
            os.kill(pid, signal.SIGTERM)
            return f"Sent stop signal to night mode (pid {pid})."
        return "Night mode is not running."

    if not rest:
        return USAGE

    if pid:
        return f"Night mode is already running (pid {pid}). Use /night stop first."

    with open(night_mode.TASK_PATH, "w") as f:
        f.write(rest)

    log_f = open(settings.NIGHT_CRON_LOG, "a")
    proc = subprocess.Popen(
        ["/usr/bin/python3", "-m", "daemon.night_mode"],
        cwd=settings.BASE_DIR, env=os.environ.copy(),
        stdout=log_f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    log_f.close()
    threading.Thread(target=proc.wait, daemon=True).start()

    return (
        f"Night mode started (pid {proc.pid}), ~8h budget.\n"
        f"Task: {rest[:300]!r}\n"
        f"Check progress with /night status, or /night stop to cancel."
    )
