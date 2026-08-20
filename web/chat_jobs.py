"""In-memory job store so /api/chat can return immediately instead of
holding one HTTP connection open for the full duration of a `claude -p`
call.

This exists because any proxy in front of this server enforces its own
connection timeout well under this app's own TIMEOUT_TIERS -- e.g.
Cloudflare's ~100s default edge-to-origin timeout on Free/Pro/Business plans,
which is silently far shorter than every one of the 5m/15m/30m tiers users
can pick in the UI. A single request/response cycle can't reliably survive
a multi-minute wait no matter what this app itself allows, regardless of the
exact proxy timeout value in front of it at any given time. Polling a
short-lived status endpoint instead means no single HTTP round trip needs to
stay open longer than the proxy will tolerate -- the actual `claude -p` call
still runs for however long its timeout tier allows, just off the request
thread.
"""
import threading
import time
import traceback
import uuid

from core import storage
from daemon import claude_daemon
from . import github_review

_lock = threading.Lock()
_jobs = {}  # job_id -> {"status", "token", "created", ["reply"|"error"]}

_PRUNE_AFTER_SECS = 900

# SCRUM-11/SCRUM-23: a min-duration *Stop hook* (the original approach here)
# does not work -- verified empirically by driving a real claudeweb session
# with actual tool use under --dangerously-skip-permissions and confirming
# its hook script was never invoked. `claude -p` (headless print mode) does
# not go through the interactive Stop-hook checkpoint at all, tool use or
# not. So the floor is enforced here instead: after the first reply, keep
# resuming the same session with a "keep working" prompt until timeout_secs
# have actually elapsed. Capped at _MAX_MIN_DURATION_TURNS follow-ups so a
# fast-replying loop can't run away.
_MAX_MIN_DURATION_TURNS = 20

_KEEP_WORKING_PROMPT = (
    "This conversation is set to a minimum working time of {label} and {remaining}s of "
    "that floor remain. Keep working on the user's request: double-check your answer, "
    "consider edge cases or alternative interpretations, improve the explanation, or "
    "verify claims -- don't just wait idly. If you believe the reply is actually "
    "complete, validate that against the user's original question before stopping (does "
    "it really satisfy what they asked?) rather than just asserting it's done. If you "
    "decide you need a tool you don't currently have (gh, an MCP server, etc.), install "
    "or configure it now instead of stopping without it."
)


def _duration_label(secs):
    if secs % 3600 == 0:
        return f"{secs // 3600}h"
    if secs % 60 == 0:
        return f"{secs // 60}m"
    return f"{secs}s"


def _run_with_min_duration(message, session_id, session_owner, floor_secs, call_timeout):
    t0 = time.time()
    result, error, owner = claude_daemon.run_claude(
        message, session_id=session_id, session_owner=session_owner, timeout=call_timeout)
    turns = 0
    while not error and turns < _MAX_MIN_DURATION_TURNS and time.time() - t0 < floor_secs:
        remaining = int(floor_secs - (time.time() - t0))
        prompt = _KEEP_WORKING_PROMPT.format(label=_duration_label(floor_secs), remaining=remaining)
        resume_id = result.get("session_id") or session_id
        result, error, owner = claude_daemon.run_claude(
            prompt, session_id=resume_id, session_owner=owner, timeout=call_timeout)
        turns += 1
    return result, error, owner


def _run_restricted(message, session_id, session_owner, call_timeout):
    """Restricted (public, no-tool-access) chat turn -- never goes through
    _run_with_min_duration: _KEEP_WORKING_PROMPT tells Claude to "install or
    configure" missing tools, which is nonsensical (and actively misleading)
    advice for a session that has no tool access at all by design. A
    restricted reply is just whatever the single call returns."""
    return claude_daemon.run_claude(
        message, session_id=session_id, session_owner=session_owner, timeout=call_timeout, restricted=True)


def _prune_locked():
    cutoff = time.time() - _PRUNE_AFTER_SECS
    stale = [jid for jid, v in _jobs.items() if v["status"] != "running" and v["created"] < cutoff]
    for jid in stale:
        del _jobs[jid]


def start_job(token, message, session_id, session_owner, timeout_secs, apply_min_hook=True, restricted=False):
    job_id = uuid.uuid4().hex
    with _lock:
        _prune_locked()
        _jobs[job_id] = {"status": "running", "token": token, "created": time.time()}

    def worker():
        # apply_min_hook=False (the "-" tier) skips the floor entirely: no
        # re-prompting, no extra instructions injected, just the plain
        # capped call. restricted=True always skips it too, regardless of
        # apply_min_hook -- see _run_restricted's docstring.
        #
        # Wrapped in try/except: this runs off the request thread (see
        # module docstring), so an unhandled exception here doesn't fail a
        # request -- it silently kills this thread while _jobs[job_id] stays
        # "running" forever, and whoever's polling /api/chat/status just
        # hangs until their own client-side timeout gives up. Seen for real:
        # a broken claude_daemon fallback path raised PermissionError here
        # and every affected chat message hung rather than erroring. Always
        # resolve the job to a terminal status, even on a bug we didn't
        # anticipate.
        try:
            if restricted:
                result, error, owner_used = _run_restricted(message, session_id, session_owner, timeout_secs)
            elif apply_min_hook:
                result, error, owner_used = _run_with_min_duration(
                    message, session_id, session_owner, timeout_secs, timeout_secs)
            else:
                result, error, owner_used = claude_daemon.run_claude(
                    message, session_id=session_id, session_owner=session_owner, timeout=timeout_secs)
        except Exception:
            traceback.print_exc()
            with _lock:
                _jobs[job_id] = {
                    "status": "error", "token": token, "created": time.time(),
                    "error": "internal error while running claude (see server logs)",
                }
            return

        if error:
            with _lock:
                _jobs[job_id] = {
                    "status": "error", "token": token, "created": time.time(),
                    "error": "claude timed out" if error == "timeout" else f"claude error: {error}",
                }
            return

        new_session_id = result.get("session_id")
        if new_session_id:
            sessions = storage.load_sessions()
            entry = sessions.setdefault(token, {})
            entry["claude_session_id"] = new_session_id
            entry["claude_session_owner"] = owner_used
            storage.save_sessions(sessions)

        reply = result.get("result", "")
        if owner_used == "root":
            reply = ("[degraded mode: claudeweb auth is broken, replying via root session "
                      "with no tool access until it's fixed]\n\n") + reply

        with _lock:
            _jobs[job_id] = {"status": "done", "token": token, "created": time.time(), "reply": reply}

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def start_github_review_job(token, owner, repo, pr_number, url):
    """Same fire-and-poll job shape as start_job (see module docstring for
    why: the review pass -- gh diff fetch + a full `claude -p` review turn --
    can easily outlast a proxy's edge timeout), but the worker runs
    github_review.review_pr() instead of a plain chat turn. Reuses the same
    _jobs store so /api/chat/status needs no changes to serve either kind."""
    job_id = uuid.uuid4().hex
    with _lock:
        _prune_locked()
        _jobs[job_id] = {"status": "running", "token": token, "created": time.time()}

    def worker():
        # Same crash guard as start_job's worker -- see the comment there.
        try:
            result, error = github_review.review_pr(owner, repo, pr_number)
        except Exception:
            traceback.print_exc()
            with _lock:
                _jobs[job_id] = {
                    "status": "error", "token": token, "created": time.time(),
                    "error": "internal error during PR review (see server logs)",
                }
            return

        if error:
            with _lock:
                _jobs[job_id] = {
                    "status": "error", "token": token, "created": time.time(),
                    "error": f"GitHub PR review failed: {error}",
                }
            return

        post_result = result["post_result"]
        target = post_result["target"]
        if post_result.get("dry_run"):
            reply = (
                f"[DRY RUN -- {url} was reviewed but no comment was actually posted; "
                f"live posting is currently disabled, see core.settings.GITHUB_REVIEW_COMMENT]\n\n"
                f"Would post to {target} as {result['account']}:\n\n{result['body']}"
            )
        else:
            reply = (
                f"Posted review to {target} as {result['account']}: "
                f"{post_result.get('url', url)}"
            )

        with _lock:
            _jobs[job_id] = {"status": "done", "token": token, "created": time.time(), "reply": reply}

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def get_job(job_id, token):
    """Returns None if the job doesn't exist or belongs to a different
    session token -- callers should treat that as 404, not leak which."""
    with _lock:
        job = _jobs.get(job_id)
    if job is None or job["token"] != token:
        return None
    return job
