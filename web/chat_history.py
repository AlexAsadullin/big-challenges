"""Saved advisor chats: every conversation is stored, so "New chat" no longer forgets the previous one.

rag.py keeps a single in-memory conversation per user. Before each message the web app rebuilds that memory from
the stored chat (see context_for), so the model sees the history of the chat the reader is in, and a restart
loses nothing.
"""
import json
import sqlite3
from contextlib import closing
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from web.auth import current_reader
from web.core import DB_PATH, db, engine, lock

router = APIRouter(prefix="/api/chats", tags=["chats"])

with lock:
    db.conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS chats_account ON chats (account_id, updated_at);
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            text TEXT NOT NULL,
            books TEXT,
            set_id INTEGER,
            feedback TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS chat_messages_chat ON chat_messages (chat_id, id);
        CREATE INDEX IF NOT EXISTS chat_messages_set ON chat_messages (set_id);
        """
    )
    db.conn.commit()

TITLE_LENGTH = 60


def read():
    return closing(sqlite3.connect(DB_PATH))


def make_title(text):
    text = " ".join(text.split())
    return text if len(text) <= TITLE_LENGTH else text[:TITLE_LENGTH].rsplit(" ", 1)[0] + "…"


def own_chat(conn, chat_id, account_id):
    row = conn.execute("SELECT id, title, created_at, updated_at FROM chats WHERE id = ? AND account_id = ?",
                       (chat_id, account_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Chat not found")
    return dict(zip(("id", "title", "created_at", "updated_at"), row))


# ---------- used by POST /api/chat ----------

def check_chat(account_id, chat_id):
    """404 unless the chat exists and belongs to the reader."""
    with read() as conn:
        own_chat(conn, chat_id, account_id)


def create_chat(account_id, first_message):
    """A new chat titled after its first message, created once the advisor has answered. Call under the lock."""
    cur = db.conn.execute("INSERT INTO chats (account_id, title) VALUES (?, ?)", (account_id, make_title(first_message)))
    return cur.lastrowid


def context_for(chat_id, rag):
    """Rebuilds rag's message list for a chat, the way rag.build_prompt and update_history would have built it:
    system prompt, then per turn the question, the books found for it, and the answer; trimmed to rag.max_messages."""
    rag_module = engine.rag_module
    rows = []
    if chat_id is not None:
        with read() as conn:
            rows = conn.execute("SELECT role, text, books FROM chat_messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
    history = []
    for n, (role, text, books) in enumerate(rows):
        if role == "assistant":
            history.append(rag_module.AIMessage(content=text))
            continue
        history.append(rag_module.HumanMessage(content=text))
        answer = rows[n + 1] if n + 1 < len(rows) and rows[n + 1][0] == "assistant" else None
        found = json.loads(answer[2]) if answer and answer[2] else []
        prompt = f"\n{rag_module.CONTEXT}\n" + "".join(
            f"{i}. {b['title']}. Автор: {b['author']})\n" for i, b in enumerate(found, 1)
        )
        history.append(rag_module.HumanMessage(content=prompt))
    history = history[-(rag.max_messages - 1):] if rag.max_messages > 1 else []
    return [rag_module.SystemMessage(content=rag_module.PROMPT_SYSTEM), *history]


def save_turn(chat_id, question, reply, books, set_id):
    """Stores a question and its answer. Call under the lock."""
    db.conn.execute("INSERT INTO chat_messages (chat_id, role, text) VALUES (?, 'user', ?)", (chat_id, question))
    db.conn.execute(
        "INSERT INTO chat_messages (chat_id, role, text, books, set_id) VALUES (?, 'assistant', ?, ?, ?)",
        (chat_id, reply, json.dumps(books, ensure_ascii=False), set_id),
    )
    db.conn.execute("UPDATE chats SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (chat_id,))
    db.conn.commit()


def mark_feedback(set_id, action):
    """Remembers a like/dislike on the answer that showed this set, so a reopened chat shows it as rated. Under the lock."""
    db.conn.execute("UPDATE chat_messages SET feedback = ? WHERE set_id = ?", (action, set_id))


# ---------- endpoints ----------

def snippet(text, needle, width=90):
    at = text.lower().find(needle.lower())
    if at < 0:
        return None
    start = max(at - width // 3, 0)
    piece = text[start:start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")


@router.get("")
def list_chats(q: str = "", account: dict = Depends(current_reader)):
    """The reader's chats, latest first. With q, only chats whose title or messages contain it, with a snippet."""
    q = q.strip()
    with read() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.updated_at,
                (SELECT m.text FROM chat_messages m WHERE m.chat_id = c.id AND :q != '' AND m.text LIKE :like
                 ORDER BY m.id LIMIT 1)
            FROM chats c
            WHERE c.account_id = :me AND (:q = '' OR c.title LIKE :like OR EXISTS (
                SELECT 1 FROM chat_messages m WHERE m.chat_id = c.id AND m.text LIKE :like))
            -- ties by the latest message: timestamps only have second precision
            ORDER BY c.updated_at DESC, (SELECT MAX(m.id) FROM chat_messages m WHERE m.chat_id = c.id) DESC LIMIT 200
            """,
            {"me": account["id"], "q": q, "like": f"%{q}%"},
        ).fetchall()
    return [
        {"id": i, "title": title, "updated_at": at, "snippet": snippet(match, q) if match else None}
        for i, title, at, match in rows
    ]


@router.get("/{chat_id}")
def get_chat(chat_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        chat = own_chat(conn, chat_id, account["id"])
        rows = conn.execute(
            "SELECT role, text, books, set_id, feedback, created_at FROM chat_messages WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()
    chat["messages"] = [
        {"role": role, "text": text, "books": json.loads(books) if books else [], "set_id": set_id,
         "feedback": feedback, "at": at}
        for role, text, books, set_id, feedback, at in rows
    ]
    return chat


class ChatTitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.patch("/{chat_id}")
def rename_chat(chat_id: int, body: ChatTitleIn, account: dict = Depends(current_reader)):
    with read() as conn:
        own_chat(conn, chat_id, account["id"])
    with lock:
        db.conn.execute("UPDATE chats SET title = ? WHERE id = ?", (" ".join(body.title.split()), chat_id))
        db.conn.commit()
    with read() as conn:
        return own_chat(conn, chat_id, account["id"])


@router.delete("/{chat_id}")
def delete_chat(chat_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        own_chat(conn, chat_id, account["id"])
    with lock:
        db.conn.execute("DELETE FROM chat_messages WHERE chat_id = ?", (chat_id,))
        db.conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        db.conn.commit()
    return {"ok": True}


# ---------- personal data ----------

def export_chats(account_id):
    with read() as conn:
        chats = conn.execute("SELECT id, title, created_at FROM chats WHERE account_id = ? ORDER BY id", (account_id,)).fetchall()
        messages = conn.execute(
            """
            SELECT m.chat_id, m.role, m.text, m.created_at FROM chat_messages m
            JOIN chats c ON c.id = m.chat_id WHERE c.account_id = ? ORDER BY m.id
            """,
            (account_id,),
        ).fetchall()
    by_chat = {}
    for chat_id, role, text, at in messages:
        by_chat.setdefault(chat_id, []).append({"role": role, "text": text, "at": at})
    return [{"title": title, "started": at, "messages": by_chat.get(i, [])} for i, title, at in chats]


def delete_chats(account_id):
    """For DELETE /api/me. Call under the lock; the caller commits."""
    db.conn.execute("DELETE FROM chat_messages WHERE chat_id IN (SELECT id FROM chats WHERE account_id = ?)", (account_id,))
    db.conn.execute("DELETE FROM chats WHERE account_id = ?", (account_id,))


def message_count(account_id):
    with read() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM chat_messages m JOIN chats c ON c.id = m.chat_id WHERE c.account_id = ?", (account_id,)
        ).fetchone()[0]
