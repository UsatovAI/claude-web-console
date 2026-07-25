"""Shared configuration constants for the console app and its background
workers.

Every constant below is derived from config.yaml (via core/config.py)
instead of `os.environ.get(...)` -- config.py is the one place that actually
parses the YAML; this module just gives the rest of the app stable,
importable names for the values it cares about, computing a few derived
paths/defaults (CERT_PATH, USE_TLS, PORT's auto-selection) along the way.
"""
import os

from . import config

PKG_DIR = os.path.dirname(os.path.abspath(__file__))  # .../site/core
BASE_DIR = os.path.dirname(PKG_DIR)  # .../site (repo root)

VAR_DIR = os.path.join(BASE_DIR, "var")
STATE_DIR = os.path.join(VAR_DIR, "state")  # config, sessions, other runtime state
LOG_DIR = os.path.join(VAR_DIR, "log")

CONFIG_PATH = os.path.join(STATE_DIR, "config.json")
SESSIONS_PATH = os.path.join(STATE_DIR, "sessions.json")
# SCRUM-10: per-round QA summaries night_mode.py appends to, so the web
# console can push them into chat instead of the user having to poll
# /night status -- see core/storage.py's append_night_summary/night_summaries_since.
NIGHT_SUMMARIES_PATH = os.path.join(STATE_DIR, "night_summaries.json")

# Human-edited feature flags/static config -- see core/config.py. Separate
# from CONFIG_PATH above, which is this app's own runtime state (JSON,
# written by the app, not hand-edited).
YAML_CONFIG_PATH = config.YAML_CONFIG_PATH

CERT_DOMAIN = config.get("cert_domain")
CERT_PATH = f"/etc/letsencrypt/live/{CERT_DOMAIN}/fullchain.pem"
KEY_PATH = f"/etc/letsencrypt/live/{CERT_DOMAIN}/privkey.pem"
USE_TLS = os.path.exists(CERT_PATH) and os.path.exists(KEY_PATH)
_port_override = config.get("port")
PORT = int(_port_override) if _port_override else (443 if USE_TLS else 8080)

DAEMON_USER = config.get("daemon_user")
# Path to the *secrets* file (VPS/GitHub credentials, and whatever the daemon
# user's own Claude Code auth needs -- see claude_daemon.py's docstring for
# the current mechanism) -- read by daemon.claude_daemon.load_daemon_env()
# and injected only into the daemon subprocess's env. This is a path
# reference; the secrets it points to are deliberately not config.yaml keys,
# since config.yaml is git-tracked and this file's contents are not.
ENV_FILE_PATH = config.get("daemon_env_file")

LOGIN_WINDOW_SECS = 300
LOGIN_MAX_ATTEMPTS = 5

TIMEOUT_TIERS = {"5m": 300, "15m": 900, "30m": 1800}
DEFAULT_TIMEOUT_TIER = "5m"

# SCRUM-11: "-" skips the min-duration floor entirely, so Claude replies as
# soon as it's actually done instead of being held to a floor and fed the
# extra "keep working / validate completeness / install missing tools"
# instructions (web/chat_jobs.py's _run_with_min_duration) -- same cap as
# the default tier, just no floor.
NO_MIN_TIER = "-"
MIN_DURATION_TIERS = TIMEOUT_TIERS
ALL_TIMEOUT_TIERS = {**MIN_DURATION_TIERS, NO_MIN_TIER: TIMEOUT_TIERS[DEFAULT_TIMEOUT_TIER]}

# Token budget is tracked as a rolling window, not an all-time total, since
# Claude's own usage limits reset on a rolling basis rather than accumulating forever.
USAGE_WINDOW_SECS = 5 * 3600

NIGHT_CRON_LOG = os.path.join(LOG_DIR, "night_mode_cron.log")

# SCRUM-13: whether a chat-triggered GitHub PR review (web/github_review.py)
# is offered at all, and whether it actually posts its comment (under the
# UsatovAI identity) to the PR or only builds the request and reports what
# it would have sent. Individual calls into
# github_review.review_pr()/post_review_comment() can still override this
# per-call via their own dry_run argument.
GITHUB_REVIEW = bool(config.get("github_review"))
GITHUB_REVIEW_COMMENT = bool(config.get("github_review_comment"))

# ~8h autonomous night-mode loop (daemon/night_mode.py): total wall-clock
# budget, the longest any single `claude -p` call within it may run, and the
# minimum session length its Stop hook enforces on the executor role so it
# can't bail after a token effort. Centralized here so night_mode.py reads
# one value instead of parsing its own environment variables.
NIGHT_MODE_BUDGET_SECS = int(config.get("night_mode_budget_secs"))
NIGHT_MODE_MAX_CALL_SECS = int(config.get("night_mode_max_call_secs"))
NIGHT_MIN_SESSION_SECS = int(config.get("night_min_session_secs"))
