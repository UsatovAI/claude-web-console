#!/usr/bin/env python3
"""Password-gated chat front-end for a headless Claude Code daemon."""
import json
import os
import secrets
import ssl
import subprocess
import threading
import time
import hashlib
import hmac
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

import dashboard

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SESSIONS_PATH = os.path.join(BASE_DIR, "sessions.json")
CERT_DOMAIN = os.environ.get("CERT_DOMAIN", "144-124-226-50.sslip.io")
CERT_PATH = f"/etc/letsencrypt/live/{CERT_DOMAIN}/fullchain.pem"
KEY_PATH = f"/etc/letsencrypt/live/{CERT_DOMAIN}/privkey.pem"
USE_TLS = os.path.exists(CERT_PATH) and os.path.exists(KEY_PATH)
DAEMON_USER = os.environ.get("DAEMON_USER", "claudeweb")
PORT = int(os.environ.get("PORT", "443" if USE_TLS else "8080"))
LOGIN_WINDOW_SECS = 300
LOGIN_MAX_ATTEMPTS = 5
CLAUDE_TIMEOUT_SECS = 180

_login_attempts = {}  # ip -> [timestamps of failed attempts]


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_sessions():
    if not os.path.exists(SESSIONS_PATH):
        return {}
    with open(SESSIONS_PATH) as f:
        return json.load(f)


def save_sessions(sessions):
    tmp = SESSIONS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sessions, f)
    os.replace(tmp, SESSIONS_PATH)


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def check_password(password, cfg):
    candidate = hash_password(password, cfg["salt"])
    return hmac.compare_digest(candidate, cfg["password_hash"])


LOGIN_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in</title>
<style>
body{{background:#0f1115;color:#e6e6e6;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
form{{background:#1a1d24;padding:2rem;border-radius:12px;width:min(320px,90vw);box-shadow:0 8px 24px rgba(0,0,0,.4)}}
h1{{font-size:1.1rem;margin:0 0 1rem}}
input{{width:100%;padding:.6rem;border-radius:8px;border:1px solid #333;background:#0f1115;color:#eee;box-sizing:border-box;margin-bottom:.8rem}}
button{{width:100%;padding:.6rem;border:0;border-radius:8px;background:#5865f2;color:#fff;font-weight:600;cursor:pointer}}
.err{{color:#ff6b6b;font-size:.85rem;margin-top:-.4rem;margin-bottom:.8rem}}
</style></head>
<body><form method="POST" action="/login">
<h1>Enter password</h1>
{error}
<input type="password" name="password" autofocus required>
<button type="submit">Unlock</button>
</form></body></html>"""

CHAT_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Console</title>
<style>
body{{background:#0f1115;color:#e6e6e6;font-family:system-ui,sans-serif;margin:0;display:flex;flex-direction:column;height:100vh}}
header{{display:flex;align-items:center;justify-content:space-between;padding:.6rem 1rem;border-bottom:1px solid #222}}
header a{{color:#9aa0f2;text-decoration:none;font-size:.85rem}}
#log{{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:.6rem}}
.msg{{max-width:80%;padding:.6rem .9rem;border-radius:10px;white-space:pre-wrap;word-wrap:break-word}}
.me{{align-self:flex-end;background:#5865f2;color:#fff}}
.bot{{align-self:flex-start;background:#1a1d24}}
.sys{{align-self:center;color:#888;font-size:.8rem}}
form{{display:flex;padding:.8rem;gap:.5rem;border-top:1px solid #222}}
input{{flex:1;padding:.7rem;border-radius:8px;border:1px solid #333;background:#1a1d24;color:#eee}}
button{{padding:.7rem 1.1rem;border:0;border-radius:8px;background:#5865f2;color:#fff;font-weight:600;cursor:pointer}}
button:disabled{{opacity:.5}}
</style></head>
<body>
<header><strong>Console</strong><a href="/dashboard">Dashboard &rarr;</a></header>
<div id="log"></div>
<form id="f"><input id="i" autocomplete="off" placeholder="Message..." autofocus><button id="b">Send</button></form>
<script>
const log = document.getElementById('log');
const form = document.getElementById('f');
const input = document.getElementById('i');
const btn = document.getElementById('b');
function add(text, cls) {{
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}}
form.addEventListener('submit', async (e) => {{
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  add(text, 'me');
  input.value = '';
  btn.disabled = true;
  add('...', 'sys');
  try {{
    const res = await fetch('/api/chat', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{message: text}})}});
    log.removeChild(log.lastChild);
    if (res.status === 401) {{ location.reload(); return; }}
    const data = await res.json();
    add(data.reply || data.error || '(no reply)', 'bot');
  }} catch (err) {{
    log.removeChild(log.lastChild);
    add('Error: ' + err, 'sys');
  }}
  btn.disabled = false;
  input.focus();
}});
</script>
</body></html>"""

DASHBOARD_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard</title>
<style>
:root{
  --surface:#1a1d24; --page:#0f1115; --ink:#e6e6e6; --ink-2:#9a9a9a; --ink-muted:#6b6b6b;
  --grid:#2a2d34; --series-in:#3987e5; --series-out:#d95926;
  --good:#0ca30c; --warn:#fab219; --crit:#e05252;
}
body{background:var(--page);color:var(--ink);font-family:system-ui,sans-serif;margin:0}
header{display:flex;align-items:center;justify-content:space-between;padding:.6rem 1rem;border-bottom:1px solid #222;position:sticky;top:0;background:var(--page)}
header a{color:#9aa0f2;text-decoration:none;font-size:.85rem}
main{padding:1rem;max-width:900px;margin:0 auto;display:flex;flex-direction:column;gap:1.2rem}
h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;color:var(--ink-2);margin:0 0 .6rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.7rem}
.tile{background:var(--surface);border-radius:10px;padding:.8rem .9rem}
.tile .label{font-size:.75rem;color:var(--ink-2)}
.tile .value{font-size:1.4rem;font-weight:600;margin-top:.2rem;font-variant-numeric:tabular-nums}
.card{background:var(--surface);border-radius:10px;padding:1rem}
.meter-row{display:flex;flex-direction:column;gap:.7rem}
.meter{display:grid;grid-template-columns:90px 1fr 70px;align-items:center;gap:.6rem;font-size:.85rem}
.meter .track{height:10px;border-radius:6px;background:#0f1115;overflow:hidden}
.meter .fill{height:100%;border-radius:6px;transition:width .4s ease}
.meter .pct{text-align:right;font-variant-numeric:tabular-nums;color:var(--ink-2)}
.legend{display:flex;gap:1rem;font-size:.8rem;color:var(--ink-2);margin-bottom:.5rem}
.legend span{display:inline-flex;align-items:center;gap:.35rem}
.swatch{width:10px;height:10px;border-radius:2px;display:inline-block}
#chart{width:100%;height:180px;display:block}
.axis-label{font-size:.65rem;fill:var(--ink-muted)}
.tablewrap{display:none;margin-top:.6rem;max-height:220px;overflow:auto}
.tablewrap.show{display:block}
table{width:100%;border-collapse:collapse;font-size:.78rem}
th,td{text-align:right;padding:.25rem .5rem;border-bottom:1px solid var(--grid)}
th:first-child,td:first-child{text-align:left}
.linklike{background:none;border:0;color:#9aa0f2;font-size:.78rem;cursor:pointer;padding:0}
.stale{color:var(--ink-muted);font-size:.75rem}
</style></head>
<body>
<header><strong>Dashboard</strong><a href="/">&larr; Console</a></header>
<main>
  <section>
    <h2>Claude token usage</h2>
    <div class="tiles">
      <div class="tile"><div class="label">Input tokens</div><div class="value" id="t-in">-</div></div>
      <div class="tile"><div class="label">Output tokens</div><div class="value" id="t-out">-</div></div>
      <div class="tile"><div class="label">Cache (read+write)</div><div class="value" id="t-cache">-</div></div>
      <div class="tile"><div class="label">Total vs limit</div><div class="value" id="t-total">-</div></div>
    </div>
  </section>

  <section class="card">
    <div class="legend">
      <span><i class="swatch" style="background:var(--series-in)"></i>Input / 5 min</span>
      <span><i class="swatch" style="background:var(--series-out)"></i>Output / 5 min</span>
      <span class="stale" id="last-collected" style="margin-left:auto"></span>
    </div>
    <svg id="chart" viewBox="0 0 640 180" preserveAspectRatio="none"></svg>
    <button class="linklike" id="toggle-table">Show as table</button>
    <div class="tablewrap" id="tablewrap">
      <table><thead><tr><th>Time</th><th>Input</th><th>Output</th></tr></thead><tbody id="tbody"></tbody></table>
    </div>
  </section>

  <section class="card">
    <h2>Token budget</h2>
    <div class="meter-row">
      <div class="meter"><span>Used / limit</span><div class="track"><div class="fill" id="m-limit-fill"></div></div><span class="pct" id="m-limit-pct">-</span></div>
    </div>
  </section>

  <section class="card">
    <h2>Host resources</h2>
    <div class="meter-row">
      <div class="meter"><span>CPU</span><div class="track"><div class="fill" id="m-cpu-fill"></div></div><span class="pct" id="m-cpu-pct">-</span></div>
      <div class="meter"><span>RAM</span><div class="track"><div class="fill" id="m-ram-fill"></div></div><span class="pct" id="m-ram-pct">-</span></div>
      <div class="meter"><span>Disk</span><div class="track"><div class="fill" id="m-disk-fill"></div></div><span class="pct" id="m-disk-pct">-</span></div>
    </div>
  </section>
</main>
<script>
function fmt(n) {
  if (n == null) return '-';
  return n.toLocaleString();
}
function statusColor(pct) {
  if (pct >= 90) return 'var(--crit)';
  if (pct >= 70) return 'var(--warn)';
  return 'var(--good)';
}
function setMeter(prefix, used, total, pct) {
  document.getElementById('m-' + prefix + '-fill').style.width = Math.min(100, pct) + '%';
  document.getElementById('m-' + prefix + '-fill').style.background = statusColor(pct);
  document.getElementById('m-' + prefix + '-pct').textContent = pct.toFixed(1) + '%';
}
function drawChart(history) {
  const svg = document.getElementById('chart');
  const W = 640, H = 180, PAD = 20;
  svg.innerHTML = '';
  const pts = history.slice(-60);
  if (pts.length < 2) {
    svg.innerHTML = '<text x="20" y="90" fill="#6b6b6b" font-size="12">Not enough samples yet (collects every 5 min)</text>';
    return;
  }
  const deltas = [];
  for (let i = 1; i < pts.length; i++) {
    deltas.push({
      ts: pts[i].ts,
      input: Math.max(0, pts[i].input_tokens - pts[i-1].input_tokens),
      output: Math.max(0, pts[i].output_tokens - pts[i-1].output_tokens),
    });
  }
  const maxVal = Math.max(1, ...deltas.map(d => d.input + d.output));
  const bw = (W - PAD*2) / deltas.length;
  const scaleY = v => (H - PAD*2) * (v / maxVal);
  let bars = '';
  deltas.forEach((d, i) => {
    const x = PAD + i * bw;
    const hIn = scaleY(d.input);
    const hOut = scaleY(d.output);
    const yBase = H - PAD;
    bars += `<rect x="${x}" y="${yBase - hIn}" width="${Math.max(1, bw*0.4)}" height="${hIn}" fill="var(--series-in)"></rect>`;
    bars += `<rect x="${x + bw*0.45}" y="${yBase - hOut}" width="${Math.max(1, bw*0.4)}" height="${hOut}" fill="var(--series-out)"></rect>`;
  });
  const gridline = `<line x1="${PAD}" y1="${H-PAD}" x2="${W-PAD}" y2="${H-PAD}" stroke="var(--grid)"></line>`;
  svg.innerHTML = gridline + bars;

  const tbody = document.getElementById('tbody');
  tbody.innerHTML = deltas.slice().reverse().map(d =>
    `<tr><td>${new Date(d.ts*1000).toLocaleString()}</td><td>${fmt(d.input)}</td><td>${fmt(d.output)}</td></tr>`
  ).join('');
}
async function refresh() {
  try {
    const res = await fetch('/api/stats');
    if (res.status === 401) { location.href = '/'; return; }
    const data = await res.json();
    document.getElementById('t-in').textContent = fmt(data.tokens.input_tokens);
    document.getElementById('t-out').textContent = fmt(data.tokens.output_tokens);
    document.getElementById('t-cache').textContent = fmt(data.tokens.cache_creation_tokens + data.tokens.cache_read_tokens);
    const total = data.tokens.input_tokens + data.tokens.output_tokens;
    document.getElementById('t-total').textContent = fmt(total) + (data.tokens.limit ? ' / ' + fmt(data.tokens.limit) : '');
    if (data.tokens.limit) {
      const pct = Math.min(999, total / data.tokens.limit * 100);
      setMeter('limit', total, data.tokens.limit, pct);
    } else {
      document.getElementById('m-limit-pct').textContent = 'no limit set';
    }
    setMeter('cpu', null, null, data.system.cpu_percent);
    setMeter('ram', data.system.mem.used_mb, data.system.mem.total_mb, data.system.mem.percent);
    setMeter('disk', data.system.disk.used_gb, data.system.disk.total_gb, data.system.disk.percent);
    drawChart(data.history);
    if (data.last_collected) {
      document.getElementById('last-collected').textContent = 'Last collected: ' + new Date(data.last_collected*1000).toLocaleTimeString();
    }
  } catch (err) {
    console.error(err);
  }
}
document.getElementById('toggle-table').addEventListener('click', () => {
  const w = document.getElementById('tablewrap');
  const on = w.classList.toggle('show');
  document.getElementById('toggle-table').textContent = on ? 'Hide table' : 'Show as table';
});
refresh();
setInterval(refresh, 15000);
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "console/1.0"

    def log_message(self, fmt, *args):
        pass

    def _cookie_token(self):
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        c = SimpleCookie()
        c.load(cookie_header)
        if "session" in c:
            return c["session"].value
        return None

    def _authed(self):
        token = self._cookie_token()
        if not token:
            return None
        sessions = load_sessions()
        return token if token in sessions else None

    def _send(self, status, body, headers=None):
        body_b = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body_b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body_b)

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            token = self._authed()
            if not token:
                self._send(200, LOGIN_PAGE.format(error=""))
            else:
                self._send(200, CHAT_PAGE.format())
        elif self.path == "/dashboard":
            if not self._authed():
                self._send(200, LOGIN_PAGE.format(error=""))
            else:
                self._send(200, DASHBOARD_PAGE)
        elif self.path == "/api/stats":
            self._handle_stats()
        else:
            self._send(404, "not found")

    def _handle_stats(self):
        if not self._authed():
            self._send_json(401, {"error": "not authenticated"})
            return
        cfg = load_config()
        data = dashboard.load_history()
        history = data.get("history", [])
        totals = history[-1] if history else dashboard.latest_totals(DAEMON_USER)
        self._send_json(200, {
            "tokens": {
                "input_tokens": totals.get("input_tokens", 0),
                "output_tokens": totals.get("output_tokens", 0),
                "cache_creation_tokens": totals.get("cache_creation_tokens", 0),
                "cache_read_tokens": totals.get("cache_read_tokens", 0),
                "limit": cfg.get("token_limit"),
            },
            "history": history[-100:],
            "last_collected": history[-1]["ts"] if history else None,
            "system": dashboard.system_stats(),
        })

    def do_POST(self):
        if self.path == "/login":
            self._handle_login()
        elif self.path == "/api/chat":
            self._handle_chat()
        else:
            self._send(404, "not found")

    def _client_ip(self):
        return self.client_address[0]

    def _rate_limited(self, ip):
        now = time.time()
        attempts = [t for t in _login_attempts.get(ip, []) if now - t < LOGIN_WINDOW_SECS]
        _login_attempts[ip] = attempts
        return len(attempts) >= LOGIN_MAX_ATTEMPTS

    def _record_failure(self, ip):
        _login_attempts.setdefault(ip, []).append(time.time())

    def _handle_login(self):
        ip = self._client_ip()
        if self._rate_limited(ip):
            self._send(429, LOGIN_PAGE.format(error='<div class="err">Too many attempts. Wait a few minutes.</div>'))
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        fields = parse_qs(body)
        password = fields.get("password", [""])[0]
        cfg = load_config()
        if not password or not check_password(password, cfg):
            self._record_failure(ip)
            self._send(401, LOGIN_PAGE.format(error='<div class="err">Wrong password.</div>'))
            return
        token = secrets.token_urlsafe(32)
        sessions = load_sessions()
        sessions[token] = {"created": time.time(), "claude_session_id": None}
        save_sessions(sessions)
        cookie_attrs = "; Secure" if USE_TLS else ""
        self._send(303, "", headers={
            "Location": "/",
            "Set-Cookie": f"session={token}; HttpOnly; SameSite=Strict; Max-Age=31536000; Path=/{cookie_attrs}",
        })

    def _handle_chat(self):
        token = self._authed()
        if not token:
            self._send_json(401, {"error": "not authenticated"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length).decode())
        except Exception:
            self._send_json(400, {"error": "bad request"})
            return
        message = (data.get("message") or "").strip()
        if not message:
            self._send_json(400, {"error": "empty message"})
            return

        sessions = load_sessions()
        claude_session_id = sessions.get(token, {}).get("claude_session_id")
        claude_bin = f"/home/{DAEMON_USER}/.local/bin/claude"
        cmd = [claude_bin, "-p", message, "--output-format", "json", "--dangerously-skip-permissions"]
        if claude_session_id:
            cmd += ["--resume", claude_session_id]
        daemon_home = "/home/" + DAEMON_USER
        try:
            proc = subprocess.run(
                cmd, cwd=daemon_home, user=DAEMON_USER,
                env={**os.environ, "HOME": daemon_home},
                capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired:
            self._send_json(504, {"error": "claude timed out"})
            return
        if proc.returncode != 0:
            self._send_json(500, {"error": f"claude exited {proc.returncode}: {proc.stderr[:500]}"})
            return
        try:
            result = json.loads(proc.stdout)
        except Exception:
            self._send_json(500, {"error": "could not parse claude output"})
            return

        new_session_id = result.get("session_id")
        if new_session_id:
            sessions.setdefault(token, {})["claude_session_id"] = new_session_id
            save_sessions(sessions)

        self._send_json(200, {"reply": result.get("result", "")})


class TLSThreadingHTTPServer(ThreadingHTTPServer):
    """Wraps only the accepted connection socket (not the listening socket) and
    performs the TLS handshake inside the per-connection worker thread, so a
    stalled/malicious handshake can't block the main accept loop."""
    daemon_threads = True

    def __init__(self, addr, handler, ssl_context):
        self.ssl_context = ssl_context
        super().__init__(addr, handler)

    def get_request(self):
        newsocket, fromaddr = socketserver.TCPServer.get_request(self)
        connstream = self.ssl_context.wrap_socket(
            newsocket, server_side=True, do_handshake_on_connect=False)
        return connstream, fromaddr

    def finish_request(self, request, client_address):
        request.settimeout(20)
        try:
            request.do_handshake()
        except Exception:
            try:
                request.close()
            except Exception:
                pass
            return
        request.settimeout(None)
        super().finish_request(request, client_address)


def main():
    stop_collector = threading.Event()
    collector_thread = threading.Thread(
        target=dashboard.collector_loop, args=(DAEMON_USER, stop_collector), daemon=True)
    collector_thread.start()

    if USE_TLS:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_PATH, KEY_PATH)
        server = TLSThreadingHTTPServer(("0.0.0.0", PORT), Handler, ctx)
        print(f"Listening on https://0.0.0.0:{PORT}")
    else:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        print(f"Listening on http://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    finally:
        stop_collector.set()


if __name__ == "__main__":
    main()
