import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import DATABASE_URL, _connections

"""登录注册。

用户名加密码，密码用 PBKDF2 加盐存。登录后发一个 HMAC 签名的令牌，里面带 user_id。
user_id 就是长期偏好按人分的那个键，登录之后不再由前端随便填。
没配数据库时用户存在内存里，重启就没了。
"""

SECRET = os.getenv("AUTH_SECRET", "").encode() or secrets.token_bytes(32)
TOKEN_TTL = 7 * 24 * 3600

_memory_users: dict[str, dict] = {}
_table_ready = False
_bearer = HTTPBearer(auto_error=False)


def _ensure_table():
    global _table_ready
    if _table_ready or not DATABASE_URL:
        return
    with _connections().connection() as conn:
        conn.execute(
            "create table if not exists users ("
            " id bigserial primary key,"
            " username text unique not null,"
            " password_hash text not null,"
            " created_at timestamptz not null default now())"
        )
    _table_ready = True


def _hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"{salt.hex()}${digest.hex()}"


def _verify(password: str, stored: str) -> bool:
    salt_hex, _ = stored.split("$", 1)
    return hmac.compare_digest(_hash(password, bytes.fromhex(salt_hex)), stored)


def _find(username: str) -> dict | None:
    if not DATABASE_URL:
        return _memory_users.get(username)
    _ensure_table()
    with _connections().connection() as conn:
        return conn.execute(
            "select id, username, password_hash from users where username = %s", (username,)
        ).fetchone()


def register(username: str, password: str) -> dict:
    username = username.strip()
    if not 2 <= len(username) <= 32:
        raise HTTPException(400, "用户名 2 到 32 个字")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if _find(username):
        raise HTTPException(409, "用户名已被占用")
    password_hash = _hash(password)
    if not DATABASE_URL:
        user = {"id": len(_memory_users) + 1, "username": username, "password_hash": password_hash}
        _memory_users[username] = user
        return user
    with _connections().connection() as conn:
        return conn.execute(
            "insert into users (username, password_hash) values (%s, %s) "
            "returning id, username, password_hash",
            (username, password_hash),
        ).fetchone()


def login(username: str, password: str) -> dict:
    user = _find(username.strip())
    if not user or not _verify(password, user["password_hash"]):
        raise HTTPException(401, "用户名或密码不对")
    return user


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user: dict) -> str:
    payload = _b64(json.dumps({
        "uid": f"user-{user['id']}",
        "name": user["username"],
        "exp": int(time.time()) + TOKEN_TTL,
    }).encode())
    sig = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def read_token(token: str) -> dict | None:
    try:
        payload, sig = token.split(".", 1)
        expected = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_unb64(payload))
        return data if data["exp"] > time.time() else None
    except (ValueError, KeyError):
        return None


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    """接口依赖：没登录或令牌过期就 401。返回 {uid, name}。"""
    data = read_token(cred.credentials) if cred else None
    if not data:
        raise HTTPException(401, "请先登录")
    return data
