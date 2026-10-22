# Books Advisor Project

## Файлы и модули

- **`bot.py`** — основной файл Telegram-бота. Содержит логику обработки сообщений, команд и взаимодействий с пользователем.  
- **`db.py`** — модуль для работы с базой данных.  
- **`rag.py`** — модуль системы RAG (Retrieval-Augmented Generation).  
- **`recsys.py`** — модуль системы рекомендаций.  
- **`states.py`** — модуль управления состояниями пользователей.  
- **`texts.py`** — файл с текстовыми константами.  
- **`cfg.py`** — файл конфигурации.  

## Установка и запуск

Проект собирается и запускается только в Docker (см. «Running with Docker» ниже), база данных живёт в Docker-томе `data`.

## Web app

`web/` contains a FastAPI app built on the same `db.py`, `rag.py` and `recsys.py` as the bot.

**Reader app** at `/`, designed for phones. Readers create an account (email, password, age, language)
and must accept the privacy policy to sign up.
- **Feed**: posts from communities the reader joined and books published by readers they follow, newest first
- **Explore**: communities, authors and readers, with search. Every author in the catalog has a profile with their
  books (built from `books.csv` on startup). Catalog authors have no account and can't sign in (many, like Pushkin,
  are long dead); readers can follow them
- **Public profiles**: name, bio, followers/following, published books, communities they run. Readers follow each other
- **Publishing**: readers publish their own books (title, genre, description, optional full text and link) to their
  profile. No copyright or plagiarism checks yet. Published books stay apart from the catalog and the recommender
- **Communities**: anyone can create one. The creator appoints admins from its members; only the creator and admins
  can post and edit posts, everyone else reads and joins. Creators and admins can repost their own books into
  communities they run. Deleting a community deletes its posts
- **Advisor**: chat with RAG search and GigaChat, like/dislike a set of picks, voice input (Web Speech API)
- **For You**: personal recommendations. Every refresh skips books from recent sets and books already rated,
  and samples from the top-ranked pool, so it doesn't repeat itself
- **Add a Book** (linked from Explore): the book goes straight into the catalog CSV and the FAISS index, with an optional link
- **Profile**: my books and communities, name, bio, age, language; download my data; sign out; delete account

Every recommended book links to its own page: the link given in Add Book, or its Open Library work page
(free, no registration, includes the description). Lookups are cached in `web_book_links`.

**Admin console** at `/admin`, designed for desktop, for accounts with the `admin` role:
overview metrics, users (with email and consent date), books, and live settings for GigaChat and the recommender.

**API** under `/api`, documented at `/docs`.
- `POST /api/auth/register`, `POST /api/auth/login` return a bearer **access token** (JWT, 15 minutes) and a
  **refresh token** (30 days). Send `Authorization: Bearer <access_token>`.
- `POST /api/auth/refresh` rotates the pair. Refresh tokens are stored in the database: reusing an old one
  revokes the whole session (theft detection), and `POST /api/auth/logout` revokes it.
- Passwords are hashed with scrypt (PBKDF2-SHA256 where Python's OpenSSL lacks scrypt). Sign-in is throttled
  after 5 failures.

**Personal data.** The privacy policy is at `/privacy` (operator and contact come from `PRIVACY_OPERATOR` and
`PRIVACY_CONTACT`). Consent is required to register and is recorded with its version and date. Bump
`CONSENT_VERSION` in `web/auth.py` when the policy changes, and readers are asked to accept again.
Readers can export their data (`GET /api/me/export`) and delete their account (`DELETE /api/me`), which removes
their account, profile, reactions and history, follows, published books, posts and the communities they created.

**Data.** Accounts, profiles, reactions, recommendation history, tokens, authors, follows, published books,
communities and posts are stored in SQLite (`DB_PATH`, `/data/books.db` in the container).
The catalog is `books.csv`, built from [goodbooks-10k-extended](https://github.com/malcolmosh/goodbooks-10k-extended)
(about 9,900 books with descriptions, genres and ratings; CC BY-SA 4.0) by `scripts/build_dataset.py`.
When the catalog changes size, the FAISS index is rebuilt on startup.

### Running with Docker

```bash
cp .env.example .env      # fill in API_KEY, JWT_SECRET, ADMIN_EMAIL, ADMIN_PASSWORD, PRIVACY_*
docker compose up -d --build
```

Open http://localhost:8000 (reader app), http://localhost:8000/admin (console), http://localhost:8000/docs (API).
The first start downloads the catalog and the search models and builds the index, which takes a few minutes;
the advisor shows "warming up" until it's ready. Everything is kept in the `data` volume.

The Telegram bot can run next to it, sharing the same data: set `BOT_TOKEN` and run `docker compose --profile bot up -d`.

### Development

There is no local Python setup: everything builds and runs in Docker. The dev override mounts `web/`, `src/` and
`scripts/` from the working tree and reloads on changes, with data in the same `data` volume:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose logs -f web
```

Rebuild the image (`--build`) only when `requirements.txt` or the `Dockerfile` changes.
To start from an empty database and catalog: `docker compose down -v`.
