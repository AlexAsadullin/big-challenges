import os
import numpy as np
import pandas as pd

# Веса скоринга; можно менять на лету через атрибут weights
DEFAULT_WEIGHTS = {
    "like": 0.7,
    "view": 0.3,
    "dislike": 1.0,
    "age": 0.5,
    "age_scale": 10,
    "language": 0.1,
    "language_like": 2,
}


class RecommendationSystem:
    def __init__(self, db, dataset_path):
        self.db = db
        self.dataset_path = dataset_path
        self.top_k = 5
        self.weights = dict(DEFAULT_WEIGHTS)
        self.books_df = None
        self.books_df_mtime = None
        self.book_ids = []
        self.book_embeddings = None
        self.load_books()
        self.load_embeddings()

    def load_books(self):
        """Загружаем данные о книгах из CSV с кешированием по времени модификации файла."""
        current_mtime = os.path.getmtime(self.dataset_path)
        if self.books_df is None or current_mtime != self.books_df_mtime:
            self.books_df = pd.read_csv(self.dataset_path)
            self.books_df.fillna(
                {"mean_age": 30, "like_count": 0, "view_count": 0, "dislike_count": 0},
                inplace=True,
            )
            self.books_df_mtime = current_mtime
        return self.books_df

    def load_embeddings(self):
        """Загружаем эмбеддинги книг из базы данных."""
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT book_id, embedding FROM embeddings")
        embeddings_data = [(row[0], np.frombuffer(row[1], dtype=np.float32)) for row in cursor.fetchall()]
        if embeddings_data:
            self.book_ids, embeddings = zip(*embeddings_data)
            self.book_ids = list(self.book_ids)
            self.book_embeddings = np.stack(embeddings, axis=0)
        else:
            self.book_ids = []
            self.book_embeddings = np.array([])  

    def add_book(self, book_id, embedding):
        """Добавляет новую книгу в систему рекомендаций."""
        self.book_ids.append(book_id)
        if self.book_embeddings.size == 0:
            self.book_embeddings = embedding[np.newaxis, :]
        else:
            self.book_embeddings = np.vstack([self.book_embeddings, embedding])
        self.load_books()

    def recommend(self, user_id, exclude=(), pool_size=40, rng=None):
        """Подборка из top_k книг.

        exclude — id книг, которые не надо показывать (уже показанные, оценённые);
        pool_size — из скольких лучших книг случайно выбирается подборка, чтобы
        «Обновить» давало новые книги; 0 или меньше top_k — строгий топ.
        """
        w = self.weights
        meta = self.db.get_user_metadata(user_id)
        user_language = meta.get("language") or "ru"
        user_age = meta.get("age") or 30

        books = self.load_books()
        # Активность читателей с тем же языком: один запрос вместо двух на каждую книгу
        lang = self.db.get_language_action_counts(user_language)
        lang_views = books["id"].map(lambda b: lang.get((b, "view"), 0)).to_numpy(dtype=np.float32)
        lang_likes = books["id"].map(lambda b: lang.get((b, "like"), 0)).to_numpy(dtype=np.float32)
        language_scores = (lang_likes * w["language_like"] + lang_views) * w["language"]
        age_scores = np.exp(-np.abs(user_age - books["mean_age"].to_numpy(dtype=np.float32)) / w["age_scale"])

        user_embedding = meta.get("profile_embedding")
        if user_embedding is not None and self.book_embeddings.size > 0:
            # Теплый старт: близость к профилю пользователя
            emb_by_id = dict(zip(self.book_ids, self.book_embeddings))
            user_vec = user_embedding / (np.linalg.norm(user_embedding) or 1)
            embedding_scores = np.array([
                float(np.dot(emb_by_id[b], user_vec) / (np.linalg.norm(emb_by_id[b]) or 1)) if b in emb_by_id else 0.0
                for b in books["id"]
            ])
            scores = embedding_scores + language_scores + age_scores * w["age"]
        else:
            # Холодный старт: популярность
            scores = (
                books["like_count"].to_numpy(dtype=np.float32) * w["like"]
                + books["view_count"].to_numpy(dtype=np.float32) * w["view"]
                - books["dislike_count"].to_numpy(dtype=np.float32) * w["dislike"]
                + age_scores * w["age"]
                + language_scores
            )

        order = np.argsort(-scores, kind="stable")
        excluded = set(int(b) for b in exclude)
        allowed = [i for i in order if int(books["id"].iat[i]) not in excluded]
        if len(allowed) < self.top_k:  # всё уже показано — начинаем по кругу
            allowed = list(order)

        pool = allowed[: max(pool_size, self.top_k)]
        if len(pool) > self.top_k:
            # Чем выше книга в рейтинге, тем вероятнее она попадёт в подборку
            rng = rng or np.random.default_rng()
            weights = np.exp(-np.arange(len(pool)) / (len(pool) / 3))
            picked = rng.choice(len(pool), size=self.top_k, replace=False, p=weights / weights.sum())
            pool = [pool[i] for i in sorted(picked)]
        top = pool[: self.top_k]
        return [(books.iloc[i].to_dict(), int(books["id"].iat[i])) for i in top]
