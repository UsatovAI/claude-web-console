"""In-memory job store so /api/chat can return immediately instead of
holding one HTTP connection open for the full duration of a `claude -p`
call.

This exists because any proxy in front of this server enforces its own
connection timeout well under this app's own TIMEOUT_TIERS -- e.g.
Cloudflare's ~100s default edge-to-origin timeout on Free/Pro/Business plans,
which is silently far shorter than every one of the 5m/15m/1h tiers users
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
import uuid

from core import storage
from daemon import claude_daemon
from . import github_review

_lock = threading.Lock()
_jobs = {}  # job_id -> {"status", "token", "created", ["reply"|"error"]}

_PRUNE_AFTER_SECS = 900


def _prune_locked():
    cutoff = time.time() - _PRUNE_AFTER_SECS
    stale = [jid for jid, v in _jobs.items() if v["status"] != "running" and v["created"] < cutoff]
    for jid in stale:
        del _jobs[jid]


def start_job(token, message, session_id, session_owner, timeout_secs):
    job_id = uuid.uuid4().hex
    with _lock:
        _prune_locked()
        _jobs[job_id] = {"status": "running", "token": token, "created": time.time()}

    def worker():
        result, error, owner_used = claude_daemon.run_claude(
            message, session_id=session_id, session_owner=session_owner, timeout=timeout_secs)

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
        result, error = github_review.review_pr(owner, repo, pr_number)

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
