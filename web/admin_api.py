"""API for the admin console. Enabled only when ADMIN_PASSWORD is set."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from web.core import (
    AGE_BUCKETS, DB_PATH, Settings, book_out, engine, genre_counts, load_settings, query_books,
    recommender, save_settings,
)

router = APIRouter(prefix="/api/admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
# Without SESSION_SECRET, admin sessions simply end when the server restarts
SECRET = (os.getenv("SESSION_SECRET") or secrets.token_hex(32)).encode()
COOKIE = "admin_session"
SESSION_TTL = 12 * 60 * 60


def sign(expires: int) -> str:
    return hmac.new(SECRET, str(expires).encode(), hashlib.sha256).hexdigest()


def session_valid(token) -> bool:
    if not token or "." not in token:
        return False
    expires, sig = token.split(".", 1)
    return expires.isdigit() and int(expires) > time.time() and hmac.compare_digest(sig, sign(int(expires)))


def require_admin(request: Request):
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "Admin console is disabled: set the ADMIN_PASSWORD environment variable")
    if not session_valid(request.cookies.get(COOKIE)):
        raise HTTPException(401, "Sign in to continue")


def ro():
    """Separate read-only connection, so admin queries never wait for a slow chat request."""
    return closing(sqlite3.connect(DB_PATH))


# ---------- session ----------

class LoginIn(BaseModel):
    password: str


@router.get("/session")
def session(request: Request):
    return {"enabled": bool(ADMIN_PASSWORD), "signed_in": bool(ADMIN_PASSWORD) and session_valid(request.cookies.get(COOKIE))}


@router.post("/login")
def login(body: LoginIn, response: Response):
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "Admin console is disabled: set the ADMIN_PASSWORD environment variable")
    if not hmac.compare_digest(body.password.encode(), ADMIN_PASSWORD.encode()):
        time.sleep(1)  # slows down password guessing
        raise HTTPException(401, "Wrong password")
    expires = int(time.time()) + SESSION_TTL
    response.set_cookie(COOKIE, f"{expires}.{sign(expires)}", max_age=SESSION_TTL, httponly=True, samesite="strict")
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


# ---------- users ----------

USERS_SQL = """
WITH ids AS (SELECT user_id FROM user_profiles UNION SELECT user_id FROM web_users)
SELECT
    ids.user_id, w.name, p.age, p.language, w.created_at,
    MAX(COALESCE(w.last_seen, ''),
        COALESCE((SELECT MAX(timestamp) FROM recommendation_sets s WHERE s.user_id = ids.user_id), '')) AS last_active,
    (SELECT COUNT(*) FROM recommendation_sets s WHERE s.user_id = ids.user_id) AS sets,
    (SELECT COUNT(*) FROM user_interactions i WHERE i.user_id = ids.user_id AND i.action = 'like') AS likes,
    (SELECT COUNT(*) FROM user_interactions i WHERE i.user_id = ids.user_id AND i.action = 'dislike') AS dislikes,
    (SELECT COUNT(*) FROM user_interactions i WHERE i.user_id = ids.user_id AND i.action = 'view') AS views
FROM ids
LEFT JOIN user_profiles p ON p.user_id = ids.user_id
LEFT JOIN web_users w ON w.user_id = ids.user_id
"""
USER_COLUMNS = ["id", "name", "age", "language", "created_at", "last_active", "sets", "likes", "dislikes", "views"]


def fetch_users(where="", params=()):
    with ro() as conn:
        rows = conn.execute(USERS_SQL + where, params).fetchall()
    users = []
    for row in rows:
        user = dict(zip(USER_COLUMNS, row))
        user["source"] = "web" if user["id"] < 0 else "telegram"
        user["last_active"] = user["last_active"] or None
        users.append(user)
    return users


def days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")


@router.get("/users", dependencies=[Depends(require_admin)])
def users(q: str = "", source: str = "all", sort: str = "last_active"):
    result = fetch_users()
    if source in ("web", "telegram"):
        result = [u for u in result if u["source"] == source]
    if q:
        needle = q.lower().strip()
        result = [u for u in result if needle in (u["name"] or "").lower() or needle in str(u["id"])]
    if sort not in ("last_active", "sets", "likes", "dislikes", "age", "name"):
        sort = "last_active"
    if sort == "name":
        result.sort(key=lambda u: (u["name"] is None, (u["name"] or "").lower()))
    else:
        result.sort(key=lambda u: (u[sort] is not None, u[sort] or 0), reverse=True)
    return {"total": len(result), "users": result[:500]}


@router.get("/users/{user_id}", dependencies=[Depends(require_admin)])
def user_detail(user_id: int):
    found = fetch_users(" WHERE ids.user_id = ?", (user_id,))
    if not found:
        raise HTTPException(404, "User not found")
    titles = recommender.load_books().set_index("id")["Title"].to_dict()
    with ro() as conn:
        sets = conn.execute(
            "SELECT set_id, book_ids, timestamp FROM recommendation_sets WHERE user_id = ? ORDER BY set_id DESC LIMIT 10",
            (user_id,),
        ).fetchall()
        reactions = conn.execute(
            "SELECT book_id, action FROM user_interactions WHERE user_id = ? AND action IN ('like', 'dislike') ORDER BY id DESC",
            (user_id,),
        ).fetchall()
    return {
        **found[0],
        "recent_sets": [
            {"id": sid, "at": at, "books": [titles.get(int(b), f"#{b}") for b in ids.split(",") if b]}
            for sid, ids, at in sets
        ],
        "liked": [titles.get(b, f"#{b}") for b, a in reactions if a == "like"][:30],
        "disliked": [titles.get(b, f"#{b}") for b, a in reactions if a == "dislike"][:30],
        "chat_messages": max(len(engine.rag.memory.get(user_id, [])) - 1, 0) if engine.rag else None,
    }


# ---------- overview ----------

@router.get("/overview", dependencies=[Depends(require_admin)])
def overview():
    all_users = fetch_users()
    df = recommender.load_books()
    with ro() as conn:
        per_day = dict(conn.execute(
            "SELECT date(timestamp), COUNT(*) FROM recommendation_sets WHERE timestamp >= ? GROUP BY 1",
            (days_ago(13)[:10],),
        ).fetchall())
        sets_total = conn.execute("SELECT COUNT(*) FROM recommendation_sets").fetchone()[0]

    likes = sum(u["likes"] for u in all_users)
    dislikes = sum(u["dislikes"] for u in all_users)
    week_ago = days_ago(7)
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=n)).isoformat() for n in range(13, -1, -1)]
    languages = {}
    for u in all_users:
        if u["language"]:
            languages[u["language"]] = languages.get(u["language"], 0) + 1
    ages = [u["age"] for u in all_users if u["age"] is not None]

    return {
        "kpis": {
            "users": len(all_users),
            "web_users": sum(u["source"] == "web" for u in all_users),
            "telegram_users": sum(u["source"] == "telegram" for u in all_users),
            "active_7d": sum((u["last_active"] or "") >= week_ago for u in all_users),
            "books": int(len(df)),
            "sets": sets_total,
            "likes": likes,
            "dislikes": dislikes,
            "like_rate": round(likes / (likes + dislikes), 3) if likes + dislikes else None,
        },
        "sets_per_day": [{"name": d, "count": per_day.get(d, 0)} for d in days],
        "genres": [{"name": name, "count": int(n)} for name, n in genre_counts(df).head(8).items()],
        "ages": [{"name": f"{lo}–{hi}", "count": sum(lo <= a <= hi for a in ages)} for lo, hi in AGE_BUCKETS],
        "languages": [{"name": k, "count": v} for k, v in sorted(languages.items(), key=lambda kv: -kv[1])][:8],
        "top_books": [
            book_out(r, full=True)
            for r in df.sort_values(["like_count", "view_count"], ascending=False).head(8).to_dict(orient="records")
        ],
        "engine": engine.status,
    }


# ---------- books ----------

@router.get("/books", dependencies=[Depends(require_admin)])
def books(q: str = "", genre: str = "", sort: str = "newest", offset: int = 0, limit: int = 50):
    limit = max(1, min(limit, 200))
    offset = max(offset, 0)
    df = query_books(q, genre, sort)
    page = df.iloc[offset: offset + limit]
    return {"total": int(len(df)), "books": [book_out(r, full=True) for r in page.to_dict(orient="records")]}


# ---------- settings ----------

@router.get("/settings", dependencies=[Depends(require_admin)])
def get_settings():
    return {"settings": load_settings().model_dump(), "defaults": Settings().model_dump(), "engine": engine.status}


@router.put("/settings", dependencies=[Depends(require_admin)])
def put_settings(body: Settings):
    save_settings(body)
    return {"settings": body.model_dump()}


@router.post("/settings/reset", dependencies=[Depends(require_admin)])
def reset_settings():
    save_settings(Settings())
    return {"settings": Settings().model_dump()}
