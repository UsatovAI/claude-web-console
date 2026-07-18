"""Claude token usage + host resource collection for the /dashboard page.

Stdlib only, matching app.py's no-dependency constraint. Token usage is scanned
from local Claude Code session transcripts (~/.claude/projects/**/*.jsonl) since
that's where the CLI already records per-turn input/output token counts -- no
extra instrumentation needed on the chat path in app.py.
"""
import glob
import json
import os
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USAGE_PATH = os.path.join(BASE_DIR, "token_usage.json")
COLLECT_INTERVAL_SECS = 300
HISTORY_MAX_POINTS = 2016  # 1 week at 5-min resolution


def _claude_project_dirs(daemon_user):
    candidates = [
        os.path.expanduser("~/.claude/projects"),
        f"/home/{daemon_user}/.claude/projects",
        "/root/.claude/projects",
    ]
    seen = set()
    dirs = []
    for c in candidates:
        if os.path.isdir(c):
            real = os.path.realpath(c)
            if real not in seen:
                seen.add(real)
                dirs.append(c)
    return dirs


def _scan_totals(daemon_user):
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0}
    for d in _claude_project_dirs(daemon_user):
        for path in glob.glob(os.path.join(d, "**", "*.jsonl"), recursive=True):
            try:
                with open(path, "r", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except ValueError:
                            continue
                        if entry.get("type") != "assistant":
                            continue
                        usage = (entry.get("message") or {}).get("usage")
                        if not usage:
                            continue
                        totals["input_tokens"] += usage.get("input_tokens", 0) or 0
                        totals["output_tokens"] += usage.get("output_tokens", 0) or 0
                        totals["cache_creation_tokens"] += usage.get("cache_creation_input_tokens", 0) or 0
                        totals["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0) or 0
            except OSError:
                continue
    return totals


def load_history():
    if not os.path.exists(USAGE_PATH):
        return {"history": []}
    try:
        with open(USAGE_PATH) as f:
            return json.load(f)
    except (ValueError, OSError):
        return {"history": []}


def save_history(data):
    tmp = USAGE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, USAGE_PATH)


def collect_once(daemon_user):
    totals = _scan_totals(daemon_user)
    data = load_history()
    history = data.get("history", [])
    history.append({"ts": time.time(), **totals})
    if len(history) > HISTORY_MAX_POINTS:
        history = history[-HISTORY_MAX_POINTS:]
    data["history"] = history
    save_history(data)
    return history[-1]


def latest_totals(daemon_user):
    data = load_history()
    history = data.get("history", [])
    if history:
        return history[-1]
    return collect_once(daemon_user)


def collector_loop(daemon_user, stop_event):
    while not stop_event.is_set():
        try:
            collect_once(daemon_user)
        except Exception:
            pass
        if stop_event.wait(COLLECT_INTERVAL_SECS):
            break


# ---------- host resource stats (stdlib only, /proc + statvfs) ----------

_prev_cpu = None  # (idle_ticks, total_ticks) from the previous /proc/stat read


def cpu_percent():
    global _prev_cpu
    with open("/proc/stat") as f:
        line = f.readline()
    parts = [int(x) for x in line.split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    total = sum(parts)
    prev = _prev_cpu
    _prev_cpu = (idle, total)
    if prev is None:
        return 0.0
    d_total = total - prev[1]
    d_idle = idle - prev[0]
    if d_total <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (1 - d_idle / d_total) * 100)), 1)


def mem_stats():
    meminfo = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, rest = line.partition(":")
            meminfo[key] = int(rest.strip().split()[0])  # kB
    total = meminfo.get("MemTotal", 0)
    available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
    used = max(0, total - available)
    return {
        "total_mb": round(total / 1024, 1),
        "used_mb": round(used / 1024, 1),
        "percent": round(used / total * 100, 1) if total else 0.0,
    }


def disk_stats(path="/"):
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - free
    return {
        "total_gb": round(total / 1024 ** 3, 2),
        "used_gb": round(used / 1024 ** 3, 2),
        "percent": round(used / total * 100, 1) if total else 0.0,
    }


def system_stats():
    return {"cpu_percent": cpu_percent(), "mem": mem_stats(), "disk": disk_stats()}
