import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "...")
DB_PATH = os.getenv("DB_PATH", "books.db")
CSV_PATH = os.getenv("CSV_PATH", "books.csv")
INDEX_PATH = os.getenv("INDEX_PATH", "faiss_index.index")
API_KEY = os.getenv("API_KEY", "...")
