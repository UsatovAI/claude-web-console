"""Password check, session cookies, and login rate limiting."""
import hashlib
import hmac
import secrets
import time
from http.cookies import SimpleCookie

from . import settings, storage

_login_attempts = {}  # ip -> [timestamps of failed attempts]


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def check_password(password, cfg):
    candidate = hash_password(password, cfg["salt"])
    return hmac.compare_digest(candidate, cfg["password_hash"])


def new_session_token():
    return secrets.token_urlsafe(32)


def cookie_token(cookie_header):
    if not cookie_header:
        return None
    c = SimpleCookie()
    c.load(cookie_header)
    if "session" in c:
        return c["session"].value
    return None


def authed_token(cookie_header):
    """Return the session token if the cookie names a live session, else None."""
    token = cookie_token(cookie_header)
    if not token:
        return None
    sessions = storage.load_sessions()
    return token if token in sessions else None


def rate_limited(ip):
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < settings.LOGIN_WINDOW_SECS]
    _login_attempts[ip] = attempts
    return len(attempts) >= settings.LOGIN_MAX_ATTEMPTS


def record_failure(ip):
    _login_attempts.setdefault(ip, []).append(time.time())
