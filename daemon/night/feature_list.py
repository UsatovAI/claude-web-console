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


def next_pending(data):
    for feat in data.get("features", []):
        if feat.get("status") == "pending":
            return feat
    return None


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
