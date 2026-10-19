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

1. Установите зависимости:
   ```bash
   pip install -r requirements.txt   ```

## Web app

`web/` contains a FastAPI app built on the same `db.py`, `rag.py` and `recsys.py` as the bot.

**Reader app** at `/`, designed for phones. Readers create an account (email, password, age, language)
and must accept the privacy policy to sign up.
- **Advisor**: chat with RAG search and GigaChat, like/dislike a set of picks, voice input (Web Speech API)
- **For You**: personal recommendations. Every refresh skips books from recent sets and books already rated,
  and samples from the top-ranked pool, so it doesn't repeat itself
- **Add Book**: the book goes straight into the CSV and the FAISS index, with an optional link
- **Profile**: name, age, language; download my data; sign out; delete account

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
their account, profile, reactions and history.

**Data.** Accounts, profiles, reactions, recommendation history and tokens are stored in SQLite (`DB_PATH`).
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

### Running locally

```bash
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -r requirements.txt
python scripts/build_dataset.py books.csv     # or scripts/make_sample_csv.py for a 40-book offline sample
export API_KEY=... JWT_SECRET=... ADMIN_EMAIL=... ADMIN_PASSWORD=...
uvicorn web.app:app --port 8000
```

The bot still runs as before: `cd src && python bot.py`.
