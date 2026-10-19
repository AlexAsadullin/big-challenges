"""Создаёт небольшой books.csv в формате, который ожидают rag.py и recsys.py.

    python scripts/make_sample_csv.py [путь]   # по умолчанию books.csv
"""
import sys

import pandas as pd

BOOKS = [
    ("1984", "George Orwell", "Dystopian, Fiction", "A totalitarian state watches every citizen as one clerk quietly rebels against Big Brother.", 4.2),
    ("Brave New World", "Aldous Huxley", "Dystopian, Science Fiction", "A future society engineered for happiness and stability at the cost of freedom and individuality.", 4.0),
    ("Fahrenheit 451", "Ray Bradbury", "Dystopian, Science Fiction", "A fireman whose job is burning books begins to question a society that fears ideas.", 4.0),
    ("The Master and Margarita", "Mikhail Bulgakov", "Classic, Fantasy", "The Devil visits Soviet Moscow while a novelist writes about Pontius Pilate.", 4.4),
    ("Crime and Punishment", "Fyodor Dostoevsky", "Classic, Psychological Fiction", "A poor student commits a murder and struggles with guilt and redemption in St. Petersburg.", 4.3),
    ("War and Peace", "Leo Tolstoy", "Classic, Historical Fiction", "Aristocratic families live through the Napoleonic wars in an epic of Russian society.", 4.1),
    ("Anna Karenina", "Leo Tolstoy", "Classic, Romance", "A married aristocrat's affair with an officer collides with the rules of high society.", 4.1),
    ("The Brothers Karamazov", "Fyodor Dostoevsky", "Classic, Philosophy", "Three brothers and their father's murder frame a debate about faith, doubt and morality.", 4.4),
    ("Dune", "Frank Herbert", "Science Fiction", "A noble heir on a desert planet becomes the center of a struggle for the most valuable substance in the universe.", 4.3),
    ("Foundation", "Isaac Asimov", "Science Fiction", "A mathematician predicts the fall of a galactic empire and plans to shorten the dark age that follows.", 4.2),
    ("Roadside Picnic", "Arkady and Boris Strugatsky", "Science Fiction", "Stalkers enter a forbidden zone left by alien visitors to smuggle out strange artifacts.", 4.2),
    ("Solaris", "Stanislaw Lem", "Science Fiction, Philosophy", "Scientists studying an ocean-covered planet are confronted with their own memories made flesh.", 4.0),
    ("The Left Hand of Darkness", "Ursula K. Le Guin", "Science Fiction", "An envoy to a planet of ambisexual people navigates politics, ice and trust.", 4.1),
    ("Neuromancer", "William Gibson", "Science Fiction, Cyberpunk", "A washed-up hacker is hired for one last job inside a global computer network.", 3.9),
    ("The Hitchhiker's Guide to the Galaxy", "Douglas Adams", "Science Fiction, Humor", "Seconds before Earth is demolished, an ordinary man is rescued by an alien researching a guidebook.", 4.2),
    ("The Hobbit", "J.R.R. Tolkien", "Fantasy, Adventure", "A homely hobbit joins thirteen dwarves on a quest to reclaim their mountain from a dragon.", 4.3),
    ("The Fellowship of the Ring", "J.R.R. Tolkien", "Fantasy, Adventure", "A young hobbit sets out to destroy a ring of terrible power before the Dark Lord reclaims it.", 4.4),
    ("A Wizard of Earthsea", "Ursula K. Le Guin", "Fantasy", "A gifted young wizard releases a shadow into the world and must hunt it down.", 4.0),
    ("Harry Potter and the Philosopher's Stone", "J.K. Rowling", "Fantasy, Young Adult", "An orphan learns he is a wizard and begins his first year at a school of magic.", 4.5),
    ("The Name of the Wind", "Patrick Rothfuss", "Fantasy", "A legendary figure recounts his youth as a gifted, poor student at a university of arcane arts.", 4.5),
    ("Pride and Prejudice", "Jane Austen", "Classic, Romance", "Elizabeth Bennet and Mr. Darcy overcome pride and misjudgment in Regency England.", 4.3),
    ("Jane Eyre", "Charlotte Bronte", "Classic, Romance", "An orphaned governess falls in love with her brooding employer, who hides a secret.", 4.1),
    ("The Great Gatsby", "F. Scott Fitzgerald", "Classic, Fiction", "A mysterious millionaire throws lavish parties to win back the woman he loves.", 3.9),
    ("One Hundred Years of Solitude", "Gabriel Garcia Marquez", "Magical Realism, Classic", "Seven generations of the Buendia family rise and fall in the mythical town of Macondo.", 4.1),
    ("The Little Prince", "Antoine de Saint-Exupery", "Classic, Children", "A pilot stranded in the desert meets a small prince who travels between tiny planets.", 4.3),
    ("Sherlock Holmes: A Study in Scarlet", "Arthur Conan Doyle", "Mystery, Classic", "Dr. Watson meets an eccentric detective and helps solve a murder rooted in a distant past.", 4.1),
    ("Murder on the Orient Express", "Agatha Christie", "Mystery", "A detective trapped on a snowbound train must find a killer among the passengers.", 4.2),
    ("The Girl with the Dragon Tattoo", "Stieg Larsson", "Mystery, Thriller", "A journalist and a hacker investigate a decades-old disappearance in a wealthy family.", 4.1),
    ("Gone Girl", "Gillian Flynn", "Thriller, Mystery", "When a wife vanishes on their anniversary, her husband becomes the prime suspect.", 4.1),
    ("The Shining", "Stephen King", "Horror", "A family caretaking an isolated hotel for the winter is stalked by its violent past.", 4.3),
    ("Dracula", "Bram Stoker", "Horror, Classic", "A group led by Van Helsing hunts the ancient vampire Count Dracula.", 4.0),
    ("Sapiens", "Yuval Noah Harari", "Nonfiction, History", "A brief history of humankind from the cognitive revolution to the present.", 4.4),
    ("Thinking, Fast and Slow", "Daniel Kahneman", "Nonfiction, Psychology", "How two systems of thought shape our judgments and the biases that follow.", 4.2),
    ("A Brief History of Time", "Stephen Hawking", "Nonfiction, Science", "An accessible tour of the universe, from the big bang to black holes.", 4.2),
    ("Atomic Habits", "James Clear", "Nonfiction, Self-Help", "Small changes compound into remarkable results through better systems for habits.", 4.4),
    ("The Catcher in the Rye", "J.D. Salinger", "Classic, Young Adult", "A teenager wanders New York after being expelled from prep school.", 3.8),
    ("To Kill a Mockingbird", "Harper Lee", "Classic, Fiction", "A girl in the American South watches her father defend a Black man accused of a crime.", 4.3),
    ("The Hunger Games", "Suzanne Collins", "Young Adult, Dystopian", "A girl volunteers to take her sister's place in a televised fight to the death.", 4.3),
    ("The Martian", "Andy Weir", "Science Fiction, Adventure", "An astronaut stranded on Mars must engineer his survival until rescue can arrive.", 4.4),
    ("Three Comrades", "Erich Maria Remarque", "Classic, Romance", "Three friends who survived the war run a garage in Weimar Germany as one of them falls in love.", 4.4),
]


def main(path):
    df = pd.DataFrame(BOOKS, columns=["Title", "Author", "Genre", "Description", "Rating"])
    df.insert(0, "id", range(1, len(df) + 1))
    df["Combined"] = df.apply(
        lambda b: f"{b.Title} by {b.Author}. Genre: {b.Genre}. Description: {b.Description}", axis=1
    )
    df["like_count"] = 0
    df["view_count"] = 0
    df["dislike_count"] = 0
    df["mean_age"] = 30.0
    df.to_csv(path, index=False)
    print(f"{len(df)} книг записано в {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "books.csv")
