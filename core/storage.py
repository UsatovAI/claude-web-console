"""JSON-file-backed persistence for auth config and chat sessions."""
import json
import os

from . import settings


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
