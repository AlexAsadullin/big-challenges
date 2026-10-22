# CLAUDE.md

Guidance for Claude Code when working in this repository. For current status and open issues, see [HANDOVER.md](HANDOVER.md).

## What this is

**Books Advisor**: book recommendations via RAG (FAISS + sentence-transformers + GigaChat) and a simple
recommender. Two front ends share the same core modules, catalog and SQLite database:

- `src/` — the original **Telegram bot** (Russian UI, `pyTelegramBotAPI` async) plus the core: `db.py`, `rag.py`, `recsys.py`.
- `web/` — a **FastAPI web app**: reader app at `/` (mobile), admin console at `/admin` (desktop), API under `/api`, docs at `/docs`.

## Commands

**Docker only.** There is no local venv; don't create one or run the app on the host. The SQLite database, catalog,
FAISS index and models live in the `data` volume (`/data` in the container).

```bash
# Dev: code mounted from the working tree, uvicorn --reload (rebuild only when requirements/Dockerfile change)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose logs -f web

# Production-style run, and the optional Telegram bot
cp .env.example .env && docker compose up -d --build
docker compose --profile bot up -d

# Scripts and one-offs run inside the container
docker compose exec web python /app/scripts/make_sample_csv.py /data/books.csv
docker compose exec web python -c "import sqlite3; print(sqlite3.connect('/data/books.db').execute('SELECT COUNT(*) FROM authors').fetchone())"

# Wipe all data (database, catalog, index, models)
docker compose down -v
```

On first start the entrypoint builds the ~9,900-book catalog (falls back to a 40-book sample offline).
Without `API_KEY` the advisor chat and Add a Book are unavailable; everything else works.
Without `JWT_SECRET` sessions end on every restart (including dev reloads).

There are no tests, linter config or CI. Verify changes against the running container: `/api/health`, `/docs`, the UI
at http://localhost:8000, or a small script calling the API.

## Architecture

### Core (`src/`)
- `cfg.py` — env vars: `BOT_TOKEN`, `DB_PATH`, `CSV_PATH`, `INDEX_PATH`, `API_KEY` (GigaChat). Unset values default to `"..."`.
- `db.py` — `Database`: one shared `sqlite3` connection (`check_same_thread=False`). Tables `embeddings`,
  `user_interactions` (`view`/`like`/`dislike`), `user_profiles` (age, language, profile embedding), `recommendation_sets` (comma-joined book ids).
- `rag.py` — `RAGSystem`: loads `books.csv` into a DataFrame, bi-encoder `all-MiniLM-L6-v2` + cross-encoder
  `ms-marco-MiniLM-L-6-v2` (cached in `./models` relative to the **cwd**), HNSW FAISS index. Query → translate to English
  (deep-translator) → FAISS top 100 → cross-encoder rerank → top_k → appended to per-user chat memory → GigaChat.
  It also mutates and rewrites `books.csv` on every search (view/like/dislike counts, `mean_age`).
- `recsys.py` — `RecommendationSystem.recommend()`: popularity + age-closeness + same-language activity (cold start), or
  cosine to the profile embedding (warm start); samples top_k from a ranked pool, excluding given ids. Weights live in `self.weights`.
- `texts.py` — Russian bot strings and the default Russian system prompt; `states.py` — in-memory bot state.

### Web (`web/`)
- `core.py` — shared singletons: `db`, `recommender`, `engine` (RAG loaded in a background thread at startup; `engine.status`
  is `loading`/`ready`/`unavailable`), the global `lock` (RLock), admin `Settings` (pydantic, stored as JSON in `web_settings`),
  `book_out()` serializer, `summarize()`, Open Library link lookup cached in `web_book_links`.
  It does `sys.path.insert(0, "src")`, so core modules are imported as top-level `db`, `rag`, `recsys`, `cfg`.
- `auth.py` — `accounts` and `refresh_tokens` tables; JWT access (15 min) + rotating refresh tokens (30 days) with
  reuse detection by token family; scrypt/PBKDF2 hashing; in-memory sign-in throttling; `ensure_admin()` from env;
  `CONSENT_VERSION` gates readers (`current_reader` → 403 `consent_required`).
- `user_api.py` — `/api/me*`, `/api/chat`, `/api/recommendations`, `/api/feedback`, `/api/books`, `/api/genres`, `/api/books/{id}/open` (public redirect).
- `social_api.py` — the social layer, keyed by **account id**: tables `authors`, `author_books`, `follows`
  (`target_type` `user`/`author`), `publications` (readers' own books), `communities`, `community_admins`,
  `community_members`, `posts` (text and/or a repost via `publication_id`). Endpoints: `/api/users*`, `/api/authors*`,
  `/api/publications*`, `/api/communities*`, `/api/posts/{id}`, `/api/feed`.
  - `sync_authors()` runs on startup and rebuilds `author_books` from the catalog's `Author` column (split on commas);
    author ids are stable by name so follows survive catalog rebuilds. `link_book_authors()` covers Add a Book.
  - Catalog authors have no account and can't sign in. Published books are separate from the catalog/recommender.
  - Permissions: community owner = creator (implicit admin, can't leave, only one who edits/deletes the community and
    adds/removes admins); admins and owner post, edit and delete any post; reposts only of the poster's own books,
    once per community. Admins are auto-joined as members.
  - `export_social_data()` / `delete_social_data()` are called from `/api/me/export` and `DELETE /api/me`.
- `admin_api.py` — `/api/admin/*` (overview, users, books, settings). Reads use their own read-only connections.
- `app.py` — mounts routers and `static/`, serves `user.html`, `admin.html`, and `/privacy` (template with `{{version}}`, `{{operator}}`, `{{contact}}`).
- `static/` — vanilla JS/CSS, no build step. `common.js` holds shared fetch/auth helpers and `el()`.
  Reader app: `user.js` (tabs, chat, For You, Add a Book, auth, routing) and `social.js` (feed, Explore, detail pages).
  Routing is by hash: tabs (`#feed`, `#chat`, `#forme`, `#explore`, `#profile`, `#add`) and detail pages
  (`#user/12`, `#author/5`, `#community/3`, `#publication/7`, `#publish[/id]`, `#new-community`, `#edit-community/3`,
  `#following/12`, `#followers/12`) registered in `PAGES` in `social.js` and rendered into `[data-panel="page"]`.

## Conventions and gotchas

- **User ids**: web accounts map to recommender user id `-account_id` (`auth.user_id_of`); positive ids are Telegram users.
  Admin "source" is derived from the sign. The social tables use the plain account id.
- **Don't change the recommender logic** (`src/recsys.py`, scoring in `rag.py`) unless asked: current work is UI and backend.
- Schema changes: `CREATE TABLE IF NOT EXISTS` at module import, and `ALTER TABLE ... ADD COLUMN` guarded by
  `PRAGMA table_info` for existing databases (see `bio` in `auth.py`). There are no migrations.
- **Locking**: anything touching the shared `db.conn` or the RAG/recommender DataFrames goes under `core.lock`. Slow GigaChat
  calls run outside it (see `user_api.chat`). Read-only queries open their own `sqlite3.connect(DB_PATH)`.
- **The catalog is a CSV that gets rewritten constantly.** `rag.books_df` and `recommender.books_df` are separate copies;
  the recommender reloads on file mtime. Columns: `id,Title,Author,Genre,Description,Rating,Combined,like_count,view_count,dislike_count,mean_age[,Link]`.
- **FAISS index must match the catalog size**: `core.drop_stale_index()` deletes it on startup if counts differ. After adding a
  book, the index is written to disk (`user_api.add_book`).
- Admin settings are applied by patching live objects and `rag` module globals (`PROMPT_SYSTEM`, `CONTEXT`) in `core.apply_settings`.
- Bump `CONSENT_VERSION` in `web/auth.py` whenever `web/templates/privacy.html` changes meaningfully.
- Data deletion (`DELETE /api/me`) must cover every table keyed by user id — update it when adding new per-user tables.
- Code style: web code and comments in English, short docstrings, comments explain *why*. Core `src/` modules have Russian
  docstrings/comments; keep the language of the file you're editing.
- Generated/large artifacts are gitignored: `books.csv`, `books.db`, `faiss_index.index`, `models/`, `.env`, `venv/`. Never commit them.
- In the browser pane, `.enter` fade-in animations can stall while the pane is hidden, so screenshots look blank or
  dim. Check the DOM text before assuming a render bug.
