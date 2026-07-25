"""Read-only helpers for the feature_list.json the planner writes and the
executor/QA sessions edit directly with their own Read/Edit tools.

The orchestrator (night_mode.py, running as root) only ever reads this file
to decide what to do next -- it never writes feature status itself. Claude
edits it from inside each `claude -p` call, same pattern as the reference
autonomous-coding quickstart.
"""
import json


def load(path):
    with open(path) as f:
        return json.load(f)


def _passing_ids(data):
    return {f["id"] for f in data.get("features", []) if f.get("status") == "passing"}


def _deps_satisfied(feat, passing_ids):
    return all(dep in passing_ids for dep in feat.get("depends_on") or [])


def next_pending(data):
    """First pending feature whose depends_on ids (if any) are all already
    passing -- a feature blocked on an unfinished dependency is skipped, not
    picked out of order (SCRUM-10's depends_on requirement)."""
    passing_ids = _passing_ids(data)
    for feat in data.get("features", []):
        if feat.get("status") == "pending" and _deps_satisfied(feat, passing_ids):
            return feat
    return None


def has_blocked_pending(data):
    """True if there's pending work that next_pending won't return because
    its dependencies aren't satisfied yet -- distinguishes "genuinely nothing
    left to do" from "stuck waiting on a dependency" for clearer logging."""
    passing_ids = _passing_ids(data)
    return any(
        feat.get("status") == "pending" and not _deps_satisfied(feat, passing_ids)
        for feat in data.get("features", [])
    )


def next_implemented(data):
    for feat in data.get("features", []):
        if feat.get("status") == "implemented":
            return feat
    return None


def all_passing(data):
    feats = data.get("features", [])
    return bool(feats) and all(f.get("status") == "passing" for f in feats)


def summary(data):
    feats = data.get("features", [])
    total = len(feats)
    passing = sum(1 for f in feats if f.get("status") == "passing")
    implemented = sum(1 for f in feats if f.get("status") == "implemented")
    pending = total - passing - implemented
    return f"{passing}/{total} passing, {implemented} awaiting QA, {pending} pending"
