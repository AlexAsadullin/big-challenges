"""Shared state for the web app: database, recommender, RAG engine and admin settings."""
import json
import logging
import math
import re
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
import warnings
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cfg import API_KEY, CSV_PATH, DB_PATH, INDEX_PATH  # noqa: E402
from db import Database  # noqa: E402
from recsys import DEFAULT_WEIGHTS, RecommendationSystem  # noqa: E402

warnings.filterwarnings("ignore")
# Own handler: ML libraries configure the root logger first, which would silently drop these INFO lines
logger = logging.getLogger("web")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False

AGE_BUCKETS = [(8, 17), (18, 27), (28, 37), (38, 47), (48, 57), (58, 67), (68, 77), (78, 87), (88, 97)]

# The shared sqlite connection and DataFrames are mutated under one lock;
# read-only catalog and admin queries skip it and open their own connection.
lock = threading.RLock()
db = Database(DB_PATH)
recommender = RecommendationSystem(db, CSV_PATH)


class Engine:
    rag = None
    rag_module = None
    status = {"state": "loading", "error": None}


engine = Engine()

with lock:
    db.conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS web_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS web_book_links (
            book_id INTEGER PRIMARY KEY,
            url TEXT
        );
        """
    )
    db.conn.commit()


# ---------- settings ----------

SYSTEM_PROMPT = (
    "You are a book advisor who answers any of the user's questions about books. "
    "Be friendly and concise, without excessive politeness. "
    "The books from the database are not sent by the user: you already have them. "
    "If those books contain mistakes, correct them. "
    "If the user just wants to chat, chat back. "
    "Reply in English unless the user writes in another language."
)


class LLMSettings(BaseModel):
    temperature: float = Field(0.7, ge=0, le=2)
    top_p: float = Field(0.35, gt=0, le=1)
    books_in_prompt: int = Field(5, ge=1, le=20)
    history_length: int = Field(10, ge=2, le=50)
    system_prompt: str = Field(SYSTEM_PROMPT, min_length=1, max_length=4000)


class RecsysSettings(BaseModel):
    top_k: int = Field(5, ge=1, le=20)
    pool_size: int = Field(40, ge=1, le=500)
    skip_recent_sets: int = Field(5, ge=0, le=50)
    like: float = Field(DEFAULT_WEIGHTS["like"], ge=0, le=10)
    view: float = Field(DEFAULT_WEIGHTS["view"], ge=0, le=10)
    dislike: float = Field(DEFAULT_WEIGHTS["dislike"], ge=0, le=10)
    age: float = Field(DEFAULT_WEIGHTS["age"], ge=0, le=10)
    age_scale: float = Field(DEFAULT_WEIGHTS["age_scale"], ge=1, le=100)
    language: float = Field(DEFAULT_WEIGHTS["language"], ge=0, le=10)
    language_like: float = Field(DEFAULT_WEIGHTS["language_like"], ge=0, le=10)


class Settings(BaseModel):
    llm: LLMSettings = LLMSettings()
    recsys: RecsysSettings = RecsysSettings()


def load_settings() -> Settings:
    with lock:
        row = db.conn.execute("SELECT value FROM web_settings WHERE key = 'settings'").fetchone()
    if not row:
        return Settings()
    try:
        return Settings(**json.loads(row[0]))
    except Exception:
        logger.warning("Stored settings are invalid, falling back to defaults")
        return Settings()


def save_settings(settings: Settings):
    with lock:
        db.conn.execute(
            "INSERT OR REPLACE INTO web_settings (key, value) VALUES ('settings', ?)",
            (settings.model_dump_json(),),
        )
        db.conn.commit()
        apply_settings(settings)


def apply_settings(settings: Settings):
    """Pushes settings into the live recommender and RAG objects."""
    with lock:
        rec = settings.recsys
        recommender.top_k = rec.top_k
        recommender.weights.update(rec.model_dump(exclude={"top_k", "pool_size", "skip_recent_sets"}))
        rag = engine.rag
        if rag is None:
            return
        llm = settings.llm
        rag.top_k = llm.books_in_prompt
        rag.max_messages = llm.history_length
        rag.giga = engine.rag_module.GigaChat(
            credentials=API_KEY, verify_ssl_certs=False, temperature=llm.temperature, top_p=llm.top_p
        )
        # rag.py reads these module globals when it starts a conversation or builds a prompt
        engine.rag_module.PROMPT_SYSTEM = llm.system_prompt
        engine.rag_module.CONTEXT = "Context from the book database:"
        for memory in rag.memory.values():
            memory[0] = engine.rag_module.SystemMessage(content=llm.system_prompt)


def drop_stale_index():
    """rag.py reuses an existing FAISS index as is; if the CSV changed size, rebuild it instead."""
    if not Path(INDEX_PATH).exists():
        return
    import faiss

    books = len(pd.read_csv(CSV_PATH, usecols=["id"]))
    indexed = faiss.read_index(INDEX_PATH).ntotal
    if indexed != books:
        logger.warning(f"FAISS index has {indexed} vectors but the catalog has {books} books: rebuilding it")
        Path(INDEX_PATH).unlink()


def load_rag():
    """Loads RAG in the background: models and the FAISS index are slow, and the rest of the app works without them."""
    if not API_KEY or API_KEY == "...":
        engine.status = {"state": "unavailable", "error": "API_KEY for GigaChat is not set"}
        return
    try:
        drop_stale_index()
        import rag as rag_module

        instance = rag_module.RAGSystem(CSV_PATH, API_KEY, db)
        with lock:
            engine.rag_module = rag_module
            engine.rag = instance
            apply_settings(load_settings())
        engine.status = {"state": "ready", "error": None}
        logger.info("RAG loaded")
    except Exception as e:
        logger.error(f"Failed to load RAG: {e}\n{traceback.format_exc()}")
        engine.status = {"state": "unavailable", "error": str(e)}


# ---------- books ----------

def clean(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


SENTENCE_END = re.compile(r"(?<=[.!?…])[\"'”»)]?\s+(?=[\"'“«(]?[A-ZА-ЯЁ0-9])")


NOT_AN_END = re.compile(r"(?:\b(?:Dr|Mr|Mrs|Ms|Prof|St|Jr|Sr|Mt|vs|etc|No|Vol|i\.e|e\.g)|\b[A-ZА-Я])\.$")


def summarize(text, max_chars=240):
    """First one or two sentences of a description, for book tiles."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = " ".join(text.split())
    sentences = []
    for part in SENTENCE_END.split(text):
        # "Dr. Watson", "J. R. R. Tolkien": an abbreviation or initial isn't a sentence end
        if sentences and NOT_AN_END.search(sentences[-1]):
            sentences[-1] += " " + part
        else:
            sentences.append(part)
    summary = sentences[0]
    # A short first sentence ("A classic.") says little, so add the second one
    if len(sentences) > 1 and len(summary) < 90 and len(summary) + len(sentences[1]) < max_chars:
        summary += " " + sentences[1]
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
    return summary


def book_out(book, full=False):
    out = {
        "id": clean(book.get("id")),
        "title": clean(book.get("Title")),
        "author": clean(book.get("Author")),
        "genre": clean(book.get("Genre")),
        "description": clean(book.get("Description")),
        "summary": summarize(clean(book.get("Description"))),
        "rating": clean(book.get("Rating")),
        "likes": clean(book.get("like_count")),
        "views": clean(book.get("view_count")),
    }
    if full:
        out["link"] = clean(book.get("Link"))
        out["dislikes"] = clean(book.get("dislike_count"))
        out["mean_age"] = clean(book.get("mean_age"))
    return out


def genre_counts(df):
    genres = df["Genre"].dropna().astype(str).str.split(",").explode().str.strip()
    return genres[genres != ""].value_counts()


SORTS = {
    "title": ("Title", True),
    "rating": ("Rating", False),
    "likes": ("like_count", False),
    "views": ("view_count", False),
    "dislikes": ("dislike_count", False),
    "newest": ("id", False),
}


def query_books(q="", genre="", sort="title", offset=0, limit=50) -> pd.DataFrame:
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
    return df


# ---------- book pages ----------
# Books open on Open Library: free, no registration, and work pages carry the description.

OPEN_LIBRARY = "https://openlibrary.org"
SERIES_SUFFIX = re.compile(r"\s*\([^()]*#\s*[\d.]+[^()]*\)\s*$")


def open_library_search_url(title, author):
    return f"{OPEN_LIBRARY}/search?" + urllib.parse.urlencode({"q": f"{title} {author}".strip()})


def find_open_library_work(title, author):
    """Returns the Open Library work page for a book, or None if there's no confident match.

    A free-text query ranks the canonical work first even when it's filed under its original
    title ("Nineteen Eighty-Four", "Le petit prince"); title-field search returns adaptations.
    """
    title = SERIES_SUFFIX.sub("", title)  # "Twilight (Twilight, #1)" -> "Twilight"
    author = author.split(",")[0]  # first author is enough to find the work
    try:
        res = requests.get(
            f"{OPEN_LIBRARY}/search.json",
            params={"q": f"{title} {author}", "limit": 1, "fields": "key,author_name,author_alternative_name"},
            headers={"User-Agent": "BooksAdvisor/1.0"},
            timeout=8,
        )
        docs = res.json().get("docs", []) if res.ok else []
    except Exception as e:
        logger.warning(f"Open Library lookup failed for {title!r}: {e}")
        return None
    if not docs or not docs[0].get("key", "").startswith("/works/"):
        return None
    # Guard against a confident-looking wrong book: the authors must share a name.
    # Alternative names carry transliterations ("Михаил Булгаков" is also "mikhail bulgakov").
    names = {w for w in author.lower().replace(".", " ").split() if len(w) > 2 and w != "and"}
    doc = docs[0]
    found = " ".join(doc.get("author_name", []) + doc.get("author_alternative_name", [])).lower()
    if not any(n in found for n in names):
        return None
    return OPEN_LIBRARY + docs[0]["key"]


def book_link(book_id):
    """The page a book opens: its own link if one was given, else its Open Library page."""
    df = recommender.load_books()
    rows = df[df["id"] == book_id]
    if rows.empty:
        return None
    book = rows.iloc[0]
    own = clean(book.get("Link"))
    if isinstance(own, str) and own.startswith(("http://", "https://")):
        return own
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cached = conn.execute("SELECT url FROM web_book_links WHERE book_id = ?", (book_id,)).fetchone()
        if cached:
            return cached[0]
        title, author = str(book.get("Title") or ""), str(book.get("Author") or "")
        url = find_open_library_work(title, author)
        if url is None:
            return open_library_search_url(title, author)  # not cached, so a later click retries
        conn.execute("INSERT OR REPLACE INTO web_book_links (book_id, url) VALUES (?, ?)", (book_id, url))
        conn.commit()
        return url


def prefetch_links(book_ids):
    """Resolves links right after recommendations are shown, so clicks open instantly."""
    for book_id in book_ids:
        book_link(int(book_id))
