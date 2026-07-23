"""HTTP request handling: routing, auth gate, and the chat/night-mode/stats endpoints."""
import json
import socketserver
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from core import auth, settings, storage
from daemon import night_control, transcripts
from . import chat_jobs, dashboard, github_review, templates


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
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            if not self._authed():
                self._send(200, templates.LOGIN_PAGE.format(error=""))
            else:
                self._send(200, templates.CHAT_PAGE.format())
        elif path == "/dashboard":
            if not self._authed():
                self._send(200, templates.LOGIN_PAGE.format(error=""))
            else:
                self._send(200, templates.DASHBOARD_PAGE)
        elif path == "/api/stats":
            self._handle_stats()
        elif path == "/api/sessions":
            self._handle_list_sessions()
        elif path == "/api/sessions/messages":
            self._handle_session_messages(parse_qs(urlparse(self.path).query))
        elif path == "/api/chat/status":
            self._handle_chat_status(parse_qs(urlparse(self.path).query))
        else:
            self._send(404, "not found")

    def _handle_list_sessions(self):
        token = self._authed()
        if not token:
            self._send_json(401, {"error": "not authenticated"})
            return
        current = storage.load_sessions().get(token, {}).get("claude_session_id")
        self._send_json(200, {"sessions": transcripts.list_sessions(), "current": current})

    def _handle_session_messages(self, query):
        if not self._authed():
            self._send_json(401, {"error": "not authenticated"})
            return
        session_id = (query.get("id") or [""])[0]
        messages = transcripts.load_messages(session_id) if session_id else None
        if messages is None:
            self._send_json(404, {"error": "session not found"})
            return
        self._send_json(200, {"messages": messages})

    def _handle_stats(self):
        if not self._authed():
            self._send_json(401, {"error": "not authenticated"})
            return
        cfg = storage.load_config()
        data = dashboard.load_history()
        history = data.get("history", [])
        totals = history[-1] if history else dashboard.latest_totals(settings.DAEMON_USER)
        window = dashboard.window_totals(history, settings.USAGE_WINDOW_SECS)
        # 24h of 5-min samples, so the 5h window marker sits inside a wider view.
        chart_points = max(1, settings.USAGE_WINDOW_SECS // dashboard.COLLECT_INTERVAL_SECS) * 5 + 1
        self._send_json(200, {
            "tokens": {
                "input_tokens": totals.get("input_tokens", 0),
                "output_tokens": totals.get("output_tokens", 0),
                "cache_creation_tokens": totals.get("cache_creation_tokens", 0),
                "cache_read_tokens": totals.get("cache_read_tokens", 0),
                "window_input_tokens": window.get("input_tokens", 0),
                "window_output_tokens": window.get("output_tokens", 0),
                "window_secs": settings.USAGE_WINDOW_SECS,
                "limit": cfg.get("token_limit"),
            },
            "history": history[-chart_points:],
            "window_start_ts": dashboard.window_start_ts(history, settings.USAGE_WINDOW_SECS),
            "last_collected": history[-1]["ts"] if history else None,
            "system": dashboard.system_stats(),
        })

    def do_POST(self):
        if self.path == "/login":
            self._handle_login()
        elif self.path == "/api/chat":
            self._handle_chat()
        elif self.path == "/api/sessions/resume":
            self._handle_resume_session()
        elif self.path == "/api/sessions/new":
            self._handle_new_session()
        else:
            self._send(404, "not found")

    def _handle_resume_session(self):
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
        session_id = (data.get("session_id") or "").strip()
        messages = transcripts.load_messages(session_id) if session_id else None
        if messages is None:
            self._send_json(404, {"error": "session not found"})
            return
        sessions = storage.load_sessions()
        sessions.setdefault(token, {})["claude_session_id"] = session_id
        storage.save_sessions(sessions)
        self._send_json(200, {"messages": messages})

    def _handle_new_session(self):
        token = self._authed()
        if not token:
            self._send_json(401, {"error": "not authenticated"})
            return
        sessions = storage.load_sessions()
        sessions.setdefault(token, {})["claude_session_id"] = None
        storage.save_sessions(sessions)
        self._send_json(200, {"ok": True})

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

        # SCRUM-13: a github.com PR/MR URL anywhere in the message is treated
        # as "review this PR" -- fetch its diff, review with code-review-skill,
        # post the result as a PR comment (see web/github_review.py for the
        # detection rule and why it's kept this simple). Routed through the
        # same async job store as a normal chat turn since the review pass
        # (diff fetch + a full `claude -p` turn) can outlast a proxy timeout.
        # Gated by config.yaml's github_review flag (settings.GITHUB_REVIEW)
        # -- when off, a pasted PR link just goes through as a normal chat
        # message instead. This flag only controls whether the feature
        # triggers at all; live posting vs. dry-run is the separate
        # settings.GITHUB_REVIEW_COMMENT switch, untouched by it.
        pr_request = settings.GITHUB_REVIEW and github_review.detect_pr_request(message)
        if pr_request:
            owner, repo, pr_number, url = pr_request
            job_id = chat_jobs.start_github_review_job(token, owner, repo, pr_number, url)
            self._send_json(200, {"job_id": job_id})
            return

        timeout_secs = settings.TIMEOUT_TIERS.get(
            data.get("timeout"), settings.TIMEOUT_TIERS[settings.DEFAULT_TIMEOUT_TIER])

        sessions = storage.load_sessions()
        session_entry = sessions.get(token, {})
        job_id = chat_jobs.start_job(
            token, message, session_entry.get("claude_session_id"),
            session_entry.get("claude_session_owner"), timeout_secs)
        # Returns immediately with a job id instead of blocking on the reply --
        # see chat_jobs.py for why (proxy timeouts well under the longer
        # TIMEOUT_TIERS). The page polls /api/chat/status for the result.
        self._send_json(200, {"job_id": job_id})

    def _handle_chat_status(self, query):
        token = self._authed()
        if not token:
            self._send_json(401, {"error": "not authenticated"})
            return
        job_id = (query.get("job_id") or [""])[0]
        job = chat_jobs.get_job(job_id, token)
        if job is None:
            self._send_json(404, {"error": "job not found"})
            return
        if job["status"] == "running":
            self._send_json(200, {"status": "running"})
        elif job["status"] == "error":
            self._send_json(200, {"status": "error", "error": job["error"]})
        else:
            self._send_json(200, {"status": "done", "reply": job["reply"]})


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
