"""Accounts and bearer-token auth: short-lived JWT access tokens plus rotating refresh tokens."""
import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import closing
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from web.core import DB_PATH, db, lock, logger

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Bump when the privacy policy changes: readers must accept the new version before continuing
CONSENT_VERSION = "2026-10-19"
ACCESS_TTL = 15 * 60
REFRESH_TTL = 30 * 24 * 60 * 60
JWT_ALGORITHM = "HS256"
JWT_SECRET = os.getenv("JWT_SECRET") or ""
if not JWT_SECRET:
    JWT_SECRET = secrets.token_urlsafe(48)
    logger.warning("JWT_SECRET is not set: using a random one, so every sign-in ends when the server restarts")

LANGUAGES = {"en", "ru", "de", "fr", "es", "it", "pt", "uk", "kk", "zh", "ja", "tr"}
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

with lock:
    db.conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            name TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            consent_version TEXT,
            consent_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            jti TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            family TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS refresh_tokens_account ON refresh_tokens (account_id);
        """
    )
    db.conn.commit()


def user_id_of(account_id: int) -> int:
    """Recommender/RAG user id for an account: negative, so it never collides with Telegram ids."""
    return -account_id


# ---------- passwords ----------

SCRYPT = {"n": 2**14, "r": 8, "p": 1}
PBKDF2_ITERATIONS = 600_000  # fallback where Python's OpenSSL lacks scrypt (e.g. LibreSSL builds)
b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    if hasattr(hashlib, "scrypt"):
        digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
        return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${b64(salt)}${b64(digest)}"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, *params = stored.split("$")
        if algorithm == "scrypt":
            n, r, p, salt, digest = params
            expected = base64.b64decode(digest)
            actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), dklen=len(expected), n=int(n), r=int(r), p=int(p))
        elif algorithm == "pbkdf2_sha256":
            iterations, salt, digest = params
            expected = base64.b64decode(digest)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        else:
            return False
    except Exception:
        return False
    return hmac.compare_digest(actual, expected)


# ---------- tokens ----------

def issue_tokens(account, family: Optional[str] = None) -> dict:
    now = int(time.time())
    access = jwt.encode(
        {"sub": str(account["id"]), "role": account["role"], "type": "access", "iat": now, "exp": now + ACCESS_TTL},
        JWT_SECRET, algorithm=JWT_ALGORITHM,
    )
    jti = secrets.token_urlsafe(24)
    family = family or secrets.token_urlsafe(12)
    refresh = jwt.encode(
        {"sub": str(account["id"]), "type": "refresh", "jti": jti, "fam": family, "iat": now, "exp": now + REFRESH_TTL},
        JWT_SECRET, algorithm=JWT_ALGORITHM,
    )
    with lock:
        db.conn.execute(
            "INSERT INTO refresh_tokens (jti, account_id, family, expires_at) VALUES (?, ?, ?, ?)",
            (jti, account["id"], family, now + REFRESH_TTL),
        )
        db.conn.commit()
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": ACCESS_TTL,
    }


def decode(token: str, expected_type: str) -> dict:
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise unauthorized("Session expired")
    except jwt.InvalidTokenError:
        raise unauthorized("Invalid token")
    if claims.get("type") != expected_type:
        raise unauthorized("Invalid token")
    return claims


def unauthorized(detail="Sign in to continue"):
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


ACCOUNT_COLUMNS = ["id", "email", "password_hash", "name", "role", "consent_version", "consent_at", "created_at", "last_seen"]


def get_account(account_id=None, email=None):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        if account_id is not None:
            row = conn.execute(f"SELECT {', '.join(ACCOUNT_COLUMNS)} FROM accounts WHERE id = ?", (account_id,)).fetchone()
        else:
            row = conn.execute(f"SELECT {', '.join(ACCOUNT_COLUMNS)} FROM accounts WHERE email = ?", (email.strip(),)).fetchone()
    return dict(zip(ACCOUNT_COLUMNS, row)) if row else None


def public_account(account) -> dict:
    return {
        "id": account["id"],
        "email": account["email"],
        "name": account["name"],
        "role": account["role"],
        "consent_version": account["consent_version"],
        "consent_at": account["consent_at"],
        "consent_required": account["consent_version"] != CONSENT_VERSION,
        "created_at": account["created_at"],
    }


# ---------- dependencies ----------

bearer = HTTPBearer(auto_error=False)
_last_seen = {}


def current_account(creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> dict:
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized()
    claims = decode(creds.credentials, "access")
    account = get_account(int(claims["sub"]))
    if account is None:
        raise unauthorized("Account no longer exists")
    now = time.monotonic()
    if now - _last_seen.get(account["id"], -1e9) > 60:  # record activity at most once a minute
        _last_seen[account["id"]] = now
        with lock:
            db.conn.execute("UPDATE accounts SET last_seen = CURRENT_TIMESTAMP WHERE id = ?", (account["id"],))
            db.conn.commit()
    return account


def current_reader(account: dict = Depends(current_account)) -> dict:
    """A signed-in reader who has accepted the current privacy policy."""
    if account["consent_version"] != CONSENT_VERSION:
        raise HTTPException(403, "consent_required")
    return account


def current_admin(account: dict = Depends(current_account)) -> dict:
    if account["role"] != "admin":
        raise HTTPException(403, "Admins only")
    return account


# ---------- sign-in throttling ----------

_failures = {}
MAX_FAILURES, WINDOW = 5, 15 * 60


def check_throttle(key):
    recent = [t for t in _failures.get(key, []) if t > time.time() - WINDOW]
    _failures[key] = recent
    if len(recent) >= MAX_FAILURES:
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")


def record_failure(key):
    _failures.setdefault(key, []).append(time.time())


def authenticate(email: str, password: str, request: Request) -> dict:
    key = f"{request.client.host if request.client else '-'}:{email.lower().strip()}"
    check_throttle(key)
    account = get_account(email=email)
    if account is None or not verify_password(password, account["password_hash"]):
        record_failure(key)
        raise HTTPException(401, "Wrong email or password")
    _failures.pop(key, None)
    return account


# ---------- endpoints ----------

class RegisterIn(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    name: Optional[str] = Field(default=None, max_length=60)
    age: int = Field(ge=5, le=110)
    language: str = Field(max_length=10)
    consent: bool
    consent_version: str


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class RefreshIn(BaseModel):
    refresh_token: str


@router.get("/consent")
def consent_info():
    return {"version": CONSENT_VERSION, "url": "/privacy"}


@router.post("/register", status_code=201)
def register(body: RegisterIn):
    if not body.consent or body.consent_version != CONSENT_VERSION:
        raise HTTPException(422, "You need to accept the current privacy policy to create an account")
    language = body.language.lower()
    if language not in LANGUAGES:
        raise HTTPException(422, "Unsupported language")
    email = body.email.strip()
    if get_account(email=email):
        raise HTTPException(409, "An account with this email already exists")
    with lock:
        cur = db.conn.execute(
            "INSERT INTO accounts (email, password_hash, name, consent_version, consent_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (email, hash_password(body.password), (body.name or "").strip() or None, CONSENT_VERSION),
        )
        db.conn.commit()
        account_id = cur.lastrowid
        db.save_user_profile(user_id_of(account_id), language=language, age=body.age)
    account = get_account(account_id)
    return {**issue_tokens(account), "account": public_account(account)}


@router.post("/login")
def login(body: LoginIn, request: Request):
    account = authenticate(body.email, body.password, request)
    return {**issue_tokens(account), "account": public_account(account)}


@router.post("/token", include_in_schema=True, summary="OAuth2 password flow (for the API docs' Authorize button)")
def token(request: Request, form: OAuth2PasswordRequestForm = Depends()):
    account = authenticate(form.username, form.password, request)
    tokens = issue_tokens(account)
    return {"access_token": tokens["access_token"], "token_type": "bearer"}


@router.post("/refresh")
def refresh(body: RefreshIn):
    claims = decode(body.refresh_token, "refresh")
    with lock:
        row = db.conn.execute(
            "SELECT account_id, family, revoked, expires_at FROM refresh_tokens WHERE jti = ?", (claims["jti"],)
        ).fetchone()
        if row is None:
            raise unauthorized("Invalid token")
        account_id, family, revoked, expires_at = row
        if revoked:
            # A rotated token came back: someone may have stolen it, so end that whole session
            db.conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE family = ?", (family,))
            db.conn.commit()
            logger.warning(f"Refresh token reuse for account {account_id}; session revoked")
            raise unauthorized("Session revoked, please sign in again")
        db.conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE jti = ?", (claims["jti"],))
        db.conn.execute("DELETE FROM refresh_tokens WHERE expires_at < ?", (int(time.time()),))
        db.conn.commit()
    account = get_account(account_id)
    if account is None:
        raise unauthorized("Account no longer exists")
    return {**issue_tokens(account, family=family), "account": public_account(account)}


@router.post("/logout")
def logout(body: RefreshIn):
    try:
        claims = jwt.decode(body.refresh_token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"verify_exp": False})
    except jwt.InvalidTokenError:
        return {"ok": True}
    with lock:
        db.conn.execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE family = (SELECT family FROM refresh_tokens WHERE jti = ?)",
            (claims.get("jti"),),
        )
        db.conn.commit()
    return {"ok": True}


def revoke_all(account_id):
    with lock:
        db.conn.execute("UPDATE refresh_tokens SET revoked = 1 WHERE account_id = ?", (account_id,))
        db.conn.commit()


def ensure_admin():
    """Creates or updates the admin account from ADMIN_EMAIL / ADMIN_PASSWORD."""
    email, password = os.getenv("ADMIN_EMAIL"), os.getenv("ADMIN_PASSWORD")
    if not email or not password:
        logger.warning("ADMIN_EMAIL / ADMIN_PASSWORD not set: no admin account is provisioned")
        return
    account = get_account(email=email)
    with lock:
        if account is None:
            db.conn.execute(
                "INSERT INTO accounts (email, password_hash, name, role) VALUES (?, ?, 'Admin', 'admin')",
                (email, hash_password(password)),
            )
        else:
            if not verify_password(password, account["password_hash"]):
                db.conn.execute("UPDATE accounts SET password_hash = ? WHERE id = ?", (hash_password(password), account["id"]))
                revoke_all(account["id"])
            db.conn.execute("UPDATE accounts SET role = 'admin' WHERE id = ?", (account["id"],))
        db.conn.commit()
