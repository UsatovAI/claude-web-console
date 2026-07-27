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
    """Return the session token if the cookie names a live, unexpired session,
    else None. Expiry is enforced here (not just via the cookie's own
    Max-Age) so a copied/replayed token stops working past its age even if
    the client that stole it ignores or strips Max-Age."""
    token = cookie_token(cookie_header)
    if not token:
        return None
    sessions = storage.load_sessions()
    session = sessions.get(token)
    if not session:
        return None
    if time.time() - session.get("created", 0) > settings.SESSION_MAX_AGE_SECS:
        del sessions[token]
        storage.save_sessions(sessions)
        return None
    return token


def rate_limited(ip):
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < settings.LOGIN_WINDOW_SECS]
    _login_attempts[ip] = attempts
    return len(attempts) >= settings.LOGIN_MAX_ATTEMPTS


def record_failure(ip):
    _login_attempts.setdefault(ip, []).append(time.time())
