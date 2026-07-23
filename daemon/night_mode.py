#!/usr/bin/env python3
"""Autonomous ~8h overnight Claude run: planner -> executor/QA loop.

Reads a task from night_task.txt and runs three roles in sequence, each a
fresh `claude -p` session with no memory of the others (per-session context
isolation, same pattern as
https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding):

  1. Planner  -- breaks the task into var/night_mode/workspace/feature_list.json
  2. Executor -- picks one "pending" feature, implements + verifies it, marks
                 it "implemented". Held to a minimum session length by a Stop
                 hook so it can't bail after a token effort (see night/setup.py).
  3. QA       -- independent session (no memory of the executor run) that
                 runs this project's own `/qa` role (task-validation +
                 code-review-skill) against an "implemented" feature and
                 either promotes it to "passing" or bounces it back to
                 "pending" with actionable notes for the next executor pass.

The orchestrator (this process, running as root) never edits feature_list.json
itself -- it only reads it between calls to decide which role to run next.
All the actual editing happens from inside each `claude -p` session via its
own Read/Edit tools.

Started by night_control.handle_command() via the /night chat command; run
standalone with `python3 -m daemon.night_mode` from the repo root so the
package-relative imports resolve.
"""
import json
import os
import time

from core import settings
from . import claude_daemon
from .night import feature_list as fl
from .night import prompts
from .night import setup as night_setup

TASK_PATH = os.path.join(settings.STATE_DIR, "night_task.txt")
LOG_PATH = os.path.join(settings.LOG_DIR, "night_mode.log")
PID_PATH = os.path.join(settings.STATE_DIR, "night_mode.pid")

TOTAL_BUDGET_SECS = settings.NIGHT_MODE_BUDGET_SECS
MAX_CALL_SECS = settings.NIGHT_MODE_MAX_CALL_SECS
MIN_SESSION_SECS = settings.NIGHT_MIN_SESSION_SECS
MAX_CONSECUTIVE_FAILURES = 15
MAX_CONSECUTIVE_STALLS = 5  # a role ran but feature_list.json state didn't move
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


def _run_role(prompt, workspace, call_timeout, extra_args=None):
    """Run one fresh, standalone claude -p session (no --resume: each role
    gets a clean context window) in the night-mode workspace."""
    result, error, _owner = claude_daemon.run_claude(
        prompt, session_id=None, timeout=call_timeout, cwd=workspace, extra_args=extra_args,
    )
    return result, error


def _run_planner(task, workspace, call_timeout):
    result, error = _run_role(prompts.planner_prompt(task), workspace, call_timeout)
    if error:
        return error
    fl_path = os.path.join(workspace, prompts.FEATURE_LIST_NAME)
    if os.path.exists(fl_path):
        try:
            data = fl.load(fl_path)
            if data.get("features"):
                return None
        except Exception:
            pass
    # One corrective retry in the same session, then give up.
    session_id = result.get("session_id") if result else None
    if session_id:
        retry_prompt = (
            f"You did not leave a valid {prompts.FEATURE_LIST_NAME} in the current directory "
            f"with a non-empty \"features\" array matching the schema described earlier. "
            f"Write it now before doing anything else."
        )
        result, error, _owner = claude_daemon.run_claude(
            retry_prompt, session_id=session_id, timeout=call_timeout, cwd=workspace,
        )
        if error:
            return error
        if os.path.exists(fl_path):
            try:
                data = fl.load(fl_path)
                if data.get("features"):
                    return None
            except Exception:
                pass
    return f"planner did not produce a valid {prompts.FEATURE_LIST_NAME}"


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
        workspace = night_setup.ensure_workspace()
        hook_settings = night_setup.hook_settings_json(MIN_SESSION_SECS)

        start = time.time()
        consecutive_failures = 0
        consecutive_stalls = 0
        iteration = 0

        log(f"night mode starting, budget {TOTAL_BUDGET_SECS}s, min session {MIN_SESSION_SECS}s, "
            f"workspace {workspace}, task: {task[:200]!r}")

        fl_path = os.path.join(workspace, prompts.FEATURE_LIST_NAME)
        if not os.path.exists(fl_path):
            log("planning: no feature_list.json yet, running planner")
            call_timeout = max(60, min(MAX_CALL_SECS, TOTAL_BUDGET_SECS - (time.time() - start)))
            error = _run_planner(task, workspace, call_timeout)
            if error:
                log(f"planner failed: {error}; aborting for tonight")
                return
            data = fl.load(fl_path)
            log(f"planning: done, {len(data.get('features', []))} features -- {fl.summary(data)}")

        while True:
            elapsed = time.time() - start
            remaining = TOTAL_BUDGET_SECS - elapsed
            if remaining <= 60:
                log("time budget exhausted, stopping")
                break

            data = fl.load(fl_path)
            if fl.all_passing(data):
                log(f"all features passing ({fl.summary(data)}); {COMPLETE_MARKER}")
                break

            target = fl.next_implemented(data)
            role = "qa"
            if target is None:
                target = fl.next_pending(data)
                role = "executor"
            if target is None:
                log(f"no pending or implemented features left but not all passing ({fl.summary(data)}); "
                    f"stopping to avoid spinning")
                break

            iteration += 1
            call_timeout = max(60, min(MAX_CALL_SECS, remaining))
            snapshot = (target["id"], target.get("status"), target.get("notes"))

            if role == "executor":
                prompt = prompts.executor_prompt(MIN_SESSION_SECS // 60)
                extra_args = ["--settings", json.dumps(hook_settings)]
            else:
                prompt = prompts.qa_prompt(target)
                extra_args = None  # QA doesn't need the min-duration floor

            result, error = _run_role(prompt, workspace, call_timeout, extra_args=extra_args)

            if error:
                consecutive_failures += 1
                log(f"iter {iteration} ({role} #{target['id']}): error ({error}) "
                    f"[{consecutive_failures} in a row]")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log("too many consecutive failures, aborting for tonight")
                    break
                time.sleep(min(120, 5 * 2 ** (consecutive_failures - 1)))
                continue
            consecutive_failures = 0

            data = fl.load(fl_path)
            updated = next((f for f in data.get("features", []) if f["id"] == target["id"]), None)
            new_snapshot = (updated["id"], updated.get("status"), updated.get("notes")) if updated else None
            if new_snapshot == snapshot:
                consecutive_stalls += 1
                log(f"iter {iteration} ({role} #{target['id']}): feature_list.json unchanged "
                    f"[{consecutive_stalls} stall(s) in a row]")
                if consecutive_stalls >= MAX_CONSECUTIVE_STALLS:
                    log("too many stalled iterations, aborting for tonight")
                    break
            else:
                consecutive_stalls = 0
                log(f"iter {iteration} ({role} #{target['id']}): -> {updated.get('status')}. "
                    f"{fl.summary(data)}")

        log(f"night mode ending after {iteration} iterations, {time.time() - start:.0f}s elapsed")
    finally:
        if os.path.exists(PID_PATH):
            os.remove(PID_PATH)


if __name__ == "__main__":
    main()
