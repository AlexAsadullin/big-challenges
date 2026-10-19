"""API for the reader-facing app."""
import secrets
import traceback
from typing import Optional

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from web.core import CSV_PATH, book_out, db, engine, genre_counts, lock, logger, recommender, touch

router = APIRouter(prefix="/api")
UID_COOKIE = "uid"
LANGUAGES = {"en", "ru", "de", "fr", "es", "it", "pt", "uk", "kk", "zh", "ja", "tr"}


def current_user(request: Request, response: Response) -> int:
    """Web users get negative ids so they never collide with Telegram ids."""
    raw = request.cookies.get(UID_COOKIE)
    if raw and raw.lstrip("-").isdigit() and int(raw) < 0:
        user_id = int(raw)
    else:
        user_id = -secrets.randbelow(10**12) - 1
        response.set_cookie(UID_COOKIE, str(user_id), max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    touch(user_id)
    return user_id


def require_age(user_id):
    if db.get_user_metadata(user_id).get("age") is None:
        raise HTTPException(409, "Add your age in the profile first")


def require_rag():
    if engine.rag is None:
        raise HTTPException(503, engine.status["error"] or "The advisor is still loading, try again in a minute")
    return engine.rag


def get_name(user_id):
    row = db.conn.execute("SELECT name FROM web_users WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else None


# ---------- profile ----------

class ProfileIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=60)
    age: int = Field(ge=5, le=110)
    language: str = Field(max_length=10)


@router.get("/me")
def get_me(user_id: int = Depends(current_user)):
    with lock:
        meta = db.get_user_metadata(user_id)
        name = get_name(user_id)
    return {"user_id": user_id, "name": name, **meta, "chat": engine.status}


@router.put("/me")
def put_me(body: ProfileIn, user_id: int = Depends(current_user)):
    language = body.language.lower()
    if language not in LANGUAGES:
        raise HTTPException(422, "Unsupported language")
    name = (body.name or "").strip() or None
    with lock:
        db.save_user_profile(user_id, language=language, age=body.age)
        db.conn.execute(
            "INSERT INTO web_users (user_id, name) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET name = excluded.name",
            (user_id, name),
        )
        db.conn.commit()
    return {"user_id": user_id, "name": name, "age": body.age, "language": language}


@router.get("/health")
def health():
    return {"ok": True, "chat": engine.status}


# ---------- chat (RAG + GigaChat) ----------

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def update_profile_task(user_id):
    with lock:
        if engine.rag is not None:
            engine.rag.update_profile_embedding(user_id)


@router.post("/chat")
def chat(body: ChatIn, tasks: BackgroundTasks, user_id: int = Depends(current_user)):
    require_age(user_id)
    rag = require_rag()
    query = body.message.strip()
    # Same steps as RAGSystem.get_recommendation, split so the slow GigaChat call runs
    # outside the lock and doesn't stall every other reader's requests.
    try:
        with lock:
            books = rag.search_books(user_id, query, initial_k=100)
            books = rag.rerank_with_cross_encoder(query, books)
            for book in books:
                db.add_interaction(user_id, book["id"], "view")
            rag.build_prompt(user_id, query, books)
            messages = list(rag._get_or_create_memory(user_id))
            giga = rag.giga
        response = giga.invoke(messages)
    except Exception as e:
        logger.error(f"Chat error for {user_id}: {e}\n{traceback.format_exc()}")
        raise HTTPException(502, "The model didn't answer. Please try again.")
    with lock:
        rag.update_history(user_id, "assistant", response.content)
        reply = rag.clean_text(response.content)
        set_id = db.save_recommendation_set(user_id, [int(b["id"]) for b in books])
    tasks.add_task(update_profile_task, user_id)
    return {"reply": reply, "books": [book_out(b) for b in books], "set_id": set_id}


@router.post("/chat/reset")
def chat_reset(user_id: int = Depends(current_user)):
    with lock:
        if engine.rag is not None:
            engine.rag.clear_history(user_id)
    return {"ok": True}


# ---------- personal recommendations ----------

@router.get("/recommendations")
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


def bump_counts(book_ids, column):
    """Counter update without RAG (RAG keeps its own DataFrame copy and updates it itself)."""
    df = pd.read_csv(CSV_PATH)
    mask = df["id"].isin(book_ids)
    df.loc[mask, column] = df.loc[mask, column].fillna(0) + 1
    df.to_csv(CSV_PATH, index=False)


@router.post("/feedback")
def feedback(body: FeedbackIn, tasks: BackgroundTasks, user_id: int = Depends(current_user)):
    with lock:
        row = db.conn.execute(
            "SELECT book_ids FROM recommendation_sets WHERE set_id = ? AND user_id = ?",
            (body.set_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Recommendation set not found")
        book_ids = [int(b) for b in row[0].split(",") if b]
        for book_id in book_ids:
            db.add_interaction(user_id, book_id, body.action)
        rag = engine.rag
        if rag is not None:
            (rag.update_like_count if body.action == "like" else rag.update_dislike_count)(book_ids)
        else:
            bump_counts(book_ids, "like_count" if body.action == "like" else "dislike_count")
    tasks.add_task(update_profile_task, user_id)
    return {"ok": True}


# ---------- books ----------

@router.get("/genres")
def genres(limit: int = 12):
    df = recommender.load_books()
    return [{"name": name, "count": int(n)} for name, n in genre_counts(df).head(limit).items()]


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    author: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    genre: str = Field(min_length=1, max_length=200)
    rating: Optional[float] = Field(default=None, ge=0, le=5)


@router.post("/books", status_code=201)
def add_book(body: BookIn, user_id: int = Depends(current_user)):
    rag = require_rag()
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
        new_id, embedding = rag.add_book(new_book)
        recommender.add_book(int(new_id), embedding)
    return book_out({**new_book, "id": int(new_id)})
