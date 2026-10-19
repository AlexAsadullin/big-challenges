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

`web/` contains a FastAPI app built on the same `db.py`, `rag.py` and `recsys.py` as the bot. It has two interfaces:

**Reader app** at `/`, designed for phones:
- **Advisor**: chat with RAG search and GigaChat, like/dislike a set of picks, voice input (Web Speech API)
- **For You**: personal recommendations from `recsys.py`
- **Add Book**: the book goes straight into the CSV and the FAISS index
- **Profile**: name, age and language, which the recommender uses

**Admin console** at `/admin`, designed for desktop, password-protected:
- **Overview**: users, activity, reactions, genres, ages, languages, top books
- **Users**: every web and Telegram user, with likes, dislikes and recent recommendations
- **Books**: the catalog with search, genre filters and per-book stats
- **Settings**: GigaChat temperature/top-p, books per prompt, history length, system prompt, and the recommender weights. Changes apply to the running app without a restart.

Web readers are identified by a cookie and get negative `user_id`s, so they never collide with Telegram ids.
RAG models load in the background. Until they're ready (or if `API_KEY` is missing), For You, the profile
and the admin console work, while chat and adding books are unavailable.

### Running

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt
python scripts/make_sample_csv.py books.csv    # if you don't have your own books.csv
export API_KEY="<GigaChat key>"
export ADMIN_PASSWORD="<admin password>"       # the admin console is disabled without it
export SESSION_SECRET="<random string>"        # optional: keeps admins signed in across restarts
uvicorn web.app:app --port 8000
```

Open http://127.0.0.1:8000 for the reader app and http://127.0.0.1:8000/admin for the console.
`BOT_TOKEN`, `DB_PATH`, `CSV_PATH` and `INDEX_PATH` can also be set as environment variables.
The bot still runs as before: `cd src && python bot.py`.
