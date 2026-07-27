"""Reader for config.yaml -- human-edited, operator-facing feature flags and
static settings. This is the single place every non-secret configuration
knob in this app is read from; core/settings.py derives its constants from
it instead of scattering `os.environ.get(...)` calls across modules.

Deliberately self-contained (computes its own file location rather than
importing core.settings): core/settings.py itself reads through this module
to build several of its own constants, so importing settings here would
create an import cycle.

Also deliberately separate from core/storage.py, which handles this app's
own runtime state (password hash, chat sessions) as JSON in var/state/. That
state is written by the app itself and never hand-edited; this file is the
other way around: written by a human, only ever read by the app.

Secrets (VPS/GitHub credentials) do NOT belong here -- config.yaml is
git-tracked. They live in the separate file pointed to by the
daemon_env_file key below, read only by daemon/claude_daemon.py's
load_daemon_env() straight into a subprocess environment, never through
this module.
"""
import os

import yaml

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_PKG_DIR)
YAML_CONFIG_PATH = os.path.join(_BASE_DIR, "config.yaml")

_DEFAULTS = {
    "github_review": False,
    "github_review_comment": False,
    "port": None,  # None = auto: 443 if a TLS cert for CERT_DOMAIN exists, else 8080
    "daemon_user": "claudeweb",
    "daemon_env_file": "/home/claudeweb/env/.env",
    "night_mode_budget_secs": 8 * 3600,
    "night_mode_max_call_secs": 3600,
    "night_min_session_secs": 600,
    "session_max_age_secs": 7 * 24 * 3600,
}


def load():
    """Parses config.yaml fresh on every call rather than caching it -- the
    file is small and edited rarely, so re-parsing it is cheap, and it means
    an operator's edit takes effect on the next read instead of requiring a
    process restart. Missing keys fall back to _DEFAULTS; a missing file
    behaves as if it were empty (all defaults)."""
    try:
        with open(YAML_CONFIG_PATH) as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        data = {}
    return {**_DEFAULTS, **data}


def get(key, default=None):
    return load().get(key, default)
