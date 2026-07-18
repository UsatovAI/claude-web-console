#!/usr/bin/env python3
"""Password-gated chat front-end for a headless Claude Code daemon.

Entry point only. See:
  core/settings.py        -- config constants
  core/storage.py         -- config.json / sessions.json persistence
  core/auth.py            -- password check, cookies, login rate limiting
  daemon/claude_daemon.py -- runs `claude -p` as the daemon user
  daemon/night_mode.py    -- ~8h autonomous overnight run loop
  daemon/night_control.py -- web-side glue for the /night chat command
  web/templates.py        -- HTML pages
  web/server.py           -- HTTP handler + TLS server
  web/dashboard.py        -- token usage / host stats for /dashboard
  var/state, var/log      -- runtime state and logs (gitignored)
"""
import ssl
import threading
from http.server import ThreadingHTTPServer

from core import settings
from web import dashboard
from web.server import Handler, TLSThreadingHTTPServer


def main():
    stop_collector = threading.Event()
    collector_thread = threading.Thread(
        target=dashboard.collector_loop, args=(settings.DAEMON_USER, stop_collector), daemon=True)
    collector_thread.start()

    if settings.USE_TLS:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(settings.CERT_PATH, settings.KEY_PATH)
        server = TLSThreadingHTTPServer(("0.0.0.0", settings.PORT), Handler, ctx)
        print(f"Listening on https://0.0.0.0:{settings.PORT}")
    else:
        server = ThreadingHTTPServer(("0.0.0.0", settings.PORT), Handler)
        print(f"Listening on http://0.0.0.0:{settings.PORT}")
    try:
        server.serve_forever()
    finally:
        stop_collector.set()


if __name__ == "__main__":
    main()
