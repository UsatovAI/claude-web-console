"""JSON-file-backed persistence for auth config, chat sessions, and night-mode
QA summaries."""
import json
import os
import time

from . import settings

_MAX_NIGHT_SUMMARIES = 500  # night_summaries.json is a flat append log, capped so it can't grow forever


def load_config():
    with open(settings.CONFIG_PATH) as f:
        return json.load(f)


def load_sessions():
    if not os.path.exists(settings.SESSIONS_PATH):
        return {}
    with open(settings.SESSIONS_PATH) as f:
        return json.load(f)


def save_sessions(sessions):
    tmp = settings.SESSIONS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sessions, f)
    os.replace(tmp, settings.SESSIONS_PATH)


def load_night_summaries():
    if not os.path.exists(settings.NIGHT_SUMMARIES_PATH):
        return []
    with open(settings.NIGHT_SUMMARIES_PATH) as f:
        return json.load(f)


def append_night_summary(token, text):
    """Append one QA-round summary for the chat token that started this
    night-mode run (night_mode.py runs as root, same as the web server
    process, so this write needs no privilege drop)."""
    summaries = load_night_summaries()
    summaries.append({"token": token, "ts": time.time(), "text": text})
    summaries = summaries[-_MAX_NIGHT_SUMMARIES:]
    tmp = settings.NIGHT_SUMMARIES_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(summaries, f)
    os.replace(tmp, settings.NIGHT_SUMMARIES_PATH)


def night_summaries_since(token, since):
    """Summaries for `token` at index >= `since`, within the token-filtered
    sequence (not the raw file's global index) -- returns (new_summaries,
    next_since) so the caller can poll with next_since next time."""
    matching = [s for s in load_night_summaries() if s["token"] == token]
    return matching[since:], len(matching)
