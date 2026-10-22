# Handover

State of the project as of 2026-10-22. Architecture and commands are in [CLAUDE.md](CLAUDE.md); user-facing docs in [README.md](README.md).

## Current focus

UI and backend. **The recommender logic is out of scope for now**: leave `src/recsys.py` and the scoring in `src/rag.py` alone.

## Where things stand

Branch `web-app` (base: `main`). History, newest first:

- `884dc31` update docs
- `776209a` Docker Compose setup (web + optional `bot` profile, shared `data` volume, entrypoint builds the catalog on first start)
- `2f949df` Accounts with bearer tokens (JWT + rotating refresh) and a personal-data agreement (`/privacy`, consent, export, delete)
- `e5a15fb` 10k-book catalog builder (`scripts/build_dataset.py`)
- `0a9e344` For You no longer repeats books (skips recent sets and rated books, samples from a ranked pool)
- `4bc5c87` 1–2 sentence summary on every recommended book
- `45c6217` Recommended books link to Open Library
- `ba663d1` Split web into reader app and admin console, English UI
- `c7eee7b` FastAPI web interface
- `dc5bfbd`, `b998e59` original Telegram bot

### Saved chats (2026-10-22, after PR #1 was opened)

- Every advisor chat is stored (`web/chat_history.py`); the model's context is rebuilt from the stored chat on each
  message, so switching chats and restarts lose nothing.
- **Chats** button on the Advisor tab opens a drawer on the left: New chat, search across titles, questions and
  answers (with a snippet), open, rename, delete. Reopened chats keep their books and like/dislike state.
- Export and account deletion include chats; the admin user detail counts saved messages for web readers.
- Verified: 20 checks in the container with a stub model (context per chat, no leaks between chats or readers,
  trimming, search, feedback, rename, delete, export, account deletion) and the drawer in the browser at phone width.
  Not verified against real GigaChat (no `API_KEY` in `.env`).
- Also: Advisor opens by default when the feed has nothing new; "Save changes" on the profile appears only when
  something changed.

### Social layer and Docker setup (2026-10-22, commit 947debe)

**Social layer** (`web/social_api.py`, `web/static/social.js`, edits to `user.html`/`user.js`/`user.css`, `auth.py`, `user_api.py`, `app.py`):
- Public profiles: `accounts.bio` column; profile page with followers/following, published books, communities run.
- Follows: readers follow readers and catalog authors.
- Catalog authors: on startup every author in `books.csv` gets an `authors` row linked to their books
  (3,814 authors for the 9,929-book catalog; Alexander Pushkin → Eugene Onegin). They have no account and can't sign in.
- Publishing: readers publish their own books (title, genre, description, optional text up to 500k chars, link). No copyright checks.
- Communities: anyone creates one; the creator appoints admins from members; only the creator and admins post and edit.
  Creators and admins repost their own books into communities they run.
- Feed: posts from joined communities and books from followed readers.
- UI: tab bar is now Feed · Advisor · For You · Explore · Profile. Add a Book moved under Explore. Detail pages use hash routes.
- Account export and deletion cover all the new tables.

**Docker-only workflow**: local `venv/` deleted; `docker-compose.dev.yml` added (code mounted, `--reload`);
`venv/` and `.DS_Store` added to `.gitignore` and `.dockerignore`; README "Running locally" replaced by "Development".

**Earlier pending fixes**: the `web` logger gets its own handler; `POST /api/books` saves the FAISS index to disk.

Verified in Docker: a 39-check API script (permissions, reposts, feed, follows, authors, export, account deletion)
passed. In the browser at phone width I checked the feed, a community as member and as creator, an author page with
follow, the profile, and publishing then reposting a book.

## Local environment notes

- Docker Desktop must be running. The `arseni_books_data` volume holds the database and catalog; it currently has test
  accounts and communities from the checks above (`docker compose down -v` wipes them).
- `.env` is empty: no `API_KEY` (chat and Add a Book unavailable), no admin account, random `JWT_SECRET`
  (sign-ins end on every reload).
- The repo root still has the old host-run files `books.csv` (40-book sample), `books.db`, `faiss_index.index` and
  `models/`. They're gitignored and Docker doesn't use them; delete them if nothing else needs them.

## Known issues and gaps

Social layer:
1. No moderation: the site admin console doesn't list communities, posts or published books, and can't remove them.
2. Catalog authors can't be claimed by a living author's account yet; co-authors, illustrators and translators listed
   in the catalog's `Author` column become authors too.
3. The feed has no activity for followed catalog authors (they don't post); they appear in Following and on author pages.
4. Offset pagination; feed and lists can shift if items are added while scrolling.
5. Published books aren't searchable by the advisor or in Explore, and they aren't in the recommender (by design for now).

Pre-existing (recommender: noted, not to be fixed now):
6. Warm-start personalisation never runs (`get_user_metadata` doesn't return `profile_embedding`; the `embeddings` table is never filled).
7. `rag.search_books` rewrites `books.csv` several times per chat request; N+1 queries in `update_mean_age_for_books`.
8. `db.add_interaction` keeps one row per (user, book), so a later "view" erases a "like".
9. Sign-in throttling and the Telegram bot's chat memory are in memory: lost on restart, not shared across workers.
   Run one uvicorn worker. (Web chats are saved in the database.)
10. GigaChat runs with `verify_ssl_certs=False`. No tests or CI.

## Suggested next steps

- Commit this work (and the two earlier fixes) on `web-app`; open a PR into `main`.
- Put real values in `.env` (`JWT_SECRET`, `ADMIN_EMAIL`/`ADMIN_PASSWORD`, `API_KEY`).
- Admin console: communities, posts and published books lists with delete, for moderation.
- Let a living author claim their catalog author profile (link `authors` to an account after verification).
- Add a pytest suite running in the container (auth, social permissions, account deletion).
