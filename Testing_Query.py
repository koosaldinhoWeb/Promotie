import sqlite3

conn = sqlite3.connect("database.db")
cur = conn.cursor()
new_round_id = 11
# Voorbeeldquery: alle rondes van speler met ID 12
# Stap 3: SQLite verbinding
conn = sqlite3.connect("database.db")
cur = conn.cursor()

# Stap 4: Tabellen aanmaken
# cur.execute('''

# CREATE TABLE IF NOT EXISTS players (
#     id INTEGER PRIMARY KEY,
#     name TEXT NOT NULL,
#     voorl TEXT NOT NULL,
#     rating INTEGER,
#     type TEXT NOT NULL,
#     ExternTeam TEXT,
#     Status TEXT NOT NULL,
#     Groep TEXT NOT NULL,
#     from_date TEXT,
#     until_date TEXT
# )
# ''')

cur.execute('''
            CREATE TABLE rondes (
    id INTEGER PRIMARY KEY,
    datum TEXT,
    Periode TEXT,
    JaartalStart INTEGER
)           
            ''')

rows = cur.fetchall()

for row in rows:
    print(row)

conn.close()