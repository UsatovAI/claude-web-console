"""HTTP request handling: routing, auth gate, and the chat/night-mode/stats endpoints."""
import json
import socketserver
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import auth
import claude_daemon
import dashboard
import night_control
import settings
import storage
import templates


class Handler(BaseHTTPRequestHandler):
    server_version = "console/1.0"

    def log_message(self, fmt, *args):
        pass

    def _authed(self):
        return auth.authed_token(self.headers.get("Cookie"))

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
            if not self._authed():
                self._send(200, templates.LOGIN_PAGE.format(error=""))
            else:
                self._send(200, templates.CHAT_PAGE.format())
        elif self.path == "/dashboard":
            if not self._authed():
                self._send(200, templates.LOGIN_PAGE.format(error=""))
            else:
                self._send(200, templates.DASHBOARD_PAGE)
        elif self.path == "/api/stats":
            self._handle_stats()
        else:
            self._send(404, "not found")

    def _handle_stats(self):
        if not self._authed():
            self._send_json(401, {"error": "not authenticated"})
            return
        cfg = storage.load_config()
        data = dashboard.load_history()
        history = data.get("history", [])
        totals = history[-1] if history else dashboard.latest_totals(settings.DAEMON_USER)
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

    def _handle_login(self):
        ip = self._client_ip()
        if auth.rate_limited(ip):
            self._send(429, templates.LOGIN_PAGE.format(error='<div class="err">Too many attempts. Wait a few minutes.</div>'))
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        fields = parse_qs(body)
        password = fields.get("password", [""])[0]
        cfg = storage.load_config()
        if not password or not auth.check_password(password, cfg):
            auth.record_failure(ip)
            self._send(401, templates.LOGIN_PAGE.format(error='<div class="err">Wrong password.</div>'))
            return
        token = auth.new_session_token()
        sessions = storage.load_sessions()
        sessions[token] = {"created": time.time(), "claude_session_id": None}
        storage.save_sessions(sessions)
        cookie_attrs = "; Secure" if settings.USE_TLS else ""
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
        if message.lower() == "/night" or message.lower().startswith("/night "):
            reply = night_control.handle_command(message[len("/night"):].strip())
            self._send_json(200, {"reply": reply})
            return

        timeout_secs = settings.TIMEOUT_TIERS.get(
            data.get("timeout"), settings.TIMEOUT_TIERS[settings.DEFAULT_TIMEOUT_TIER])

        sessions = storage.load_sessions()
        claude_session_id = sessions.get(token, {}).get("claude_session_id")
        result, error = claude_daemon.run_claude(message, session_id=claude_session_id, timeout=timeout_secs)
        if error == "timeout":
            self._send_json(504, {"error": "claude timed out"})
            return
        if error:
            self._send_json(500, {"error": f"claude error: {error}"})
            return

        new_session_id = result.get("session_id")
        if new_session_id:
            sessions.setdefault(token, {})["claude_session_id"] = new_session_id
            storage.save_sessions(sessions)

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
