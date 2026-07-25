"""Standalone planner/executor agents for the subagents pipeline (SCRUM-10).

SCRUM-28's scope is narrower than the full night-mode loop: a planner agent
creation method and an executor agent creation method, dry-runnable on their
own -- no 8h budget, no PID lock, no min-session-duration Stop hook. Reuses
the same prompt templates and feature_list.json conventions as
daemon/night_mode.py (daemon/night/prompts.py, daemon/night/feature_list.py)
since those are already generic, not night-specific. QA (SCRUM-29) and
parallel worktrees (SCRUM-30) are separate subtasks, not built here.
"""
import os

from daemon import claude_daemon
from daemon.night import feature_list as fl
from daemon.night import prompts

DEFAULT_CALL_TIMEOUT = 900  # 15 minutes -- generous single-role ceiling for a dry run
# A planner that dispatches its own research sub-agent (via a nested `claude
# -p` Bash call, see daemon/night/prompts.py's _research_dispatch_step) can
# run far longer than plain planning -- give it real headroom.
RESEARCH_CALL_TIMEOUT = 2700  # 45 minutes


def create_planner_agent(task, workspace, timeout=None, force_deep_research=False):
    """Returns a callable that, when invoked, runs one planner round in
    `workspace`: (optionally dispatches a research sub-agent, then) turns
    `task` into feature_list.json + progress notes, committed to git.
    Returns (ok, error)."""
    if timeout is None:
        timeout = RESEARCH_CALL_TIMEOUT if force_deep_research else DEFAULT_CALL_TIMEOUT

    def run():
        result, error, owner = claude_daemon.run_claude(
            prompts.planner_prompt(task, force_deep_research=force_deep_research),
            session_id=None, timeout=timeout, cwd=workspace)
        if error:
            return False, error
        fl_path = os.path.join(workspace, prompts.FEATURE_LIST_NAME)
        if not os.path.exists(fl_path):
            return False, f"planner did not write {prompts.FEATURE_LIST_NAME}"
        try:
            data = fl.load(fl_path)
        except Exception as e:
            return False, f"planner wrote invalid {prompts.FEATURE_LIST_NAME}: {e}"
        if not data.get("features"):
            return False, f"planner's {prompts.FEATURE_LIST_NAME} has no features"
        return True, None
    return run


_MAX_RESEARCH_RETRIES = 5

_KEEP_RESEARCHING_PROMPT = (
    "You have not written {findings_name} yet. Write your complete findings there now, with "
    "sources, before finishing."
)


def create_researcher_agent(research_task, workspace, timeout=RESEARCH_CALL_TIMEOUT):
    """Returns a callable that, when invoked, runs a standalone research
    round in `workspace`. Enforced by re-prompting, not `/goal` -- verified
    directly that Claude Code slash commands and Stop hooks are
    interactive-session features silently ignored when piped into a headless
    `claude -p` call (an earlier draft of this function relied on `/goal` and
    would have silently done nothing). Resumes the same session up to
    _MAX_RESEARCH_RETRIES times until research-findings.md actually exists --
    same pattern as web/chat_jobs.py's _run_with_min_duration. Returns
    (ok, error)."""
    findings_path = os.path.join(workspace, prompts.RESEARCH_FINDINGS_NAME)

    def run():
        result, error, owner = claude_daemon.run_claude(
            prompts.researcher_prompt(research_task), session_id=None, timeout=timeout, cwd=workspace)
        retries = 0
        while not error and retries < _MAX_RESEARCH_RETRIES and not os.path.exists(findings_path):
            resume_id = result.get("session_id") if result else None
            prompt = _KEEP_RESEARCHING_PROMPT.format(findings_name=prompts.RESEARCH_FINDINGS_NAME)
            result, error, owner = claude_daemon.run_claude(
                prompt, session_id=resume_id, timeout=timeout, cwd=workspace)
            retries += 1
        if error:
            return False, error
        if not os.path.exists(findings_path):
            return False, f"researcher did not write {prompts.RESEARCH_FINDINGS_NAME} after {retries} retries"
        return True, None
    return run


def create_executor_agent(workspace, timeout=DEFAULT_CALL_TIMEOUT):
    """Returns a callable that, when invoked, runs one executor round in
    `workspace`: implements the first pending feature in feature_list.json,
    verifies it, marks it implemented, commits. Returns (ok, error)."""
    def run():
        result, error, owner = claude_daemon.run_claude(
            prompts.executor_prompt(), session_id=None, timeout=timeout, cwd=workspace)
        if error:
            return False, error
        return True, None
    return run
