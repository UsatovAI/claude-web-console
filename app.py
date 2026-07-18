#!/usr/bin/env python3
"""Password-gated chat front-end for a headless Claude Code daemon."""
import json
import os
import secrets
import ssl
import subprocess
import time
import hashlib
import hmac
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

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
        else:
            self._send(404, "not found")

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
    if USE_TLS:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_PATH, KEY_PATH)
        server = TLSThreadingHTTPServer(("0.0.0.0", PORT), Handler, ctx)
        print(f"Listening on https://0.0.0.0:{PORT}")
    else:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        print(f"Listening on http://0.0.0.0:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
