"""Builds books.csv from goodbooks-10k-extended: ~10,000 popular books with descriptions, genres and ratings.

    python scripts/build_dataset.py [path]   # defaults to books.csv

Source: https://github.com/malcolmosh/goodbooks-10k-extended (based on goodbooks-10k by Zygmunt Zając,
CC BY-SA 4.0). Delete faiss_index.index after rebuilding so the search index is rebuilt for the new books.
"""
import ast
import re
import sys
import urllib.request
from io import StringIO

import pandas as pd

SOURCE = "https://raw.githubusercontent.com/malcolmosh/goodbooks-10k-extended/master/books_enriched.csv"
# Goodreads shelves that say nothing about the content
NOISE_GENRES = {"books", "ebooks", "audiobook", "audiobooks", "kindle", "owned", "to-read", "favorites", "default"}


# Goodreads cataloguing notes at the start of a description, e.g.
# "Alternate cover edition of ISBN 9780679783268", "Librarian's note: An alternate cover edition can be found here"
LEADING_NOTES = [
    re.compile(r"^(?:an |this is an? )?alternate cover[^.]*?(?:ISBN(?:13)?[:\s]*[\dX-]{9,17}(?:\s*\(ISBN13:\s*\d{13}\))?|here|\.)\s*", re.I),
    re.compile(r"^librarian'?s note:.*?(?:here|\.)\s*", re.I),
]


def clean_description(text):
    if not isinstance(text, str):
        return ""
    text = re.sub(r"<[^>]+>", " ", text).strip()
    for _ in range(3):  # notes sometimes stack
        for note in LEADING_NOTES:
            text = note.sub("", text).lstrip(" .:-")
    # The source glues sentences together: "FORTUNE.LOSING", "TV.Sixteen"
    text = re.sub(r"([.!?…])(?=[\"“]?[A-Z][A-Za-z])", r"\1 ", text)
    return " ".join(text.split())


def parse_list(value):
    try:
        items = ast.literal_eval(value) if isinstance(value, str) else []
    except (ValueError, SyntaxError):
        items = []
    # A few records wrap the names in stray brackets: ['[George Orwell', ..., 'George Packer]']
    return [str(i).strip(" []") for i in items if str(i).strip(" []")]


def genre_label(shelf):
    return " ".join(part.capitalize() for part in shelf.replace("_", "-").split("-"))


def main(path):
    print(f"Downloading {SOURCE} …")
    with urllib.request.urlopen(SOURCE, timeout=120) as res:
        raw = pd.read_csv(StringIO(res.read().decode("utf-8")))

    df = pd.DataFrame({
        "Title": raw["title"].astype(str).str.strip(),
        # Goodreads lists the author first, then translators, illustrators and introduction writers
        "Author": raw["authors"].map(lambda a: (parse_list(a) or [""])[0]),
        "Genre": raw["genres"].map(lambda g: ", ".join(
            [genre_label(s) for s in parse_list(g) if s.lower() not in NOISE_GENRES][:3])),
        "Description": raw["description"].map(clean_description),
        "Rating": raw["average_rating"].round(2),
        "ratings_count": raw["ratings_count"],
    })
    df = df[(df["Description"].str.len() >= 40) & (df["Author"] != "") & (df["Title"] != "")]
    df = df.drop_duplicates(subset=["Title", "Author"]).sort_values("ratings_count", ascending=False)
    df = df.drop(columns="ratings_count").reset_index(drop=True)

    df.insert(0, "id", range(1, len(df) + 1))
    df["Combined"] = df.apply(
        lambda b: f"{b.Title} by {b.Author}. Genre: {b.Genre}. Description: {b.Description}", axis=1
    )
    df["like_count"] = 0
    df["view_count"] = 0
    df["dislike_count"] = 0
    df["mean_age"] = 30.0
    df.to_csv(path, index=False)
    print(f"Wrote {len(df)} books to {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "books.csv")
