#!/usr/bin/env python3
"""Autonomous ~8h overnight Claude run.

Reads a task from night_task.txt and keeps a `claude -p` session going,
immediately relaunching it (via --resume) whenever a call ends -- success,
error, or timeout -- until the task signals completion or the time budget
runs out. Meant to be triggered by cron once nightly.
"""
import os
import time

import claude_daemon

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_PATH = os.path.join(BASE_DIR, "night_task.txt")
LOG_PATH = os.path.join(BASE_DIR, "night_mode.log")
PID_PATH = os.path.join(BASE_DIR, "night_mode.pid")

TOTAL_BUDGET_SECS = int(os.environ.get("NIGHT_MODE_BUDGET_SECS", 8 * 3600))
MAX_CALL_SECS = int(os.environ.get("NIGHT_MODE_MAX_CALL_SECS", 3600))
MAX_CONSECUTIVE_FAILURES = 15
COMPLETE_MARKER = "NIGHT_MODE_COMPLETE"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def already_running():
    if not os.path.exists(PID_PATH):
        return False
    try:
        pid = int(open(PID_PATH).read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def main():
    if already_running():
        log("night mode already running, skipping this trigger")
        return
    with open(PID_PATH, "w") as f:
        f.write(str(os.getpid()))
    try:
        if not os.path.exists(TASK_PATH) or not open(TASK_PATH).read().strip():
            log("no task configured in night_task.txt, skipping tonight's run")
            return

        task = open(TASK_PATH).read().strip()
        prompt = (
            task
            + "\n\nThis is an unattended overnight session with no human present to "
              "answer questions -- use your best judgment instead of stopping to ask. "
              "When the task is fully complete, end your final reply with the exact "
              f"line: {COMPLETE_MARKER}"
        )

        start = time.time()
        session_id = None
        consecutive_failures = 0
        iteration = 0

        log(f"night mode starting, budget {TOTAL_BUDGET_SECS}s, task: {task[:200]!r}")

        while True:
            elapsed = time.time() - start
            remaining = TOTAL_BUDGET_SECS - elapsed
            if remaining <= 60:
                log("time budget exhausted, stopping")
                break

            iteration += 1
            call_timeout = max(30, min(MAX_CALL_SECS, remaining))
            result, error = claude_daemon.run_claude(prompt, session_id=session_id, timeout=call_timeout)

            if error:
                consecutive_failures += 1
                log(f"iter {iteration}: error ({error}) [{consecutive_failures} in a row]")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log("too many consecutive failures, aborting for tonight")
                    break
                time.sleep(min(120, 5 * 2 ** (consecutive_failures - 1)))
                continue

            consecutive_failures = 0
            session_id = result.get("session_id") or session_id
            reply = result.get("result", "")
            log(f"iter {iteration}: ok, session={session_id}, reply={reply[:200]!r}")

            if COMPLETE_MARKER in reply:
                log(f"task signaled complete after {iteration} iterations, stopping")
                break

            prompt = "Continue."

        log(f"night mode ending after {iteration} iterations, {time.time() - start:.0f}s elapsed")
    finally:
        if os.path.exists(PID_PATH):
            os.remove(PID_PATH)


if __name__ == "__main__":
    main()
