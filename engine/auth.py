"""Local single-host accounts with two roles.

- passwords: PBKDF2-HMAC-SHA256, per-user salt, stored as algorithm$iterations$salt$hash
- sessions: random token in an HttpOnly cookie; only its SHA-256 is stored
- roles: editor (change data), approver (decide change requests), admin (both)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuthSession, User

COOKIE_NAME = "netcfg_session"
ITERATIONS = 210_000
ROLES = {"editor", "approver", "admin"}
SESSION_TTL_HOURS = int(os.environ.get("NETCFG_SESSION_HOURS", "12"))
LOGIN_MAX_FAILURES = int(os.environ.get("NETCFG_LOGIN_MAX_FAILURES", "5"))
LOGIN_WINDOW_SECONDS = int(os.environ.get("NETCFG_LOGIN_WINDOW_SECONDS", "300"))
WEAK_PASSWORDS = {"password", "password1", "12345678", "123456789", "qwertyui", "admin123", "11111111"}

_failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_failure_lock = threading.Lock()


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _normalise(username: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKC", username or "").strip()


def check_login_allowed(username: str, client: str) -> None:
    key = (_normalise(username).casefold(), client)
    now = time.time()
    with _failure_lock:
        bucket = _failures[key]
        while bucket and bucket[0] < now - LOGIN_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= LOGIN_MAX_FAILURES:
            raise AuthError("TOO_MANY_ATTEMPTS",
                            f"登录失败次数过多，请 {LOGIN_WINDOW_SECONDS // 60} 分钟后再试", 429)


def record_login_failure(username: str, client: str) -> None:
    key = (_normalise(username).casefold(), client)
    with _failure_lock:
        _failures[key].append(time.time())


def reset_login_failures(username: str, client: str) -> None:
    with _failure_lock:
        _failures.pop((_normalise(username).casefold(), client), None)


def reset_all_login_failures() -> None:
    """Tests only."""
    with _failure_lock:
        _failures.clear()


def hash_password(password: str) -> str:
    if not 10 <= len(password) <= 200:
        raise AuthError("WEAK_PASSWORD", "密码至少 10 位", 422)
    if password.casefold() in WEAK_PASSWORDS or len(set(password)) == 1:
        raise AuthError("WEAK_PASSWORD", "密码过于简单，请使用更长的随机密码", 422)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(candidate, digest)


def create_user(db: Session, username: str, role: str, password: str) -> User:
    username = _normalise(username)
    if not 2 <= len(username) <= 80:
        raise AuthError("INVALID_USERNAME", "用户名需要 2–80 个字符", 422)
    if role not in ROLES:
        raise AuthError("INVALID_ROLE", "角色只能是 editor / approver / admin", 422)
    if db.scalar(select(User).where(User.username == username)):
        raise AuthError("USER_EXISTS", "同名用户已存在", 409)
    user = User(username=username, role=role, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    username = _normalise(username)
    user = db.scalar(select(User).where(User.username == username))
    if user is None or user.disabled or not verify_password(password, user.password_hash):
        raise AuthError("INVALID_CREDENTIALS", "用户名或密码错误")
    return user


def start_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    session = AuthSession(id=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
                          expires_at=datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS))
    db.add(session)
    db.flush()
    return token


def resolve(db: Session, token: str | None) -> User:
    if not token:
        raise AuthError("AUTH_REQUIRED", "请先登录")
    row = db.get(AuthSession, hashlib.sha256(token.encode()).hexdigest())
    if row is None or row.revoked_at is not None:
        raise AuthError("AUTH_REQUIRED", "登录已失效，请重新登录")
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        raise AuthError("AUTH_EXPIRED", "登录已过期，请重新登录")
    user = db.get(User, row.user_id)
    if user is None or user.disabled:
        raise AuthError("AUTH_REQUIRED", "账号不可用")
    return user


def end_session(db: Session, token: str | None) -> None:
    if not token:
        return
    row = db.get(AuthSession, hashlib.sha256(token.encode()).hexdigest())
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)


def user_payload(user: User) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role,
            "created_at": user.created_at.isoformat()}
