"""Веб-интерфейс Books Advisor: FastAPI поверх тех же модулей, что и Telegram-бот."""
import logging
import math
import os
import secrets
import sqlite3
import sys
import threading
import traceback
import warnings
from contextlib import closing
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cfg import API_KEY, CSV_PATH, DB_PATH  # noqa: E402
from db import Database  # noqa: E402
from recsys import RecommendationSystem  # noqa: E402

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
UID_COOKIE = "uid"
AGE_BUCKETS = [(8, 17), (18, 27), (28, 37), (38, 47), (48, 57), (58, 67), (68, 77), (78, 87), (88, 97)]

# Общее sqlite-соединение и DataFrame'ы меняются под одним замком; чтение каталога и статистики идёт без него
lock = threading.RLock()
db = Database(DB_PATH)
recommender = RecommendationSystem(db, CSV_PATH)
rag = None
chat_status = {"state": "loading", "error": None}


def load_rag():
    """Загружает RAG в фоне: модели и FAISS-индекс грузятся долго, а каталог и рекомендации работают без них"""
    global rag
    if not API_KEY or API_KEY == "...":
        chat_status.update(state="unavailable", error="Не задан API_KEY для GigaChat")
        return
    try:
        from rag import RAGSystem

        instance = RAGSystem(CSV_PATH, API_KEY, db)
        with lock:
            rag = instance
        chat_status.update(state="ready", error=None)
        logger.info("RAG загружен")
    except Exception as e:
        logger.error(f"Не удалось загрузить RAG: {e}\n{traceback.format_exc()}")
        chat_status.update(state="unavailable", error=str(e))


app = FastAPI(title="Books Advisor")


@app.on_event("startup")
def startup():
    threading.Thread(target=load_rag, daemon=True).start()


def current_user(request: Request, response: Response) -> int:
    """Веб-пользователи получают отрицательные id, чтобы не пересекаться с id из Telegram"""
    raw = request.cookies.get(UID_COOKIE)
    if raw and raw.lstrip("-").isdigit() and int(raw) < 0:
        return int(raw)
    user_id = -secrets.randbelow(10**12) - 1
    response.set_cookie(UID_COOKIE, str(user_id), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return user_id


def require_age(user_id):
    if db.get_user_metadata(user_id).get("age") is None:
        raise HTTPException(409, "Сначала укажите возраст в профиле")


def require_rag():
    if rag is None:
        raise HTTPException(503, chat_status["error"] or "ИИ-чат ещё загружается, попробуйте через минуту")
    return rag


def clean(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def book_out(book):
    return {
        "id": clean(book.get("id")),
        "title": clean(book.get("Title")),
        "author": clean(book.get("Author")),
        "genre": clean(book.get("Genre")),
        "description": clean(book.get("Description")),
        "rating": clean(book.get("Rating")),
        "likes": clean(book.get("like_count")),
        "views": clean(book.get("view_count")),
    }


def bump_counts(book_ids, column):
    """Счётчики в CSV без RAG (RAG держит свою копию DataFrame и обновляет её сам)"""
    df = pd.read_csv(CSV_PATH)
    mask = df["id"].isin(book_ids)
    df.loc[mask, column] = df.loc[mask, column].fillna(0) + 1
    df.to_csv(CSV_PATH, index=False)


# ---------- профиль ----------

class ProfileIn(BaseModel):
    age: int = Field(ge=5, le=110)
    language: Optional[str] = Field(default=None, max_length=10)


@app.get("/api/me")
def get_me(user_id: int = Depends(current_user)):
    with lock:
        meta = db.get_user_metadata(user_id)
    return {"user_id": user_id, **meta, "chat": chat_status}


@app.put("/api/me")
def put_me(body: ProfileIn, user_id: int = Depends(current_user)):
    with lock:
        language = body.language or db.get_user_metadata(user_id).get("language")
        db.save_user_profile(user_id, language=language, age=body.age)
    return {"user_id": user_id, "age": body.age, "language": language}


@app.get("/api/health")
def health():
    return {"ok": True, "chat": chat_status}


# ---------- чат (RAG + GigaChat) ----------

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def update_profile_task(user_id):
    with lock:
        if rag is not None:
            rag.update_profile_embedding(user_id)


@app.post("/api/chat")
def chat(body: ChatIn, tasks: BackgroundTasks, user_id: int = Depends(current_user)):
    require_age(user_id)
    engine = require_rag()
    with lock:
        try:
            reply, books = engine.get_recommendation(user_id, body.message.strip())
        except Exception as e:
            logger.error(f"Ошибка чата для {user_id}: {e}\n{traceback.format_exc()}")
            raise HTTPException(502, "Не удалось получить ответ от модели. Попробуйте ещё раз.")
        set_id = db.save_recommendation_set(user_id, [int(b["id"]) for b in books])
    tasks.add_task(update_profile_task, user_id)
    return {"reply": reply, "books": [book_out(b) for b in books], "set_id": set_id}


@app.post("/api/chat/reset")
def chat_reset(user_id: int = Depends(current_user)):
    with lock:
        if rag is not None:
            rag.clear_history(user_id)
    return {"ok": True}


# ---------- персональные рекомендации ----------

@app.get("/api/recommendations")
def recommendations(user_id: int = Depends(current_user)):
    require_age(user_id)
    with lock:
        recs = recommender.recommend(user_id)
        books = [book for book, _ in recs]
        set_id = db.save_recommendation_set(user_id, [int(book_id) for _, book_id in recs])
    return {"books": [book_out(b) for b in books], "set_id": set_id}


class FeedbackIn(BaseModel):
    set_id: int
    action: str = Field(pattern="^(like|dislike)$")


@app.post("/api/feedback")
def feedback(body: FeedbackIn, tasks: BackgroundTasks, user_id: int = Depends(current_user)):
    with lock:
        row = db.conn.execute(
            "SELECT book_ids FROM recommendation_sets WHERE set_id = ? AND user_id = ?",
            (body.set_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Подборка не найдена")
        book_ids = [int(b) for b in row[0].split(",") if b]
        for book_id in book_ids:
            db.add_interaction(user_id, book_id, body.action)
        if rag is not None:
            (rag.update_like_count if body.action == "like" else rag.update_dislike_count)(book_ids)
        else:
            bump_counts(book_ids, "like_count" if body.action == "like" else "dislike_count")
    tasks.add_task(update_profile_task, user_id)
    return {"ok": True}


# ---------- каталог ----------

SORTS = {"title": ("Title", True), "rating": ("Rating", False), "likes": ("like_count", False), "views": ("view_count", False)}


@app.get("/api/books")
def list_books(q: str = "", genre: str = "", sort: str = "title", offset: int = 0, limit: int = 50):
    limit = max(1, min(limit, 200))
    df = recommender.load_books()
    if q:
        needle = q.strip().lower()
        hay = df["Title"].astype(str).str.lower() + " " + df["Author"].astype(str).str.lower()
        df = df[hay.str.contains(needle, regex=False)]
    if genre:
        df = df[df["Genre"].astype(str).str.lower().str.contains(genre.lower(), regex=False)]
    column, ascending = SORTS.get(sort, SORTS["title"])
    if column in df:
        df = df.sort_values(column, ascending=ascending, na_position="last")
    page = df.iloc[max(offset, 0): max(offset, 0) + limit]
    return {"total": int(len(df)), "books": [book_out(r) for r in page.to_dict(orient="records")]}


def genre_counts(df):
    genres = df["Genre"].dropna().astype(str).str.split(",").explode().str.strip()
    return genres[genres != ""].value_counts()


@app.get("/api/genres")
def genres(limit: int = 12):
    df = recommender.load_books()
    return [{"name": name, "count": int(n)} for name, n in genre_counts(df).head(limit).items()]


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    author: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    genre: str = Field(min_length=1, max_length=200)
    rating: Optional[float] = Field(default=None, ge=0, le=5)


@app.post("/api/books", status_code=201)
def add_book(body: BookIn):
    engine = require_rag()
    new_book = {
        "Title": body.title.strip(),
        "Author": body.author.strip(),
        "Description": body.description.strip(),
        "Genre": body.genre.strip(),
        "Rating": body.rating or 0.0,
        "like_count": 0,
        "view_count": 0,
        "dislike_count": 0,
    }
    new_book["Combined"] = (
        f"{new_book['Title']} by {new_book['Author']}. Genre: {new_book['Genre']}. "
        f"Description: {new_book['Description']}"
    )
    with lock:
        new_id, embedding = engine.add_book(new_book)
        recommender.add_book(int(new_id), embedding)
    return book_out({**new_book, "id": int(new_id)})


# ---------- статистика ----------

@app.get("/api/stats")
def stats():
    # Только чтение: своё соединение, чтобы статистика не ждала долгий запрос к GigaChat под замком
    df = recommender.load_books()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        users = cur.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]
        actions = dict(cur.execute("SELECT action, COUNT(*) FROM user_interactions GROUP BY action").fetchall())
        sets = cur.execute("SELECT COUNT(*) FROM recommendation_sets").fetchone()[0]
        ages = [row[0] for row in cur.execute("SELECT age FROM user_profiles WHERE age IS NOT NULL")]

    top = df.sort_values(["like_count", "view_count"], ascending=False).head(8)
    return {
        "kpis": {
            "books": int(len(df)),
            "users": users,
            "sets": sets,
            "likes": int(actions.get("like", 0)),
            "dislikes": int(actions.get("dislike", 0)),
            "views": int(actions.get("view", 0)),
        },
        "genres": [{"name": name, "count": int(n)} for name, n in genre_counts(df).head(8).items()],
        "ages": [
            {"name": f"{lo}–{hi}", "count": sum(lo <= a <= hi for a in ages)} for lo, hi in AGE_BUCKETS
        ],
        "top_books": [book_out(r) for r in top.to_dict(orient="records")],
    }


# ---------- статика ----------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
