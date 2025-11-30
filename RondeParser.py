import sqlite3
import re
from collections import namedtuple

# Stap 1: Tekst inlezen
with open("rondes.txt", encoding="utf-8") as f:
    all_text = f.read()

PlayerRound = namedtuple("PlayerRound", ["round_id", "player_id", "group_id", "opponent_id", "color", "result_id", "present"])

def result_to_id(result, player_color):
    if result == "1-0":
        return 1 if player_color == "white" else 3
    elif result == "0-1":
        return 3 if player_color == "white" else 1
    elif result == "0.5-0.5":
        return 2
    return None

def generate_round_entries(matches, round_id, group_id, player_id_map):
    entries = []
    for white_name, black_name, result in matches:
        white_id = player_id_map.get(white_name)
        black_id = player_id_map.get(black_name)
        if not white_id or not black_id:
            continue
        if result is None:
            entries.append(PlayerRound(round_id, white_id, group_id, None, "white", None, 1))
        else:
            entries.append(PlayerRound(round_id, white_id, group_id, black_id, "white", result_to_id(result, "white"), 1))
            entries.append(PlayerRound(round_id, black_id, group_id, white_id, "black", result_to_id(result, "black"), 1))
    return entries

def parse_round_from_text(round_id: int, text: str) -> dict:
    matches = re.findall(r"^\s*(.*?)\s*-\s*(.*?)\s+(1-0|0-1|rem)", text, re.MULTILINE)
    parsed_matches = [(w.strip(), b.strip(), "0.5-0.5" if r == "rem" else r) for w, b, r in matches]

    afwezig = re.findall(r"Afwezig:\s*(.*?)(?:-+\s*$|\Z)", text, re.DOTALL)
    extern = re.findall(r"Extern:\s*(.*?)Afwezig:", text, re.DOTALL)

    afw_list = re.findall(r"([A-Z]\. [\w\(\)\.]+)", afwezig[0]) if afwezig else []
    ext_list = re.findall(r"([A-Z]\. [\w\(\)\.]+)", extern[0]) if extern else []

    return {
        "matches": parsed_matches,
        "afwezig": [n.strip() for n in afw_list],
        "extern": [n.strip() for n in ext_list]
    }

# Stap 2: Spelers verzamelen
all_players = set()
round_chunks = re.split(r"Uitslagen van ronde (\d+) op.*?\n[-—]{3,}", all_text)

for i in range(1, len(round_chunks), 2):
    text_block = round_chunks[i + 1]
    data = parse_round_from_text(int(round_chunks[i]), text_block)
    for m in data["matches"]:
        all_players.add(m[0])
        all_players.add(m[1])
    all_players.update(data["afwezig"])
    all_players.update(data["extern"])

player_id_map = {name: idx + 1 for idx, name in enumerate(sorted(all_players))}

# Stap 1: speler → lijst van groepen (om dominante groep te bepalen)
from collections import defaultdict, Counter

player_group_history = defaultdict(list)

for i in range(1, len(round_chunks), 2):
    text_block = round_chunks[i + 1]
    round_id = int(round_chunks[i])
    
    # Vind posities van groepsblokken
    matches_i = re.findall(r"Intern, Groep I(.*?)(?:Intern, Groep II|Extern:|Afwezig:)", text_block, re.DOTALL)
    matches_ii = re.findall(r"Intern, Groep II(.*?)(?:Extern:|Afwezig:)", text_block, re.DOTALL)

    for match_text, group in [(matches_i, "I"), (matches_ii, "II")]:
        if not match_text:
            continue
        lines = re.findall(r"\s*(.*?)\s*-\s*(.*?)\s+(?:1-0|0-1|rem)", match_text[0])
        for white, black in lines:
            player_group_history[white.strip()].append(group)
            player_group_history[black.strip()].append(group)

# Stap 2: Dominante groep per speler kiezen
player_to_group = {}
for player, group_list in player_group_history.items():
    most_common = Counter(group_list).most_common(1)
    player_to_group[player] = most_common[0][0] if most_common else None



# Stap 3: SQLite verbinding
conn = sqlite3.connect("database.db")
cur = conn.cursor()

# Stap 4: Tabellen aanmaken
cur.execute('''
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    voorl TEXT NOT NULL,
    rating INTEGER,
    type TEXT NOT NULL,
    ExternTeam TEXT,
    Status TEXT NOT NULL,
    Groep TEXT NOT NULL,
    from_date TEXT,
    until_date TEXT
)
''')

cur.execute('''
CREATE TABLE IF NOT EXISTS player_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER,
    player_id INTEGER,
    group_id TEXT,
    opponent_id INTEGER,
    color TEXT,
    result_id INTEGER,
    present INTEGER
)
''')

# Stap 5: Leegmaken en vullen van 'players'
cur.execute("DELETE FROM players")
for name, pid in player_id_map.items():
    group = player_to_group.get(name)
    cur.execute("INSERT INTO players (id, name, group_name) VALUES (?, ?, ?)", (pid, name, group))


# Stap 6: Leegmaken en vullen van 'player_rounds'
cur.execute("DELETE FROM player_rounds")

for i in range(1, len(round_chunks), 2):
    round_id = int(round_chunks[i])
    text_block = round_chunks[i + 1]
    data = parse_round_from_text(round_id, text_block)

    all_rounds = []
    all_rounds += generate_round_entries(data["matches"], round_id, None, player_id_map)

    for name in data["afwezig"]:
        pid = player_id_map.get(name)
        if pid:
            all_rounds.append(PlayerRound(round_id, pid, None, None, None, 5, 0))

    for name in data["extern"]:
        pid = player_id_map.get(name)
        if pid:
            all_rounds.append(PlayerRound(round_id, pid, None, None, None, 4, 1))

    for pr in all_rounds:
        cur.execute('''
            INSERT INTO player_rounds (round_id, player_id, group_id, opponent_id, color, result_id, present)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (pr.round_id, pr.player_id, pr.group_id, pr.opponent_id, pr.color, pr.result_id, pr.present))

# Commit & sluit
conn.commit()
conn.close()

print("✅ Alle spelers en rondegegevens (1–10) zijn succesvol opgeslagen in de database.")
