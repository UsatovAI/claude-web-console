"""Reads Claude Code session transcripts for the daemon user.

Gives the web console the equivalent of `claude --resume`: a list of past
sessions to switch into, and each session's message history to render as
chat bubbles. Sourced from the same ~/.claude/projects/**/*.jsonl transcript
files dashboard.py already scans for token usage -- no extra bookkeeping.
"""
import glob
import json
import os

from core import settings


def _project_dirs():
    base = f"/home/{settings.DAEMON_USER}/.claude/projects"
    if not os.path.isdir(base):
        return []
    return [os.path.join(base, d) for d in os.listdir(base)
            if os.path.isdir(os.path.join(base, d))]


def _session_path(session_id):
    for d in _project_dirs():
        p = os.path.join(d, session_id + ".jsonl")
        if os.path.exists(p):
            return p
    return None


def _iter_entries(path):
    with open(path, errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _extract_text(content):
    """Pull plain reply/prompt text out of a message's content, skipping
    thinking/tool_use/tool_result blocks -- those aren't user-facing text."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p).strip()
    return ""


def list_sessions(limit=50):
    """Session summaries, most recently active first."""
    sessions = []
    for d in _project_dirs():
        for path in glob.glob(os.path.join(d, "*.jsonl")):
            session_id = os.path.basename(path)[:-len(".jsonl")]
            preview = None
            last_ts = None
            turns = 0
            for entry in _iter_entries(path):
                if entry.get("timestamp"):
                    last_ts = entry["timestamp"]
                if entry.get("type") not in ("user", "assistant"):
                    continue
                text = _extract_text((entry.get("message") or {}).get("content"))
                if not text:
                    continue
                turns += 1
                if preview is None and entry.get("type") == "user":
                    preview = text[:120].replace("\n", " ")
            if turns == 0:
                continue
            sessions.append({
                "session_id": session_id,
                "preview": preview or "(no preview)",
                "last_ts": last_ts,
                "turns": turns,
            })
    sessions.sort(key=lambda s: s["last_ts"] or "", reverse=True)
    return sessions[:limit]


def load_messages(session_id):
    """[{role, text}] for a session's real (non-tool) turns, in order.
    Returns None if the session doesn't exist."""
    path = _session_path(session_id)
    if not path:
        return None
    messages = []
    for entry in _iter_entries(path):
        if entry.get("type") not in ("user", "assistant"):
            continue
        message = entry.get("message") or {}
        text = _extract_text(message.get("content"))
        if not text:
            continue
        messages.append({"role": message.get("role"), "text": text})
    return messages
