import argparse
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from genereer_rondes import RefreshPlayersResults


DATABASE = "database.db"
DEFAULT_INPUT = "Rondes.txt"

MONTHS = {
    "januari": 1,
    "februari": 2,
    "maart": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "augustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}

RESULT_TYPE_BY_TEXT = {
    "1-0": 1,
    "0-1": 2,
    "rem": 3,
    "0.5-0.5": 3,
    "½-½": 3,
}


@dataclass
class Game:
    white: str
    black: str
    result_type: int
    group_number: int


@dataclass
class RoundData:
    round_number: int
    date: datetime
    season_year: int
    games: list[Game] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)


def clean_text(text: str) -> str:
    return text.replace("\x1a", "").replace("\r\n", "\n")


def normalize_name(name: str) -> str:
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip().casefold()


def parse_date(day: str, month_name: str, year: str) -> datetime:
    return datetime(int(year), MONTHS[month_name.casefold()], int(day))


def season_year_for(date: datetime) -> int:
    return date.year + 1 if date.month >= 8 else date.year


def format_db_date(date: datetime) -> str:
    return f"{date.day}-{date.month}-{date.year}"


def split_names(text: str) -> list[str]:
    text = re.sub(r"\([^)]*\)", "", text)
    return [name.strip() for name in text.replace("\n", " ").split(",") if name.strip()]


def parse_rounds(text: str) -> list[RoundData]:
    text = clean_text(text)
    header = re.compile(
        r"Uitslagen van ronde\s+(\d+)\s+op\s+\w+\s+(\d+)\s+([a-zA-Z]+)\s+(\d{4})",
        re.IGNORECASE,
    )
    matches = list(header.finditer(text))
    rounds: list[RoundData] = []

    for index, match in enumerate(matches):
        block_start = match.end()
        block_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[block_start:block_end]

        date = parse_date(match.group(2), match.group(3), match.group(4))
        round_data = RoundData(
            round_number=int(match.group(1)),
            date=date,
            season_year=season_year_for(date),
        )

        for group_number, group_name in ((1, "I"), (2, "II")):
            group_match = re.search(
                rf"Intern,\s*Groep\s+{group_name}\s*(.*?)(?=Intern,\s*Groep|Extern:|Afwezig:|-{{20,}}|\Z)",
                block,
                re.IGNORECASE | re.DOTALL,
            )
            if not group_match:
                continue

            for line in group_match.group(1).splitlines():
                game_match = re.match(
                    r"^\s*(.*?)\s+-\s+(.*?)\s+(1-0|0-1|rem|0\.5-0\.5|½-½)\s*$",
                    line.strip(),
                    re.IGNORECASE,
                )
                if not game_match:
                    continue

                result_text = game_match.group(3).lower()
                round_data.games.append(
                    Game(
                        white=game_match.group(1).strip(),
                        black=game_match.group(2).strip(),
                        result_type=RESULT_TYPE_BY_TEXT[result_text],
                        group_number=group_number,
                    )
                )

        external_match = re.search(r"Extern:\s*(.*?)(?=Afwezig:|-{20,}|\Z)", block, re.DOTALL | re.IGNORECASE)
        absent_match = re.search(r"Afwezig:\s*(.*?)(?=-{20,}|\Z)", block, re.DOTALL | re.IGNORECASE)
        if external_match:
            round_data.external = split_names(external_match.group(1))
        if absent_match:
            round_data.absent = split_names(absent_match.group(1))

        rounds.append(round_data)

    return rounds


def load_players(cur: sqlite3.Cursor) -> dict[str, tuple[str, int]]:
    rows = cur.execute("SELECT Id, Name, GroupNumber FROM Players").fetchall()
    return {normalize_name(name): (str(player_id), group_number) for player_id, name, group_number in rows}


def get_or_create_player(
    cur: sqlite3.Cursor,
    players: dict[str, tuple[str, int]],
    name: str,
    group_number: int,
    create_missing: bool,
    missing: set[str],
) -> tuple[str, int] | None:
    key = normalize_name(name)
    if key in players:
        return players[key]

    missing.add(name)
    if not create_missing:
        return None

    next_id = str(cur.execute("SELECT COALESCE(MAX(CAST(Id AS INTEGER)), 0) + 1 FROM Players").fetchone()[0])
    cur.execute(
        """
        INSERT INTO Players (Id, Name, GroupNumber, Active, Rating, Type)
        VALUES (?, ?, ?, 0, NULL, 'Imported')
        """,
        (next_id, name, group_number),
    )
    players[key] = (next_id, group_number)
    return players[key]


def get_or_create_competition(
    cur: sqlite3.Cursor, name: str, year: int, number_of_rounds: int
) -> int:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS Competitions(
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            Name TEXT NOT NULL,
            Year INTEGER NOT NULL,
            NumberOfRounds INTEGER NOT NULL,
            NumberOfNonCompete INTEGER NOT NULL DEFAULT 0,
            Active BOOLEAN NOT NULL DEFAULT 1,
            Last_Update DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    round_columns = {
        row[1] for row in cur.execute("PRAGMA table_info(Rounds)").fetchall()
    }
    if "CompetitionId" not in round_columns:
        cur.execute("ALTER TABLE Rounds ADD COLUMN CompetitionId INTEGER")

    competition_count = cur.execute("SELECT COUNT(*) FROM Competitions").fetchone()[0]
    legacy_rounds = cur.execute(
        "SELECT COUNT(*), MIN(Year) FROM Rounds WHERE CompetitionId IS NULL"
    ).fetchone()
    if competition_count == 0 and legacy_rounds[0] > 0:
        legacy_year = legacy_rounds[1] or year
        cur.execute(
            """
            INSERT INTO Competitions
                (Name, Year, NumberOfRounds, NumberOfNonCompete)
            VALUES (?, ?, ?, 0)
            """,
            (f"Competitie {legacy_year}", legacy_year, legacy_rounds[0]),
        )

    default_competition = cur.execute(
        "SELECT Id FROM Competitions ORDER BY Id LIMIT 1"
    ).fetchone()
    if default_competition:
        cur.execute(
            "UPDATE Rounds SET CompetitionId = ? WHERE CompetitionId IS NULL",
            (default_competition[0],),
        )

    row = cur.execute(
        "SELECT Id FROM Competitions WHERE Name = ? AND Year = ? ORDER BY Id LIMIT 1",
        (name, year),
    ).fetchone()
    if row:
        return row[0]

    cur.execute(
        """
        INSERT INTO Competitions
            (Name, Year, NumberOfRounds, NumberOfNonCompete)
        VALUES (?, ?, ?, 0)
        """,
        (name, year, number_of_rounds),
    )
    return cur.lastrowid


def get_or_create_round(
    cur: sqlite3.Cursor, round_data: RoundData, competition_id: int
) -> int:
    row = cur.execute(
        """SELECT Id FROM Rounds
           WHERE CompetitionId = ? AND RoundNumber = ?""",
        (competition_id, round_data.round_number),
    ).fetchone()
    if row:
        round_id = row[0]
        cur.execute(
            "UPDATE Rounds SET Date = ?, Played = 1 WHERE Id = ?",
            (format_db_date(round_data.date), round_id),
        )
        return round_id

    round_id = cur.execute("SELECT COALESCE(MAX(Id), 0) + 1 FROM Rounds").fetchone()[0]
    cur.execute(
        """
        INSERT INTO Rounds
            (Id, Period, RoundNumber, Year, Date, Played, CompetitionId)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """,
        (
            round_id,
            1 if round_data.round_number <= 10 else 2,
            round_data.round_number,
            round_data.season_year,
            f"{round_data.date.day}-{round_data.date.month}-{round_data.date.year}",
            competition_id,
        ),
    )
    return round_id


def result_id_for_reason(cur: sqlite3.Cursor, name: str, group_number: int) -> int | None:
    row = cur.execute(
        "SELECT Id FROM Results WHERE Name = ? AND GroupNumber = ? ORDER BY Id LIMIT 1",
        (name, group_number),
    ).fetchone()
    return row[0] if row else None


def import_rounds(args: argparse.Namespace) -> int:
    rounds = parse_rounds(Path(args.input).read_text(encoding=args.encoding))
    if not rounds:
        raise SystemExit(f"No rounds found in {args.input}")

    conn = sqlite3.connect(args.database)
    cur = conn.cursor()
    competition_year = rounds[0].season_year
    competition_name = args.competition_name or f"Import {competition_year}"
    competition_id = get_or_create_competition(
        cur, competition_name, competition_year, len(rounds)
    )
    players = load_players(cur)
    missing: set[str] = set()

    total_games = 0
    total_external = 0
    total_absent = 0

    try:
        for round_data in rounds:
            round_id = get_or_create_round(cur, round_data, competition_id)
            cur.execute("DELETE FROM Pairings WHERE RoundId = ?", (round_id,))
            cur.execute("DELETE FROM Present WHERE RoundId = ?", (round_id,))

            for game in round_data.games:
                white = get_or_create_player(cur, players, game.white, game.group_number, args.create_missing_players, missing)
                black = get_or_create_player(cur, players, game.black, game.group_number, args.create_missing_players, missing)
                if not white or not black:
                    continue

                cur.execute(
                    """
                    INSERT INTO Pairings (PlayerId1, PlayerId2, RoundId, GroupNumber, ResultsType)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (white[0], black[0], round_id, game.group_number, game.result_type),
                )
                cur.execute(
                    "INSERT OR REPLACE INTO Present (PlayerId, RoundId, Present, ReasonAbsentId) VALUES (?, ?, 1, NULL)",
                    (white[0], round_id),
                )
                cur.execute(
                    "INSERT OR REPLACE INTO Present (PlayerId, RoundId, Present, ReasonAbsentId) VALUES (?, ?, 1, NULL)",
                    (black[0], round_id),
                )
                total_games += 1

            for reason_name, names in (("Extern", round_data.external), ("Absent", round_data.absent)):
                for name in names:
                    player = get_or_create_player(cur, players, name, 1, args.create_missing_players, missing)
                    if not player:
                        continue

                    reason_id = result_id_for_reason(cur, reason_name, player[1])
                    cur.execute(
                        "INSERT OR REPLACE INTO Present (PlayerId, RoundId, Present, ReasonAbsentId) VALUES (?, ?, 0, ?)",
                        (player[0], round_id, reason_id),
                    )
                    if reason_name == "Extern":
                        total_external += 1
                    else:
                        total_absent += 1

            cur.execute("UPDATE Rounds SET Played = 1 WHERE Id = ?", (round_id,))

        if args.commit:
            conn.commit()
            RefreshPlayersResults(args.database)
        else:
            conn.rollback()
    finally:
        conn.close()

    print(f"Rounds parsed: {len(rounds)}")
    print(f"Games imported: {total_games}")
    print(f"External entries imported: {total_external}")
    print(f"Absent entries imported: {total_absent}")
    if missing:
        print("Missing players:")
        for name in sorted(missing):
            print(f"  - {name}")
        if not args.create_missing_players:
            print("Run again with --create-missing-players to insert them as inactive imported players.")
    if not args.commit:
        print("Dry run only. Add --commit to write changes.")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import internal round results from Rondes.txt into database.db.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Text file with round results.")
    parser.add_argument("--database", default=DATABASE, help="SQLite database path.")
    parser.add_argument("--encoding", default="utf-8-sig", help="Input file encoding.")
    parser.add_argument(
        "--competition-name",
        help="Competition name. Defaults to 'Import <season year>'.",
    )
    parser.add_argument("--create-missing-players", action="store_true", help="Create unknown players as inactive.")
    parser.add_argument("--commit", action="store_true", help="Write changes. Without this the script is a dry run.")
    return import_rounds(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
