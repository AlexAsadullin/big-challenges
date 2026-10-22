"""Social layer of the reader app: public profiles, authors, follows, published books, communities and the feed.

Ids here are account ids (not the negative recommender user ids). Catalog authors come from books.csv and have
no account: they can be followed but never sign in (many of them, like Pushkin, are long dead).
"""
import sqlite3
from contextlib import closing
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from web.auth import current_reader
from web.core import DB_PATH, book_out, db, lock, logger, recommender, summarize

router = APIRouter(prefix="/api", tags=["social"])

with lock:
    db.conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS author_books (
            author_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (author_id, book_id)
        );
        CREATE INDEX IF NOT EXISTS author_books_book ON author_books (book_id);
        CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER NOT NULL,
            target_type TEXT NOT NULL CHECK (target_type IN ('user', 'author')),
            target_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (follower_id, target_type, target_id)
        );
        CREATE INDEX IF NOT EXISTS follows_target ON follows (target_type, target_id);
        CREATE TABLE IF NOT EXISTS publications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            genre TEXT,
            description TEXT NOT NULL,
            content TEXT,
            link TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME
        );
        CREATE INDEX IF NOT EXISTS publications_account ON publications (account_id, id);
        CREATE TABLE IF NOT EXISTS communities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            description TEXT,
            owner_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS community_admins (
            community_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (community_id, account_id)
        );
        CREATE TABLE IF NOT EXISTS community_members (
            community_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (community_id, account_id)
        );
        CREATE INDEX IF NOT EXISTS community_members_account ON community_members (account_id);
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            community_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            body TEXT,
            publication_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME
        );
        CREATE INDEX IF NOT EXISTS posts_community ON posts (community_id, id);
        CREATE INDEX IF NOT EXISTS posts_publication ON posts (publication_id);
        """
    )
    db.conn.commit()


def read():
    """Own connection for reads, so they never wait for a slow chat request holding the lock."""
    return closing(sqlite3.connect(DB_PATH))


def person(account_id, name):
    return {"id": account_id, "name": name or f"Reader {account_id}"}


def placeholders(items):
    return ",".join("?" for _ in items)


def page_args(offset, limit, max_limit=100):
    return max(offset, 0), max(1, min(limit, max_limit))


# ---------- catalog authors ----------

def split_authors(value):
    """ "Stephen King, Peter Straub" -> two authors; the catalog lists co-authors and translators this way."""
    if not isinstance(value, str):
        return []
    return [name.strip() for name in value.split(",") if name.strip()]


def _link_books(pairs):
    """pairs: (author name, book id, position). Creates missing authors and links them to their books."""
    names = {}
    for name, _, _ in pairs:
        names.setdefault(name.casefold(), name)
    db.conn.executemany("INSERT OR IGNORE INTO authors (name) VALUES (?)", [(n,) for n in names.values()])
    ids = {}
    for author_id, name in db.conn.execute("SELECT id, name FROM authors"):
        ids.setdefault(name.casefold(), author_id)
    db.conn.executemany(
        "INSERT OR IGNORE INTO author_books (author_id, book_id, position) VALUES (?, ?, ?)",
        [(ids[name.casefold()], book_id, position) for name, book_id, position in pairs],
    )


def sync_authors():
    """Every author in the catalog gets a profile with their books. Runs on startup; author ids stay stable
    across catalog rebuilds (they're keyed by name), so follows survive."""
    books = recommender.load_books()
    pairs = [
        (name, int(book_id), position)
        for book_id, value in zip(books["id"], books["Author"])
        for position, name in enumerate(split_authors(value))
    ]
    with lock:
        db.conn.execute("DELETE FROM author_books")
        _link_books(pairs)
        db.conn.commit()
    logger.info(f"Authors synced: {len({p[0].casefold() for p in pairs})} authors, {len(books)} books")


def link_book_authors(book_id, author_value):
    """For a book just added to the catalog."""
    with lock:
        _link_books([(name, int(book_id), n) for n, name in enumerate(split_authors(author_value))])
        db.conn.commit()


def get_author(conn, author_id):
    row = conn.execute("SELECT id, name FROM authors WHERE id = ?", (author_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Author not found")
    return row


AUTHORS_SQL = """
SELECT au.id, au.name, COUNT(ab.book_id) AS books,
    (SELECT COUNT(*) FROM follows f WHERE f.target_type = 'author' AND f.target_id = au.id) AS followers,
    EXISTS (SELECT 1 FROM follows f WHERE f.target_type = 'author' AND f.target_id = au.id AND f.follower_id = :me)
FROM authors au JOIN author_books ab ON ab.author_id = au.id
"""


def author_row_out(row):
    author_id, name, books, followers, following = row
    return {"id": author_id, "name": name, "books": books, "followers": followers, "is_following": bool(following)}


@router.get("/authors")
def list_authors(q: str = "", offset: int = 0, limit: int = 30, account: dict = Depends(current_reader)):
    offset, limit = page_args(offset, limit)
    where, params = "", {"me": account["id"], "limit": limit, "offset": offset}
    if q.strip():
        where, params["q"] = "WHERE au.name LIKE :q", f"%{q.strip()}%"
    with read() as conn:
        total = conn.execute(
            f"SELECT COUNT(DISTINCT au.id) FROM authors au JOIN author_books ab ON ab.author_id = au.id {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"{AUTHORS_SQL} {where} GROUP BY au.id ORDER BY followers DESC, books DESC, au.name LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
    return {"total": total, "authors": [author_row_out(r) for r in rows]}


@router.get("/authors/{author_id}")
def author_detail(author_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        get_author(conn, author_id)
        row = conn.execute(f"{AUTHORS_SQL} WHERE au.id = :id GROUP BY au.id", {"me": account["id"], "id": author_id}).fetchone()
        book_ids = [b for (b,) in conn.execute("SELECT book_id FROM author_books WHERE author_id = ?", (author_id,))]
    books = recommender.load_books()
    books = books[books["id"].isin(book_ids)].sort_values("Rating", ascending=False, na_position="last")
    out = author_row_out(row) if row else {"id": author_id, "books": 0, "followers": 0, "is_following": False}
    return {**out, "book_list": [book_out(b) for b in books.to_dict(orient="records")]}


# ---------- people & follows ----------

def get_person(conn, account_id):
    row = conn.execute("SELECT id, name, bio, created_at FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "User not found")
    return row


def follow_counts(conn, account_id):
    followers = conn.execute(
        "SELECT COUNT(*) FROM follows WHERE target_type = 'user' AND target_id = ?", (account_id,)
    ).fetchone()[0]
    following = conn.execute("SELECT COUNT(*) FROM follows WHERE follower_id = ?", (account_id,)).fetchone()[0]
    return followers, following


def is_following(conn, follower_id, target_type, target_id):
    return conn.execute(
        "SELECT 1 FROM follows WHERE follower_id = ? AND target_type = ? AND target_id = ?",
        (follower_id, target_type, target_id),
    ).fetchone() is not None


@router.get("/users")
def search_users(q: str = "", limit: int = 30, account: dict = Depends(current_reader)):
    """Find readers by name (emails are private and never searchable)."""
    _, limit = page_args(0, limit)
    with read() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.name, a.bio,
                (SELECT COUNT(*) FROM follows f WHERE f.target_type = 'user' AND f.target_id = a.id) AS followers,
                EXISTS (SELECT 1 FROM follows f WHERE f.target_type = 'user' AND f.target_id = a.id AND f.follower_id = ?)
            FROM accounts a
            WHERE a.id != ? AND (? = '' OR a.name LIKE ? OR CAST(a.id AS TEXT) = ?)
            ORDER BY followers DESC, a.id DESC LIMIT ?
            """,
            (account["id"], account["id"], q.strip(), f"%{q.strip()}%", q.strip(), limit),
        ).fetchall()
    return [
        {**person(i, name), "bio": bio, "followers": followers, "is_following": bool(following)}
        for i, name, bio, followers, following in rows
    ]


@router.get("/users/{account_id}")
def user_profile(account_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        user_id, name, bio, created_at = get_person(conn, account_id)
        followers, following = follow_counts(conn, account_id)
        publications = conn.execute("SELECT COUNT(*) FROM publications WHERE account_id = ?", (account_id,)).fetchone()[0]
        communities = conn.execute(
            """
            SELECT c.id, c.name, CASE WHEN c.owner_id = ? THEN 'owner' ELSE 'admin' END
            FROM communities c
            WHERE c.owner_id = ? OR c.id IN (SELECT community_id FROM community_admins WHERE account_id = ?)
            ORDER BY c.name
            """,
            (account_id, account_id, account_id),
        ).fetchall()
        followed = is_following(conn, account["id"], "user", account_id)
    return {
        **person(user_id, name),
        "bio": bio,
        "joined": created_at,
        "followers": followers,
        "following": following,
        "publications": publications,
        "manages": [{"id": i, "name": n, "role": r} for i, n, r in communities],
        "is_me": account_id == account["id"],
        "is_following": followed,
    }


@router.get("/users/{account_id}/following")
def user_following(account_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        get_person(conn, account_id)
        users = conn.execute(
            """
            SELECT a.id, a.name FROM follows f JOIN accounts a ON a.id = f.target_id
            WHERE f.follower_id = ? AND f.target_type = 'user' ORDER BY f.created_at DESC
            """,
            (account_id,),
        ).fetchall()
        authors = conn.execute(
            """
            SELECT au.id, au.name FROM follows f JOIN authors au ON au.id = f.target_id
            WHERE f.follower_id = ? AND f.target_type = 'author' ORDER BY f.created_at DESC
            """,
            (account_id,),
        ).fetchall()
    return {"users": [person(i, n) for i, n in users], "authors": [{"id": i, "name": n} for i, n in authors]}


@router.get("/users/{account_id}/followers")
def user_followers(account_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        get_person(conn, account_id)
        rows = conn.execute(
            """
            SELECT a.id, a.name FROM follows f JOIN accounts a ON a.id = f.follower_id
            WHERE f.target_type = 'user' AND f.target_id = ? ORDER BY f.created_at DESC
            """,
            (account_id,),
        ).fetchall()
    return [person(i, n) for i, n in rows]


def set_follow(follower_id, target_type, target_id, on):
    with lock:
        if on:
            db.conn.execute(
                "INSERT OR IGNORE INTO follows (follower_id, target_type, target_id) VALUES (?, ?, ?)",
                (follower_id, target_type, target_id),
            )
        else:
            db.conn.execute(
                "DELETE FROM follows WHERE follower_id = ? AND target_type = ? AND target_id = ?",
                (follower_id, target_type, target_id),
            )
        db.conn.commit()
    return {"is_following": on}


@router.post("/users/{account_id}/follow")
def follow_user(account_id: int, account: dict = Depends(current_reader)):
    if account_id == account["id"]:
        raise HTTPException(422, "You can't follow yourself")
    with read() as conn:
        get_person(conn, account_id)
    return set_follow(account["id"], "user", account_id, True)


@router.delete("/users/{account_id}/follow")
def unfollow_user(account_id: int, account: dict = Depends(current_reader)):
    return set_follow(account["id"], "user", account_id, False)


@router.post("/authors/{author_id}/follow")
def follow_author(author_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        get_author(conn, author_id)
    return set_follow(account["id"], "author", author_id, True)


@router.delete("/authors/{author_id}/follow")
def unfollow_author(author_id: int, account: dict = Depends(current_reader)):
    return set_follow(account["id"], "author", author_id, False)


# ---------- published books ----------
# Books readers write themselves. They live on the reader's profile, apart from the catalog and the recommender.

class PublicationIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    genre: Optional[str] = Field(default=None, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    content: Optional[str] = Field(default=None, max_length=500_000)
    link: Optional[str] = Field(default=None, max_length=500, pattern=r"^(https?://\S+)?$")


PUBLICATION_SQL = """
SELECT p.id, p.account_id, a.name, p.title, p.genre, p.description, p.link, p.created_at, p.updated_at,
    p.content IS NOT NULL AND p.content != '' AS has_content
FROM publications p LEFT JOIN accounts a ON a.id = p.account_id
"""


def publication_out(row, me):
    pub_id, account_id, name, title, genre, description, link, created_at, updated_at, has_content = row
    return {
        "id": pub_id,
        "author": person(account_id, name),
        "title": title,
        "genre": genre,
        "description": description,
        "summary": summarize(description),
        "link": link,
        "has_content": bool(has_content),
        "created_at": created_at,
        "updated_at": updated_at,
        "is_mine": account_id == me,
    }


def publications_by_id(conn, ids, me):
    if not ids:
        return {}
    rows = conn.execute(f"{PUBLICATION_SQL} WHERE p.id IN ({placeholders(ids)})", list(ids)).fetchall()
    return {row[0]: publication_out(row, me) for row in rows}


def get_publication(conn, pub_id, me):
    found = publications_by_id(conn, [pub_id], me)
    if not found:
        raise HTTPException(404, "Book not found")
    return found[pub_id]


def clean_publication(body: PublicationIn):
    return (
        body.title.strip(), (body.genre or "").strip() or None, body.description.strip(),
        (body.content or "").strip() or None, (body.link or "").strip() or None,
    )


@router.get("/users/{account_id}/publications")
def user_publications(account_id: int, offset: int = 0, limit: int = 30, account: dict = Depends(current_reader)):
    offset, limit = page_args(offset, limit)
    with read() as conn:
        get_person(conn, account_id)
        rows = conn.execute(
            f"{PUBLICATION_SQL} WHERE p.account_id = ? ORDER BY p.id DESC LIMIT ? OFFSET ?", (account_id, limit, offset)
        ).fetchall()
    return [publication_out(r, account["id"]) for r in rows]


@router.post("/publications", status_code=201)
def publish(body: PublicationIn, account: dict = Depends(current_reader)):
    with lock:
        cur = db.conn.execute(
            "INSERT INTO publications (account_id, title, genre, description, content, link) VALUES (?, ?, ?, ?, ?, ?)",
            (account["id"], *clean_publication(body)),
        )
        db.conn.commit()
    with read() as conn:
        return get_publication(conn, cur.lastrowid, account["id"])


@router.get("/publications/{pub_id}")
def publication_detail(pub_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        out = get_publication(conn, pub_id, account["id"])
        out["content"] = conn.execute("SELECT content FROM publications WHERE id = ?", (pub_id,)).fetchone()[0]
        out["reposted_to"] = [
            {"id": i, "name": n}
            for i, n in conn.execute(
                """
                SELECT DISTINCT c.id, c.name FROM posts p JOIN communities c ON c.id = p.community_id
                WHERE p.publication_id = ? ORDER BY c.name
                """,
                (pub_id,),
            )
        ]
    return out


def own_publication(conn, pub_id, account_id):
    row = conn.execute("SELECT account_id FROM publications WHERE id = ?", (pub_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Book not found")
    if row[0] != account_id:
        raise HTTPException(403, "Only the author can change this book")


@router.put("/publications/{pub_id}")
def edit_publication(pub_id: int, body: PublicationIn, account: dict = Depends(current_reader)):
    with read() as conn:
        own_publication(conn, pub_id, account["id"])
    with lock:
        db.conn.execute(
            """
            UPDATE publications SET title = ?, genre = ?, description = ?, content = ?, link = ?,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (*clean_publication(body), pub_id),
        )
        db.conn.commit()
    return publication_detail(pub_id, account)


@router.delete("/publications/{pub_id}")
def delete_publication(pub_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        own_publication(conn, pub_id, account["id"])
    with lock:
        db.conn.execute("DELETE FROM posts WHERE publication_id = ?", (pub_id,))  # its reposts go too
        db.conn.execute("DELETE FROM publications WHERE id = ?", (pub_id,))
        db.conn.commit()
    return {"ok": True}


# ---------- communities ----------
# Anyone can create a community. Its creator (owner) appoints admins; only the owner and admins post
# and edit posts. Everyone else can read and join.

class CommunityIn(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    description: Optional[str] = Field(default=None, max_length=2000)


class AdminIn(BaseModel):
    account_id: int


class PostIn(BaseModel):
    body: Optional[str] = Field(default=None, max_length=5000)
    publication_id: Optional[int] = None


class PostEditIn(BaseModel):
    body: Optional[str] = Field(default=None, max_length=5000)


COMMUNITIES_SQL = """
SELECT * FROM (
    SELECT c.id, c.name, c.description, c.owner_id, o.name AS owner_name, c.created_at,
        (SELECT COUNT(*) FROM community_members m WHERE m.community_id = c.id) AS members,
        (SELECT COUNT(*) FROM posts p WHERE p.community_id = c.id) AS posts,
        CASE
            WHEN c.owner_id = :me THEN 'owner'
            WHEN EXISTS (SELECT 1 FROM community_admins ad WHERE ad.community_id = c.id AND ad.account_id = :me) THEN 'admin'
            WHEN EXISTS (SELECT 1 FROM community_members m WHERE m.community_id = c.id AND m.account_id = :me) THEN 'member'
        END AS role
    FROM communities c LEFT JOIN accounts o ON o.id = c.owner_id
)
"""


def community_out(row):
    community_id, name, description, owner_id, owner_name, created_at, members, posts, role = row
    return {
        "id": community_id,
        "name": name,
        "description": description,
        "owner": person(owner_id, owner_name),
        "created_at": created_at,
        "members": members,
        "posts": posts,
        "role": role,
        "can_post": role in ("owner", "admin"),
    }


def get_community(conn, community_id, me):
    row = conn.execute(f"{COMMUNITIES_SQL} WHERE id = :id", {"me": me, "id": community_id}).fetchone()
    if row is None:
        raise HTTPException(404, "Community not found")
    return community_out(row)


def require_role(conn, community_id, me, roles):
    community = get_community(conn, community_id, me)
    if community["role"] not in roles:
        raise HTTPException(403, "Only the community's creator can do this" if roles == ("owner",)
                            else "Only the community's creator and admins can do this")
    return community


def managed_ids(conn, me):
    return {
        i for (i,) in conn.execute(
            "SELECT id FROM communities WHERE owner_id = ? UNION SELECT community_id FROM community_admins WHERE account_id = ?",
            (me, me),
        )
    }


@router.get("/communities")
def list_communities(q: str = "", scope: str = "all", offset: int = 0, limit: int = 30,
                     account: dict = Depends(current_reader)):
    """scope: all, joined (member, admin or owner) or managed (admin or owner)."""
    offset, limit = page_args(offset, limit)
    where, params = ["1"], {"me": account["id"], "limit": limit, "offset": offset}
    if q.strip():
        where.append("(name LIKE :q OR description LIKE :q)")
        params["q"] = f"%{q.strip()}%"
    if scope == "joined":
        where.append("role IS NOT NULL")
    elif scope == "managed":
        where.append("role IN ('owner', 'admin')")
    with read() as conn:
        rows = conn.execute(
            f"{COMMUNITIES_SQL} WHERE {' AND '.join(where)} ORDER BY members DESC, id DESC LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
    return [community_out(r) for r in rows]


@router.post("/communities", status_code=201)
def create_community(body: CommunityIn, account: dict = Depends(current_reader)):
    with lock:
        try:
            cur = db.conn.execute(
                "INSERT INTO communities (name, description, owner_id) VALUES (?, ?, ?)",
                (body.name.strip(), (body.description or "").strip() or None, account["id"]),
            )
        except sqlite3.IntegrityError:
            db.conn.rollback()
            raise HTTPException(409, "A community with this name already exists")
        db.conn.execute(
            "INSERT INTO community_members (community_id, account_id) VALUES (?, ?)", (cur.lastrowid, account["id"])
        )
        db.conn.commit()
    with read() as conn:
        return get_community(conn, cur.lastrowid, account["id"])


def community_admins(conn, community_id):
    return [
        person(i, n) for i, n in conn.execute(
            """
            SELECT a.id, a.name FROM community_admins ad JOIN accounts a ON a.id = ad.account_id
            WHERE ad.community_id = ? ORDER BY ad.added_at
            """,
            (community_id,),
        )
    ]


@router.get("/communities/{community_id}")
def community_detail(community_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        return {**get_community(conn, community_id, account["id"]), "admins": community_admins(conn, community_id)}


@router.put("/communities/{community_id}")
def edit_community(community_id: int, body: CommunityIn, account: dict = Depends(current_reader)):
    with read() as conn:
        require_role(conn, community_id, account["id"], ("owner",))
    with lock:
        try:
            db.conn.execute(
                "UPDATE communities SET name = ?, description = ? WHERE id = ?",
                (body.name.strip(), (body.description or "").strip() or None, community_id),
            )
        except sqlite3.IntegrityError:
            db.conn.rollback()
            raise HTTPException(409, "A community with this name already exists")
        db.conn.commit()
    return community_detail(community_id, account)


def drop_communities(ids):
    """Deletes communities with their posts, admins and members. Call under the lock; the caller commits."""
    for table, column in (("posts", "community_id"), ("community_admins", "community_id"),
                          ("community_members", "community_id"), ("communities", "id")):
        db.conn.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders(ids)})", list(ids))


@router.delete("/communities/{community_id}")
def delete_community(community_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        require_role(conn, community_id, account["id"], ("owner",))
    with lock:
        drop_communities([community_id])
        db.conn.commit()
    return {"ok": True}


@router.post("/communities/{community_id}/join")
def join_community(community_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        get_community(conn, community_id, account["id"])
    with lock:
        db.conn.execute(
            "INSERT OR IGNORE INTO community_members (community_id, account_id) VALUES (?, ?)", (community_id, account["id"])
        )
        db.conn.commit()
    return community_detail(community_id, account)


@router.delete("/communities/{community_id}/join")
def leave_community(community_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        if get_community(conn, community_id, account["id"])["role"] == "owner":
            raise HTTPException(409, "You created this community: delete it instead of leaving")
    with lock:
        # Leaving also gives up admin rights
        for table in ("community_members", "community_admins"):
            db.conn.execute(f"DELETE FROM {table} WHERE community_id = ? AND account_id = ?", (community_id, account["id"]))
        db.conn.commit()
    return community_detail(community_id, account)


@router.get("/communities/{community_id}/members")
def community_members(community_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        community = get_community(conn, community_id, account["id"])
        admins = {a["id"] for a in community_admins(conn, community_id)}
        rows = conn.execute(
            """
            SELECT a.id, a.name FROM community_members m JOIN accounts a ON a.id = m.account_id
            WHERE m.community_id = ? ORDER BY m.joined_at LIMIT 500
            """,
            (community_id,),
        ).fetchall()
    role = lambda i: "owner" if i == community["owner"]["id"] else "admin" if i in admins else "member"  # noqa: E731
    return [{**person(i, n), "role": role(i)} for i, n in rows]


@router.post("/communities/{community_id}/admins")
def add_admin(community_id: int, body: AdminIn, account: dict = Depends(current_reader)):
    with read() as conn:
        community = require_role(conn, community_id, account["id"], ("owner",))
        get_person(conn, body.account_id)
    if body.account_id == community["owner"]["id"]:
        raise HTTPException(422, "The creator already manages this community")
    with lock:
        db.conn.execute(
            "INSERT OR IGNORE INTO community_admins (community_id, account_id) VALUES (?, ?)", (community_id, body.account_id)
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO community_members (community_id, account_id) VALUES (?, ?)", (community_id, body.account_id)
        )
        db.conn.commit()
    return community_detail(community_id, account)


@router.delete("/communities/{community_id}/admins/{account_id}")
def remove_admin(community_id: int, account_id: int, account: dict = Depends(current_reader)):
    """The owner removes any admin; an admin can step down themselves."""
    with read() as conn:
        community = get_community(conn, community_id, account["id"])
    if community["role"] != "owner" and account_id != account["id"]:
        raise HTTPException(403, "Only the community's creator can remove admins")
    with lock:
        db.conn.execute("DELETE FROM community_admins WHERE community_id = ? AND account_id = ?", (community_id, account_id))
        db.conn.commit()
    return community_detail(community_id, account)


# ---------- posts ----------

POST_SQL = """
SELECT p.id, p.community_id, c.name, p.account_id, a.name, p.body, p.publication_id, p.created_at, p.updated_at
FROM posts p JOIN communities c ON c.id = p.community_id LEFT JOIN accounts a ON a.id = p.account_id
"""


def posts_out(conn, rows, me):
    publications = publications_by_id(conn, {r[6] for r in rows if r[6]}, me)
    managed = managed_ids(conn, me)
    return [
        {
            "id": post_id,
            "community": {"id": community_id, "name": community_name},
            "author": person(account_id, name),
            "body": body,
            "publication": publications.get(pub_id),
            "created_at": created_at,
            "updated_at": updated_at,
            "can_edit": community_id in managed,
        }
        for post_id, community_id, community_name, account_id, name, body, pub_id, created_at, updated_at in rows
    ]


def get_post(conn, post_id, me):
    rows = conn.execute(f"{POST_SQL} WHERE p.id = ?", (post_id,)).fetchall()
    if not rows:
        raise HTTPException(404, "Post not found")
    return posts_out(conn, rows, me)[0]


@router.get("/communities/{community_id}/posts")
def community_posts(community_id: int, offset: int = 0, limit: int = 20, account: dict = Depends(current_reader)):
    offset, limit = page_args(offset, limit, 50)
    with read() as conn:
        get_community(conn, community_id, account["id"])
        rows = conn.execute(
            f"{POST_SQL} WHERE p.community_id = ? ORDER BY p.id DESC LIMIT ? OFFSET ?", (community_id, limit, offset)
        ).fetchall()
        return posts_out(conn, rows, account["id"])


@router.post("/communities/{community_id}/posts", status_code=201)
def create_post(community_id: int, body: PostIn, account: dict = Depends(current_reader)):
    """A post, or a repost of one of the poster's own published books (with an optional comment)."""
    text = (body.body or "").strip() or None
    if not text and not body.publication_id:
        raise HTTPException(422, "Write something or pick a book to repost")
    with read() as conn:
        require_role(conn, community_id, account["id"], ("owner", "admin"))
        if body.publication_id:
            row = conn.execute("SELECT account_id FROM publications WHERE id = ?", (body.publication_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Book not found")
            if row[0] != account["id"]:
                raise HTTPException(403, "You can only repost your own books")
            if conn.execute(
                "SELECT 1 FROM posts WHERE community_id = ? AND publication_id = ?", (community_id, body.publication_id)
            ).fetchone():
                raise HTTPException(409, "This book is already reposted to this community")
    with lock:
        cur = db.conn.execute(
            "INSERT INTO posts (community_id, account_id, body, publication_id) VALUES (?, ?, ?, ?)",
            (community_id, account["id"], text, body.publication_id),
        )
        db.conn.commit()
    with read() as conn:
        return get_post(conn, cur.lastrowid, account["id"])


def editable_post(conn, post_id, me):
    post = get_post(conn, post_id, me)
    if not post["can_edit"]:
        raise HTTPException(403, "Only the community's creator and admins can change posts")
    return post


@router.put("/posts/{post_id}")
def edit_post(post_id: int, body: PostEditIn, account: dict = Depends(current_reader)):
    text = (body.body or "").strip() or None
    with read() as conn:
        post = editable_post(conn, post_id, account["id"])
    if not text and not post["publication"]:
        raise HTTPException(422, "A post needs some text")
    with lock:
        db.conn.execute("UPDATE posts SET body = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (text, post_id))
        db.conn.commit()
    with read() as conn:
        return get_post(conn, post_id, account["id"])


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, account: dict = Depends(current_reader)):
    with read() as conn:
        editable_post(conn, post_id, account["id"])
    with lock:
        db.conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        db.conn.commit()
    return {"ok": True}


# ---------- feed ----------

@router.get("/feed")
def feed(offset: int = 0, limit: int = 20, account: dict = Depends(current_reader)):
    """Posts from joined communities and books published by followed readers, newest first."""
    offset, limit = page_args(offset, limit, 50)
    me = account["id"]
    with read() as conn:
        rows = conn.execute(
            """
            SELECT 'post', p.id, p.created_at FROM posts p
            JOIN community_members m ON m.community_id = p.community_id AND m.account_id = :me
            UNION ALL
            SELECT 'publication', pub.id, pub.created_at FROM publications pub
            JOIN follows f ON f.follower_id = :me AND f.target_type = 'user' AND f.target_id = pub.account_id
            ORDER BY 3 DESC, 2 DESC LIMIT :limit OFFSET :offset
            """,
            {"me": me, "limit": limit + 1, "offset": offset},
        ).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        post_ids = [i for kind, i, _ in rows if kind == "post"]
        posts = {
            p["id"]: p for p in posts_out(
                conn, conn.execute(f"{POST_SQL} WHERE p.id IN ({placeholders(post_ids)})", post_ids).fetchall(), me
            )
        } if post_ids else {}
        publications = publications_by_id(conn, [i for kind, i, _ in rows if kind == "publication"], me)
    items = [
        {"kind": kind, "at": at, kind: posts[i] if kind == "post" else publications[i]}
        for kind, i, at in rows
        if (posts if kind == "post" else publications).get(i)
    ]
    return {"items": items, "next_offset": offset + limit if more else None}


# ---------- personal data ----------

def export_social_data(account_id):
    """The reader's social data, for GET /api/me/export."""
    with read() as conn:
        follows = conn.execute(
            """
            SELECT f.target_type, COALESCE(a.name, au.name, f.target_id), f.created_at FROM follows f
            LEFT JOIN accounts a ON f.target_type = 'user' AND a.id = f.target_id
            LEFT JOIN authors au ON f.target_type = 'author' AND au.id = f.target_id
            WHERE f.follower_id = ?
            """,
            (account_id,),
        ).fetchall()
        publications = conn.execute(
            "SELECT title, genre, description, content, link, created_at FROM publications WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        communities = conn.execute(
            """
            SELECT c.name, CASE WHEN c.owner_id = :me THEN 'owner'
                WHEN ad.account_id IS NOT NULL THEN 'admin' ELSE 'member' END
            FROM communities c
            LEFT JOIN community_admins ad ON ad.community_id = c.id AND ad.account_id = :me
            LEFT JOIN community_members m ON m.community_id = c.id AND m.account_id = :me
            WHERE c.owner_id = :me OR ad.account_id IS NOT NULL OR m.account_id IS NOT NULL
            """,
            {"me": account_id},
        ).fetchall()
        posts = conn.execute(
            """
            SELECT c.name, p.body, p.publication_id, p.created_at FROM posts p
            JOIN communities c ON c.id = p.community_id WHERE p.account_id = ?
            """,
            (account_id,),
        ).fetchall()
    return {
        "following": [{"type": t, "name": n, "since": at} for t, n, at in follows],
        "published_books": [
            dict(zip(("title", "genre", "description", "content", "link", "published_at"), row)) for row in publications
        ],
        "communities": [{"name": n, "role": r} for n, r in communities],
        "posts": [{"community": c, "text": b, "repost_of_book": p, "at": at} for c, b, p, at in posts],
    }


def delete_social_data(account_id):
    """Everything social tied to an account, for DELETE /api/me. Call under the lock; the caller commits.
    Communities the reader created go with them, including their posts."""
    owned = [i for (i,) in db.conn.execute("SELECT id FROM communities WHERE owner_id = ?", (account_id,))]
    if owned:
        drop_communities(owned)
    pub_ids = [i for (i,) in db.conn.execute("SELECT id FROM publications WHERE account_id = ?", (account_id,))]
    if pub_ids:
        db.conn.execute(f"DELETE FROM posts WHERE publication_id IN ({placeholders(pub_ids)})", pub_ids)
    db.conn.execute("DELETE FROM posts WHERE account_id = ?", (account_id,))
    db.conn.execute("DELETE FROM publications WHERE account_id = ?", (account_id,))
    db.conn.execute(
        "DELETE FROM follows WHERE follower_id = ? OR (target_type = 'user' AND target_id = ?)", (account_id, account_id)
    )
    for table in ("community_admins", "community_members"):
        db.conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
