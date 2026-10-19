"""API for the reader app. Every reader endpoint needs a bearer access token and accepted consent."""
import traceback
from typing import Optional

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from web.auth import (
    CONSENT_VERSION, LANGUAGES, current_account, current_reader, get_account, public_account, revoke_all,
    user_id_of, verify_password,
)
from web.core import (
    CSV_PATH, book_link, book_out, db, engine, genre_counts, load_settings, lock, logger, prefetch_links, recommender,
)

router = APIRouter(prefix="/api", tags=["reader"])


def require_rag():
    if engine.rag is None:
        raise HTTPException(503, engine.status["error"] or "The advisor is still loading, try again in a minute")
    return engine.rag


@router.get("/health", tags=["service"])
def health():
    return {"ok": True, "chat": engine.status}


# ---------- account & profile ----------

class ProfileIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=60)
    age: int = Field(ge=5, le=110)
    language: str = Field(max_length=10)


class ConsentIn(BaseModel):
    version: str


class DeleteIn(BaseModel):
    password: str = Field(max_length=128)


def me_out(account):
    with lock:
        profile = db.get_user_metadata(user_id_of(account["id"]))
    return {**public_account(account), "age": profile["age"], "language": profile["language"], "chat": engine.status}


@router.get("/me")
def get_me(account: dict = Depends(current_account)):
    return me_out(account)


@router.put("/me")
def put_me(body: ProfileIn, account: dict = Depends(current_reader)):
    language = body.language.lower()
    if language not in LANGUAGES:
        raise HTTPException(422, "Unsupported language")
    with lock:
        db.save_user_profile(user_id_of(account["id"]), language=language, age=body.age)
        db.conn.execute("UPDATE accounts SET name = ? WHERE id = ?", ((body.name or "").strip() or None, account["id"]))
        db.conn.commit()
    return me_out(get_account(account["id"]))


@router.post("/me/consent")
def accept_consent(body: ConsentIn, account: dict = Depends(current_account)):
    if body.version != CONSENT_VERSION:
        raise HTTPException(409, "The privacy policy has changed, reload the page to see the current version")
    with lock:
        db.conn.execute(
            "UPDATE accounts SET consent_version = ?, consent_at = CURRENT_TIMESTAMP WHERE id = ?",
            (CONSENT_VERSION, account["id"]),
        )
        db.conn.commit()
    return me_out(get_account(account["id"]))


@router.get("/me/export")
def export_my_data(account: dict = Depends(current_account)):
    """Everything stored about the reader, as JSON (right of access)."""
    user_id = user_id_of(account["id"])
    titles = recommender.load_books().set_index("id")["Title"].to_dict()
    with lock:
        profile = db.get_user_metadata(user_id)
        interactions = db.conn.execute(
            "SELECT book_id, action, timestamp FROM user_interactions WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
        sets = db.conn.execute(
            "SELECT set_id, book_ids, timestamp FROM recommendation_sets WHERE user_id = ? ORDER BY set_id", (user_id,)
        ).fetchall()
        chat = [getattr(m, "content", str(m)) for m in (engine.rag.memory.get(user_id, [])[1:] if engine.rag else [])]
    return {
        "account": {k: v for k, v in public_account(account).items() if k != "consent_required"},
        "profile": profile,
        "reactions": [{"book": titles.get(b, b), "action": a, "at": t} for b, a, t in interactions],
        "recommendations": [
            {"at": t, "books": [titles.get(int(b), b) for b in ids.split(",") if b]} for _, ids, t in sets
        ],
        "current_chat": chat,
    }


@router.delete("/me")
def delete_account(body: DeleteIn, account: dict = Depends(current_account)):
    """Withdraws consent: removes the account and all personal data tied to it."""
    if not verify_password(body.password, account["password_hash"]):
        raise HTTPException(403, "Wrong password")
    user_id = user_id_of(account["id"])
    revoke_all(account["id"])
    with lock:
        for table in ("user_interactions", "recommendation_sets", "user_profiles"):
            db.conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        db.conn.execute("DELETE FROM refresh_tokens WHERE account_id = ?", (account["id"],))
        db.conn.execute("DELETE FROM accounts WHERE id = ?", (account["id"],))
        db.conn.commit()
        if engine.rag is not None:
            engine.rag.clear_history(user_id)
    logger.info(f"Account {account['id']} deleted at the reader's request")
    return {"ok": True}


# ---------- chat (RAG + GigaChat) ----------

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def update_profile_task(user_id):
    with lock:
        if engine.rag is not None:
            engine.rag.update_profile_embedding(user_id)


@router.post("/chat")
def chat(body: ChatIn, tasks: BackgroundTasks, account: dict = Depends(current_reader)):
    user_id = user_id_of(account["id"])
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
    tasks.add_task(prefetch_links, [b["id"] for b in books])
    return {"reply": reply, "books": [book_out(b) for b in books], "set_id": set_id}


@router.post("/chat/reset")
def chat_reset(account: dict = Depends(current_reader)):
    with lock:
        if engine.rag is not None:
            engine.rag.clear_history(user_id_of(account["id"]))
    return {"ok": True}


# ---------- personal recommendations ----------

def seen_books(user_id, recent_sets):
    """Books to skip in For You: anything in the last few sets, plus everything already rated."""
    rows = db.conn.execute(
        "SELECT book_ids FROM recommendation_sets WHERE user_id = ? ORDER BY set_id DESC LIMIT ?", (user_id, recent_sets)
    ).fetchall()
    seen = {int(b) for (ids,) in rows for b in ids.split(",") if b}
    seen.update(b for (b,) in db.conn.execute(
        "SELECT book_id FROM user_interactions WHERE user_id = ? AND action IN ('like', 'dislike')", (user_id,)
    ))
    return seen


@router.get("/recommendations")
def recommendations(tasks: BackgroundTasks, account: dict = Depends(current_reader)):
    user_id = user_id_of(account["id"])
    settings = load_settings().recsys
    with lock:
        if db.get_user_metadata(user_id).get("age") is None:
            raise HTTPException(409, "Add your age in the profile first")
        recs = recommender.recommend(
            user_id, exclude=seen_books(user_id, settings.skip_recent_sets), pool_size=settings.pool_size
        )
        books = [book for book, _ in recs]
        set_id = db.save_recommendation_set(user_id, [int(book_id) for _, book_id in recs])
    tasks.add_task(prefetch_links, [book_id for _, book_id in recs])
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
def feedback(body: FeedbackIn, tasks: BackgroundTasks, account: dict = Depends(current_reader)):
    user_id = user_id_of(account["id"])
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


@router.get("/books/{book_id}/open")
def open_book(book_id: int):
    """Public on purpose: books open in a new tab via a plain link, which can't carry a bearer token."""
    url = book_link(book_id)
    if url is None:
        raise HTTPException(404, "Book not found")
    return RedirectResponse(url, status_code=302)


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    author: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    genre: str = Field(min_length=1, max_length=200)
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    link: Optional[str] = Field(default=None, max_length=500, pattern=r"^(https?://\S+)?$")


@router.post("/books", status_code=201)
def add_book(body: BookIn, account: dict = Depends(current_reader)):
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
        "Link": (body.link or "").strip() or None,
    }
    new_book["Combined"] = (
        f"{new_book['Title']} by {new_book['Author']}. Genre: {new_book['Genre']}. "
        f"Description: {new_book['Description']}"
    )
    with lock:
        new_id, embedding = rag.add_book(new_book)
        recommender.add_book(int(new_id), embedding)
    return book_out({**new_book, "id": int(new_id)})
