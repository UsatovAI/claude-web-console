"""SCRUM-13 ("Github usage"): detect a chat message asking to review a GitHub
PR/MR, fetch its diff, review it with this project's own code-review-skill,
and post the review as a comment on the PR itself under the UsatovAI GitHub
identity -- not just echo the review back into the chat UI (that part already
worked, wired up manually in an earlier session).

Credential handling mirrors daemon.claude_daemon.load_daemon_env(): the PAT
(GHP_KEY in settings.ENV_FILE_PATH) is read server-side and handed to the `gh`
subprocess only via its environment (as GH_TOKEN, which `gh` reads natively) --
never placed in argv or a URL, so it can't leak into a process listing or a
request log. The comment body itself is piped over stdin (`--body-file -`)
rather than passed as an argument, for the same "nothing but flags in argv"
reason (and so an arbitrarily long review never risks hitting an argv length
limit).

Posting is gated behind an explicit `dry_run` parameter on every function that
can write to GitHub, defaulting to `not settings.GITHUB_REVIEW_COMMENT` --
see settings.py/config.yaml for that flag.
"""
import os
import re
import subprocess

from core import settings
from daemon import claude_daemon

# Deliberately simple signal per SCRUM-13: any github.com PR/MR URL in the
# message is enough to trigger a review -- no attempt to also parse verbs
# like "review"/"check" out of surrounding text. A pasted PR link *is* the
# request ("I ask in chat to review MR github"); overbuilding NLU here would
# just add ways to miss it.
PR_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>\d+)",
    re.IGNORECASE,
)

GH_TOKEN_ENV_KEY = "GHP_KEY"
GH_ACCOUNT_ENV_KEY = "GITHUB_ACCOUNT"
DEFAULT_ACCOUNT = "UsatovAI"

DIFF_FETCH_TIMEOUT_SECS = 60
COMMENT_POST_TIMEOUT_SECS = 30
REVIEW_GENERATION_TIMEOUT_SECS = 900  # `claude -p` pass over the diff, code-review-skill


def detect_pr_request(message):
    """Return (owner, repo, pr_number, url) for the first github.com PR URL
    found in `message`, or None if there isn't one."""
    m = PR_URL_RE.search(message or "")
    if not m:
        return None
    return m.group("owner"), m.group("repo"), int(m.group("number")), m.group(0)


def fetch_pr_diff(owner, repo, pr_number, gh_token=None):
    """`gh pr diff <n> --repo <owner>/<repo>` -- a read-only call. Public repos
    generally work unauthenticated; a token, if we have one, is still passed
    through the environment (never argv) since some `gh` setups otherwise
    refuse to run at all without any auth context, and it can't hurt for a
    read. Returns (diff_text, error)."""
    cmd = ["gh", "pr", "diff", str(pr_number), "--repo", f"{owner}/{repo}"]
    env = dict(os.environ)
    if gh_token:
        env["GH_TOKEN"] = gh_token
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DIFF_FETCH_TIMEOUT_SECS, env=env)
    except subprocess.TimeoutExpired:
        return None, "timed out fetching PR diff"
    if proc.returncode != 0:
        return None, f"gh pr diff failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}"
    if not proc.stdout.strip():
        return None, "PR diff was empty"
    return proc.stdout, None


def build_review_prompt(owner, repo, pr_number, diff):
    """Prompt for a fresh `claude -p` session, instructed to use the
    code-review-skill already installed for this daemon user (mirrored into
    claudeweb's ~/.claude/skills by daemon/night/setup.py's QA-skill sync) and
    to use its actual severity taxonomy verbatim -- not invented labels."""
    return f"""Use the code-review-skill skill to review the pull request diff below, from
{owner}/{repo} PR #{pr_number}. Follow the skill's review process (context, high-level,
line-by-line, summary) and use its actual severity labels exactly as defined in the skill,
nothing invented:

- \U0001F534 `[blocking]`   - must fix before merge
- \U0001F7E1 `[important]`  - should fix, discuss if you disagree
- \U0001F7E2 `[nit]`        - nice to have, not blocking
- \U0001F4A1 `[suggestion]` - alternative approach to consider (non-blocking)
- \U0001F4DA `[learning]`   - educational note, no action needed (non-blocking)
- \U0001F389 `[praise]`     - good work, called out explicitly (non-blocking)

Output ONLY the finished review, written as a single ready-to-post GitHub PR comment in
GitHub-flavored Markdown: a short overall summary, then specific findings (each labeled with one
of the tags above, referencing file paths/hunks from the diff), then a closing
Approve / Comment / Request Changes call per the skill's Phase 4. No meta-commentary about being
an AI, no clarifying questions -- nobody is watching this session, so use your best judgment and
produce the finished comment text directly.

--- PR DIFF START ---
{diff}
--- PR DIFF END ---
"""


def generate_review(owner, repo, pr_number, diff, timeout=REVIEW_GENERATION_TIMEOUT_SECS):
    """Runs the review as a fresh, standalone `claude -p` session (no
    --resume, same "clean context per role" pattern used by night mode's
    planner/executor/QA roles) so it isn't influenced by whatever the user's
    ongoing chat conversation happens to contain. Returns (review_text, error)."""
    prompt = build_review_prompt(owner, repo, pr_number, diff)
    result, error, _owner_used = claude_daemon.run_claude(prompt, session_id=None, timeout=timeout)
    if error:
        return None, f"review generation failed: {error}"
    review_text = (result or {}).get("result", "").strip()
    if not review_text:
        return None, "review generation produced no output"
    return review_text, None


def format_comment(review_text, account):
    return (
        f"{review_text}\n\n"
        f"---\n"
        f"*Automated review by [@{account}](https://github.com/{account}).*"
    )


def post_review_comment(owner, repo, pr_number, body, gh_token, *, dry_run):
    """POST the comment (`gh pr comment <n> --repo <owner>/<repo> --body-file -`,
    body on stdin, token via env only) unless dry_run -- in which case the
    exact command/target/identity/body that *would* be sent is returned
    instead of anything being sent. Returns (result_dict, error)."""
    target = f"{owner}/{repo}#{pr_number}"
    cmd = ["gh", "pr", "comment", str(pr_number), "--repo", f"{owner}/{repo}", "--body-file", "-"]

    if dry_run:
        return {
            "dry_run": True,
            "target": target,
            "command": " ".join(cmd) + "  # body piped via stdin, GH_TOKEN via env -- neither shown here",
            "body": body,
        }, None

    if not gh_token:
        return None, f"GH_TOKEN not available ({GH_TOKEN_ENV_KEY} missing from {settings.ENV_FILE_PATH}); refusing to post live"

    env = dict(os.environ)
    env["GH_TOKEN"] = gh_token
    try:
        proc = subprocess.run(cmd, input=body, capture_output=True, text=True,
                               timeout=COMMENT_POST_TIMEOUT_SECS, env=env)
    except subprocess.TimeoutExpired:
        return None, "timed out posting PR comment"
    if proc.returncode != 0:
        return None, f"gh pr comment failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}"
    return {"dry_run": False, "target": target, "url": proc.stdout.strip()}, None


def review_pr(owner, repo, pr_number, *, dry_run=None):
    """End-to-end: fetch diff -> generate review (code-review-skill) -> post
    comment (or dry-run it). `dry_run=None` (the default used by the real
    chat path) defers to `not settings.GITHUB_REVIEW_COMMENT`; pass True/False
    explicitly to override for a one-off call, e.g. verification.

    Returns (result_dict, error). result_dict has "account", "body", and
    "post_result" (itself carrying "dry_run" plus either the request that
    would be sent or the live outcome)."""
    if dry_run is None:
        dry_run = not settings.GITHUB_REVIEW_COMMENT

    env = claude_daemon.load_daemon_env()
    gh_token = env.get(GH_TOKEN_ENV_KEY)
    account = env.get(GH_ACCOUNT_ENV_KEY) or DEFAULT_ACCOUNT

    diff, error = fetch_pr_diff(owner, repo, pr_number, gh_token=gh_token)
    if error:
        return None, f"could not fetch PR diff for {owner}/{repo}#{pr_number}: {error}"

    review_text, error = generate_review(owner, repo, pr_number, diff)
    if error:
        return None, error

    body = format_comment(review_text, account)

    post_result, error = post_review_comment(owner, repo, pr_number, body, gh_token, dry_run=dry_run)
    if error:
        return None, f"review was generated but not posted: {error}"

    return {"account": account, "body": body, "post_result": post_result}, None
