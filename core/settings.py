"""Shared configuration constants for the console app and its background workers."""
import os

PKG_DIR = os.path.dirname(os.path.abspath(__file__))  # .../site/core
BASE_DIR = os.path.dirname(PKG_DIR)  # .../site (repo root)

VAR_DIR = os.path.join(BASE_DIR, "var")
STATE_DIR = os.path.join(VAR_DIR, "state")  # config, sessions, other runtime state
LOG_DIR = os.path.join(VAR_DIR, "log")

CONFIG_PATH = os.path.join(STATE_DIR, "config.json")
SESSIONS_PATH = os.path.join(STATE_DIR, "sessions.json")

CERT_DOMAIN = os.environ.get("CERT_DOMAIN", "144-124-226-50.sslip.io")
CERT_PATH = f"/etc/letsencrypt/live/{CERT_DOMAIN}/fullchain.pem"
KEY_PATH = f"/etc/letsencrypt/live/{CERT_DOMAIN}/privkey.pem"
USE_TLS = os.path.exists(CERT_PATH) and os.path.exists(KEY_PATH)
PORT = int(os.environ.get("PORT", "443" if USE_TLS else "8080"))

DAEMON_USER = os.environ.get("DAEMON_USER", "claudeweb")
ENV_FILE_PATH = os.environ.get("DAEMON_ENV_FILE", "/root/env/.env")

LOGIN_WINDOW_SECS = 300
LOGIN_MAX_ATTEMPTS = 5

TIMEOUT_TIERS = {"5m": 300, "15m": 900, "1h": 3600}
DEFAULT_TIMEOUT_TIER = "5m"

NIGHT_CRON_LOG = os.path.join(LOG_DIR, "night_mode_cron.log")
