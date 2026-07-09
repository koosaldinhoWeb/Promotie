import os
import sqlite3
from datetime import datetime

from flask import abort, request, session


DATABASE = os.environ.get("DATABASE", "database.db")

def ensure_competition_schema():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
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
    if competition_count == 0:
        settings = dict(
            cur.execute(
                "SELECT Name, Value FROM Settings WHERE Name IN "
                "('NumberOfPeriodRounds', 'NumberOfNonCompete', 'Year')"
            ).fetchall()
        )
        round_count = cur.execute("SELECT COUNT(*) FROM Rounds").fetchone()[0]
        year_row = cur.execute("SELECT MIN(Year) FROM Rounds").fetchone()
        year = int(settings.get("Year") or (year_row[0] if year_row else 0) or datetime.now().year)
        number_of_rounds = int(settings.get("NumberOfPeriodRounds") or round_count or 1)
        non_compete = int(settings.get("NumberOfNonCompete") or 0)
        cur.execute(
            """
            INSERT INTO Competitions
                (Name, Year, NumberOfRounds, NumberOfNonCompete)
            VALUES (?, ?, ?, ?)
            """,
            (f"Competitie {year}", year, number_of_rounds, non_compete),
        )

    default_competition_id = cur.execute(
        "SELECT Id FROM Competitions ORDER BY Id LIMIT 1"
    ).fetchone()[0]
    cur.execute(
        "UPDATE Rounds SET CompetitionId = ? WHERE CompetitionId IS NULL",
        (default_competition_id,),
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_rounds_competition ON Rounds(CompetitionId, Id)"
    )
    conn.commit()
    conn.close()


def ensure_competition_settings_schema():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS CompetitionSettings(
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            CompetitionId INTEGER NOT NULL UNIQUE,
            Name TEXT NOT NULL,
            SettingsType TEXT NOT NULL
                CHECK(SettingsType IN ('swiss', 'percentage')),
            Last_Update DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (CompetitionId) REFERENCES Competitions(Id)
        );

        CREATE TABLE IF NOT EXISTS CompetitionGroupPoints(
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            CompetitionSettingsId INTEGER NOT NULL,
            GroupNumber INTEGER NOT NULL,
            WinPoints REAL NOT NULL,
            DrawPoints REAL NOT NULL,
            LossPoints REAL NOT NULL,
            Last_Update DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(CompetitionSettingsId, GroupNumber),
            FOREIGN KEY (CompetitionSettingsId)
                REFERENCES CompetitionSettings(Id)
        );

        CREATE TABLE IF NOT EXISTS CompetitionAbsenceReasons(
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            CompetitionGroupPointsId INTEGER NOT NULL,
            Name TEXT NOT NULL,
            Points REAL NOT NULL,
            SortOrder INTEGER NOT NULL DEFAULT 0,
            Last_Update DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (CompetitionGroupPointsId)
                REFERENCES CompetitionGroupPoints(Id)
        );

        CREATE INDEX IF NOT EXISTS idx_competition_group_points_settings
            ON CompetitionGroupPoints(CompetitionSettingsId, GroupNumber);
        CREATE INDEX IF NOT EXISTS idx_competition_absence_reasons_group
            ON CompetitionAbsenceReasons(CompetitionGroupPointsId, SortOrder, Id);

        CREATE TABLE IF NOT EXISTS CompetitionPlayerGroups(
            CompetitionId INTEGER NOT NULL,
            PlayerId TEXT NOT NULL,
            GroupNumber INTEGER NOT NULL,
            Last_Update DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (CompetitionId, PlayerId),
            FOREIGN KEY (CompetitionId) REFERENCES Competitions(Id),
            FOREIGN KEY (PlayerId) REFERENCES Players(Id)
        );

        CREATE INDEX IF NOT EXISTS idx_competition_player_groups_group
            ON CompetitionPlayerGroups(CompetitionId, GroupNumber);

        CREATE TABLE IF NOT EXISTS CompetitionPlayers(
            CompetitionId INTEGER NOT NULL,
            PlayerId TEXT NOT NULL,
            Last_Update DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (CompetitionId, PlayerId),
            FOREIGN KEY (CompetitionId) REFERENCES Competitions(Id),
            FOREIGN KEY (PlayerId) REFERENCES Players(Id)
        );
        """
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO CompetitionPlayerGroups
            (CompetitionId, PlayerId, GroupNumber)
        SELECT cp.CompetitionId, cp.PlayerId, p.GroupNumber
        FROM CompetitionPlayers cp
        INNER JOIN Players p ON cp.PlayerId = p.Id
        WHERE p.Active = 1
        """
    )
    cur.execute(
        """
        UPDATE CompetitionPlayerGroups
        SET GroupNumber = (
            SELECT MIN(cgp.GroupNumber)
            FROM CompetitionSettings cs
            INNER JOIN CompetitionGroupPoints cgp
                ON cgp.CompetitionSettingsId = cs.Id
            WHERE cs.CompetitionId = CompetitionPlayerGroups.CompetitionId
        ),
            Last_Update = CURRENT_TIMESTAMP
        WHERE CompetitionId IN (
            SELECT cs.CompetitionId
            FROM CompetitionSettings cs
            INNER JOIN CompetitionGroupPoints cgp
                ON cgp.CompetitionSettingsId = cs.Id
            GROUP BY cs.CompetitionId
            HAVING COUNT(*) = 1
        )
        """
    )
    conn.commit()
    conn.close()


def query_db(query, args=(), one=False):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute(query, args)
    rows = cursor.fetchall()
    conn.close()
    return rows if not one else rows[0] if rows else None


def get_current_competition_id():
    requested_id = request.values.get("competition_id", type=int)
    if requested_id is None and request.is_json:
        requested_id = (request.get_json(silent=True) or {}).get("competition_id")
        try:
            requested_id = int(requested_id) if requested_id is not None else None
        except (TypeError, ValueError):
            requested_id = None

    if requested_id is not None:
        exists = query_db(
            "SELECT Id FROM Competitions WHERE Id = ? AND Active = 1",
            (requested_id,),
            one=True,
        )
        if exists:
            session["competition_id"] = requested_id

    selected_id = session.get("competition_id")
    selected = query_db(
        "SELECT Id FROM Competitions WHERE Id = ? AND Active = 1",
        (selected_id,),
        one=True,
    ) if selected_id is not None else None
    if selected:
        return selected[0]

    first = query_db(
        "SELECT Id FROM Competitions WHERE Active = 1 ORDER BY Id DESC LIMIT 1",
        one=True,
    )
    if not first:
        abort(500, "Er is geen competitie ingesteld")
    session["competition_id"] = first[0]
    return first[0]



def init_database():
    ensure_competition_schema()
    ensure_competition_settings_schema()
