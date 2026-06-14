import sqlite3
import os
import json
import math
from datetime import datetime
from flask import Flask, abort, render_template, request, jsonify, redirect, session, url_for
from genereer_rondes import BuildNextRound,SaveResultsToPlayers,RefreshPlayersResults

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "development-secret-change-in-production")

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

ensure_competition_schema()

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
        """
    )
    conn.commit()
    conn.close()

ensure_competition_settings_schema()

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

@app.context_processor
def competition_context():
    competitions = query_db(
        "SELECT Id, Name, Year FROM Competitions WHERE Active = 1 ORDER BY Year DESC, Id DESC"
    )
    competition_id = get_current_competition_id()
    current = next((row for row in competitions if row[0] == competition_id), None)
    return {
        "competitions": competitions,
        "current_competition": current,
        "current_competition_id": competition_id,
    }

@app.template_filter("nederlands_resultaat")
def nederlands_resultaat(value):
    translations = {
        "Win": "Winst",
        "Draw": "Remise",
        "Loss": "Verlies",
        "Absent": "Afwezig",
        "Absent more than allowed": "Vaker afwezig dan toegestaan",
        "Extern": "Extern",
        "Extern more than allowed": "Vaker extern dan toegestaan",
        "Wedstrijd leider": "Wedstrijdleider",
        "Bye1": "Vrije ronde 1",
        "Bye2": "Vrije ronde 2",
        "Bye3": "Vrije ronde 3",
        "Uneven": "Oneven aantal spelers",
    }
    return translations.get(value, value)

def get_latest_round_with_pairings(competition_id):
    row = query_db(
        """SELECT MAX(p.RoundId)
           FROM Pairings p
           INNER JOIN Rounds r ON p.RoundId = r.Id
           WHERE r.CompetitionId = ?""",
        (competition_id,),
        one=True,
    )
    return row[0] if row and row[0] is not None else None

def get_latest_played_round(competition_id):
    row = query_db(
        "SELECT MAX(Id) FROM Rounds WHERE Played = 1 AND CompetitionId = ?",
        (competition_id,),
        one=True,
    )
    return row[0] if row and row[0] is not None else None

def get_latest_editable_round(competition_id):
    row = query_db(
        """
        SELECT MAX(r.Id)
        FROM Rounds r
        WHERE r.CompetitionId = ?
          AND (r.Played = 1
           OR EXISTS (SELECT 1 FROM Pairings p WHERE p.RoundId = r.Id))
        """,
        (competition_id,),
        one=True,
    )
    return row[0] if row and row[0] is not None else None

def upsert_setting(cur, name, value, description=None):
    cur.execute("UPDATE Settings SET Value = ? WHERE Name = ?", (str(value), name))
    if cur.rowcount > 0:
        return

    cur.execute("SELECT COALESCE(MAX(CAST(Id AS INTEGER)), 0) + 1 FROM Settings")
    new_id = str(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO Settings (Id, Name, Value, Description) VALUES (?, ?, ?, ?)",
        (new_id, name, str(value), description),
    )

def get_competition_settings_data(competition_id):
    settings_row = query_db(
        """
        SELECT Id, Name, SettingsType
        FROM CompetitionSettings
        WHERE CompetitionId = ?
        """,
        (competition_id,),
        one=True,
    )

    if settings_row:
        group_rows = query_db(
            """
            SELECT Id, GroupNumber, WinPoints, DrawPoints, LossPoints
            FROM CompetitionGroupPoints
            WHERE CompetitionSettingsId = ?
            ORDER BY GroupNumber ASC
            """,
            (settings_row[0],),
        )
        groups = []
        for group_id, group_number, win_points, draw_points, loss_points in group_rows:
            reason_rows = query_db(
                """
                SELECT Name, Points
                FROM CompetitionAbsenceReasons
                WHERE CompetitionGroupPointsId = ?
                ORDER BY SortOrder ASC, Id ASC
                """,
                (group_id,),
            )
            groups.append(
                {
                    "group_number": group_number,
                    "win_points": win_points,
                    "draw_points": draw_points,
                    "loss_points": loss_points,
                    "absence_reasons": [
                        {"name": reason[0], "points": reason[1]}
                        for reason in reason_rows
                    ],
                }
            )
        return {
            "name": settings_row[1],
            "settings_type": settings_row[2],
            "groups": groups,
        }

    competition = query_db(
        "SELECT Name FROM Competitions WHERE Id = ?",
        (competition_id,),
        one=True,
    )
    global_type = query_db(
        "SELECT Value FROM Settings WHERE Name = 'CompType'",
        one=True,
    )
    settings_type = (global_type[0] if global_type else "percentage").lower()
    if settings_type not in {"swiss", "percentage"}:
        settings_type = "percentage"

    group_numbers = [
        row[0]
        for row in query_db(
            "SELECT DISTINCT GroupNumber FROM Players ORDER BY GroupNumber ASC"
        )
    ]
    groups = []
    for group_number in group_numbers:
        result_rows = query_db(
            """
            SELECT Name, ResultsType, Points
            FROM Results
            WHERE GroupNumber = ?
            ORDER BY Id ASC
            """,
            (group_number,),
        )
        points_by_type = {
            str(result_type): points
            for _, result_type, points in result_rows
            if str(result_type) in {"1", "2", "3"}
        }
        groups.append(
            {
                "group_number": group_number,
                "win_points": points_by_type.get("1", 0),
                "draw_points": points_by_type.get("2", 0),
                "loss_points": points_by_type.get("3", 0),
                "absence_reasons": [
                    {"name": name, "points": points}
                    for name, result_type, points in result_rows
                    if str(result_type) == "4"
                ],
            }
        )

    return {
        "name": f"Instellingen {competition[0]}",
        "settings_type": settings_type,
        "groups": groups,
    }


@app.route("/")
def home():
    return render_template("home.html")

@app.route("/competition/select", methods=["POST"])
def select_competition():
    competition_id = request.form.get("competition_id", type=int)
    competition = query_db(
        "SELECT Id FROM Competitions WHERE Id = ? AND Active = 1",
        (competition_id,),
        one=True,
    )
    if not competition:
        abort(404)
    session["competition_id"] = competition_id
    next_url = request.form.get("next", "")
    if not next_url.startswith("/"):
        next_url = url_for("home")
    return redirect(next_url)

@app.route("/spelers")
def spelers():
    rows = query_db(
        "SELECT id, name, rating FROM Players WHERE Active = 1 ORDER BY Name ASC"
    )
    
    # Convert tuples -> dict for easier template handling
    players = [{"id": r[0], "name": r[1], "rating": r[2]} for r in rows]
    
    return render_template("spelers.html", players=players)

@app.route("/spelers/<player_id>/edit", methods=["GET", "POST"])
def edit_player(player_id):
    player = query_db(
        "SELECT Id, Name, Rating FROM Players WHERE Id = ? AND Active = 1",
        (player_id,),
        one=True,
    )
    if not player:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        rating_value = request.form.get("rating", "").strip()

        if not name:
            return render_template(
                "edit_player.html",
                player={"id": player[0], "name": name, "rating": rating_value},
                error="Naam is verplicht.",
            ), 400

        try:
            rating = int(rating_value) if rating_value else None
        except ValueError:
            return render_template(
                "edit_player.html",
                player={"id": player[0], "name": name, "rating": rating_value},
                error="Rating moet een geheel getal zijn.",
            ), 400

        conn = sqlite3.connect(DATABASE)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE Players
            SET Name = ?, Rating = ?, Last_Update = CURRENT_TIMESTAMP
            WHERE Id = ? AND Active = 1
            """,
            (name, rating, player_id),
        )
        conn.commit()
        conn.close()
        return redirect(url_for("spelers"))

    return render_template(
        "edit_player.html",
        player={"id": player[0], "name": player[1], "rating": player[2]},
    )

@app.route("/spelers/<player_id>/delete", methods=["POST"])
def delete_player(player_id):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE Players
        SET Active = 0, Last_Update = CURRENT_TIMESTAMP
        WHERE Id = ? AND Active = 1
        """,
        (player_id,),
    )
    if cur.rowcount == 0:
        conn.close()
        abort(404)

    conn.commit()
    conn.close()
    return redirect(url_for("spelers"))

@app.route("/player-overview")
def player_overview():
    competition_id = get_current_competition_id()
    # Get all players and their presence
    active_round = query_db(
        """SELECT Id, Date FROM Rounds
           WHERE Played = 0 AND CompetitionId = ?
           ORDER BY RoundNumber ASC LIMIT 1""",
        (competition_id,),
        one=True,
    )
    if not active_round:
        return render_template(
            "player_overview.html", players=[], reasons=[], date="-"
        )
    active_round_id, active_round_date = active_round

    rows = query_db("""
        SELECT p.Id, p.Name, pr.Present, pr.ReasonAbsentId, r.Name, pr.RoundId
        FROM Players p
        LEFT JOIN Present pr ON p.Id = pr.PlayerId
        LEFT JOIN Results r ON pr.ReasonAbsentId = r.Id
        WHERE pr.RoundId = ?
        ORDER BY p.Name ASC
    """, (active_round_id,))
    players = [
        {
            "id": r[0],
            "name": r[1],
            "present": r[2],
            "reason_id": r[3],
            "reason": r[4],
            "RoundId": r[5]
        }
        for r in rows
    ]
    
    # Get all possible results/reasons
    reasons = query_db("SELECT Id, Name FROM Results ORDER BY Id ASC")

    return render_template(
        "player_overview.html",
        players=players,
        reasons=reasons,
        date=active_round_date,
    )

@app.route("/update-presence", methods=["POST"])
def update_presence():
    competition_id = get_current_competition_id()
    active_round = query_db(
        """SELECT Id FROM Rounds
           WHERE Played = 0 AND CompetitionId = ?
           ORDER BY RoundNumber ASC LIMIT 1""",
        (competition_id,),
        one=True,
    )
    if not active_round:
        return jsonify({"success": False, "error": "Er is geen actieve ronde gevonden"}), 400
    active_round_id = active_round[0]

    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    for key, value in request.form.items():
        if key.startswith("present_"):
            player_id = key.split("_")[1]
            present = int(value)
            reason_key = f"reason_{player_id}"
            reason_id = request.form.get(reason_key, None)
            if present == 1:
                cur.execute(
                    "UPDATE Present SET Present=?, ReasonAbsentId=NULL WHERE PlayerId=? AND RoundId=?",
                    (present, player_id, active_round_id),
                )
            else:
                cur.execute(
                    "UPDATE Present SET Present=?, ReasonAbsentId=? WHERE PlayerId=? AND RoundId=?",
                    (present, reason_id, player_id, active_round_id),
                )
    conn.commit()
    conn.close()
    return redirect("/player-overview")


@app.route("/confirm-attendance", methods=["POST"])
def confirm_attendance():
    competition_id = get_current_competition_id()
    active_round = query_db(
        """SELECT Id FROM Rounds
           WHERE Played = 0 AND CompetitionId = ?
           ORDER BY RoundNumber ASC LIMIT 1""",
        (competition_id,),
        one=True,
    )
    if not active_round or active_round[0] is None:
        return jsonify({"success": False, "error": "Er is geen actieve ronde gevonden"}), 400

    active_round_id = active_round[0]
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    for key, value in request.form.items():
        if not key.startswith("present_"):
            continue

        player_id = key.split("_")[1]
        present = int(value)
        reason_key = f"reason_{player_id}"
        reason_id = request.form.get(reason_key, None)

        if present == 1:
            cur.execute(
                "UPDATE Present SET Present=?, ReasonAbsentId=NULL WHERE PlayerId=? AND RoundId=?",
                (present, player_id, active_round_id),
            )
        else:
            cur.execute(
                "UPDATE Present SET Present=?, ReasonAbsentId=? WHERE PlayerId=? AND RoundId=?",
                (present, reason_id, player_id, active_round_id),
            )

    conn.commit()
    conn.close()

    BuildNextRound(competition_id, DATABASE)
    return redirect("/genereer-ronde")

@app.route("/generate-round", methods=["POST"])
def generate_round():
    competition_id = get_current_competition_id()
    rows = query_db(
        """SELECT COUNT(*)
           FROM Pairings p
           INNER JOIN Rounds r ON p.RoundId = r.Id
           WHERE p.ResultsType IS NULL AND r.CompetitionId = ?""",
        (competition_id,),
    )
    if rows[0][0] > 0:
         return jsonify({"success": False, "error": "Er ontbreken gegevens"}), 400
    BuildNextRound(competition_id, DATABASE)
    return redirect("/genereer-ronde")

@app.route("/genereer-ronde")
def genereer_ronde():
    competition_id = get_current_competition_id()
    rows = query_db("""SELECT b.Name, c.Name, RoundId, a.GroupNumber,a.Id FROM TempPairing a
                    LEFT JOIN Players b ON a.PlayerId1 = b.Id
                    LEFT JOIN Players c ON a.PlayerId2 = c.Id
                    INNER JOIN Rounds r ON a.RoundId = r.Id
                    WHERE r.CompetitionId = ?
                    ORDER BY a.GroupNumber ASC""", (competition_id,))

    pairings = [
        {"player1": r[0], "player2": r[1], "round": r[2], "group": r[3]," id": r[4]}
        for r in rows
    ]

    return render_template("genereer_ronde.html", pairings=pairings)


@app.route("/swap-players", methods=["POST"])
def swap_players():
    competition_id = get_current_competition_id()
    data = request.json
    player_a_name = data.get("player_a")
    player_b_name = data.get("player_b")

    player_a = query_db("SELECT Id FROM Players WHERE Name=?", (player_a_name,), one=True)
    player_b = query_db("SELECT Id FROM Players WHERE Name=?", (player_b_name,), one=True)

    if player_a is not None:
        player_a = player_a[0]
    if player_b is not None:
        player_b = player_b[0]
    # Fetch matches for both players
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute(
        """SELECT t.PlayerId1, t.PlayerId2, t.RoundId, t.GroupNumber, t.Id
           FROM TempPairing t
           INNER JOIN Rounds r ON t.RoundId = r.Id
           WHERE r.CompetitionId = ? AND (t.PlayerId1 = ? OR t.PlayerId2 = ?)""",
        (competition_id, player_a, player_a),
    )
    match_a = cur.fetchone()
    cur.execute(
        """SELECT t.PlayerId1, t.PlayerId2, t.RoundId, t.GroupNumber, t.Id
           FROM TempPairing t
           INNER JOIN Rounds r ON t.RoundId = r.Id
           WHERE r.CompetitionId = ? AND (t.PlayerId1 = ? OR t.PlayerId2 = ?)""",
        (competition_id, player_b, player_b),
    )
    match_b = cur.fetchone()

    if not match_a or not match_b:
        return jsonify({"success": False, "error": "Een van de spelers is niet gevonden"}), 400

    # Special case: both players are in the same pairing, just flip colors.
    if match_a[4] == match_b[4]:
        p1, p2, rnd, grp, row_id = match_a
        if {p1, p2} != {player_a, player_b}:
            conn.close()
            return jsonify({"success": False, "error": "De spelers zijn geen tegenstanders in dezelfde partij"}), 400

        cur.execute(
            """
            UPDATE TempPairing
            SET PlayerId1=?, PlayerId2=?
            WHERE Id = ?
            """,
            (p2, p1, row_id),
        )
    else:
        # General case: players are in different matches, exchange them.
        def swap_player_in_match(match, swap_out, swap_in):
            p1, p2, rnd, grp, row_id = match
            if p1 == swap_out:
                p1 = swap_in
            elif p2 == swap_out:
                p2 = swap_in
            return p1, p2, rnd, grp, row_id

        new_a = swap_player_in_match(match_a, player_a, player_b)
        new_b = swap_player_in_match(match_b, player_b, player_a)

        cur.execute(
            """
            UPDATE TempPairing
            SET PlayerId1=?, PlayerId2=?
            WHERE Id = ?
            """,
            (new_a[0], new_a[1], new_a[4]),
        )
        cur.execute(
            """
            UPDATE TempPairing
            SET PlayerId1=?, PlayerId2=?
            WHERE Id = ?
            """,
            (new_b[0], new_b[1], new_b[4]),
        )

    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.route("/finalize-round", methods=["POST"])
def finalize_roundfromTemp():
    competition_id = get_current_competition_id()
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute(
        """SELECT t.RoundId FROM TempPairing t
           INNER JOIN Rounds r ON t.RoundId = r.Id
           WHERE r.CompetitionId = ?
           LIMIT 1""",
        (competition_id,),
    )
    round_row = cur.fetchone()
    if not round_row:
        conn.close()
        return redirect("/genereer-ronde")

    round_id = round_row[0]
    cur.execute("DELETE FROM Pairings WHERE RoundId = ?", (round_id,))
    cur.execute("""
        INSERT INTO Pairings (PlayerId1, PlayerId2, RoundId, GroupNumber)
        SELECT PlayerId1, PlayerId2, RoundId, GroupNumber
        FROM TempPairing
        WHERE RoundId = ?
    """, (round_id,))
    cur.execute("DELETE FROM TempPairing WHERE RoundId = ?", (round_id,))
    conn.commit()
    conn.close()
    return redirect(f"/finalized_round?round_id={round_id}")

@app.route("/finalized_round")
def finalize_round():
    competition_id = get_current_competition_id()
    selected_round = request.args.get("round_id", type=int)
    if selected_round is None:
        selected_round = get_latest_round_with_pairings(competition_id)

    if selected_round is None:
        return render_template("finalized-round.html", pairings=[], round_id=None)
    round_exists = query_db(
        "SELECT Id FROM Rounds WHERE Id = ? AND CompetitionId = ?",
        (selected_round, competition_id),
        one=True,
    )
    if not round_exists:
        abort(404)

    rows = query_db(
        """SELECT b.Name, c.Name, RoundId, a.GroupNumber,a.ResultsType,a.Id FROM Pairings a
           LEFT JOIN Players b ON a.PlayerId1 = b.Id
           LEFT JOIN Players c ON a.PlayerId2 = c.Id
           WHERE a.RoundId = ?
           ORDER BY a.GroupNumber ASC""",
        (selected_round,),
    )

    pairings = [
    {"player1": r[0], "player2": r[1], "round": r[2], "group": r[3],"Result": r[4],"Id": r[5]}
    for r in rows
    ]

    return render_template("finalized-round.html", pairings=pairings, round_id=selected_round)

@app.route("/update-result", methods=["POST"])
def update_result():
    competition_id = get_current_competition_id()
    data = request.json
    # print("Received data:", data)  # Add this line for debugging
    pairing_id = data.get("pairing_id")
    result_id = data.get("result_id")
    print(pairing_id)
    if not pairing_id:
        return jsonify({"success": False, "error": "Er ontbreken gegevens"}), 400
    pairing = query_db(
        """SELECT p.Id FROM Pairings p
           INNER JOIN Rounds r ON p.RoundId = r.Id
           WHERE p.Id = ? AND r.CompetitionId = ?""",
        (pairing_id, competition_id),
        one=True,
    )
    if not pairing:
        abort(404)

    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    if result_id in (None, ""):
        cur.execute("UPDATE Pairings SET ResultsType=NULL WHERE Id=?", (pairing_id,))
    else:
        cur.execute("UPDATE Pairings SET ResultsType=? WHERE Id=?", (int(result_id), pairing_id))
    conn.commit()
    conn.close()
    RefreshPlayersResults(DATABASE)

    return jsonify({"success": True})



@app.route("/save-results", methods=["POST"])
def save_results():
    competition_id = get_current_competition_id()
    round_id = request.form.get("round_id", type=int)
    if round_id is None:
        round_id = get_latest_round_with_pairings(competition_id)

    if round_id is None:
        return jsonify({"success": False, "error": "Er is geen ronde gevonden om op te slaan"}), 400
    round_exists = query_db(
        "SELECT Id FROM Rounds WHERE Id = ? AND CompetitionId = ?",
        (round_id, competition_id),
        one=True,
    )
    if not round_exists:
        abort(404)

    missing = query_db(
        """
        SELECT COUNT(*) FROM Pairings
        WHERE RoundId = ?
          AND ResultsType IS NULL
          AND PlayerId1 != 999
          AND PlayerId2 != 999
        """,
        (round_id,),
        one=True,
    )[0]

    if missing > 0:
        return jsonify(
            {"success": False, "error": "Niet alle resultaten van deze ronde zijn ingevuld"}
        ), 400

    SaveResultsToPlayers(competition_id, round_id, DATABASE)
    return redirect(f"/finalized_round?round_id={round_id}")

@app.route("/player_ranking")
def ranking():    
    competition_id = get_current_competition_id()
    RefreshPlayersResults(DATABASE)
    date = query_db(
        """SELECT Date FROM Rounds
           WHERE Played = 1 AND CompetitionId = ?
           ORDER BY RoundNumber DESC LIMIT 1""",
        (competition_id,),
        one=True,
    )

    rows = query_db("""
        SELECT p.Id, p.Name,SUM(pr.Points) as TotalPoints,p.GroupNumber
        FROM Players p
        LEFT JOIN PlayersResults pr ON p.Id = pr.PlayerId
          AND pr.RoundId IN (SELECT Id FROM Rounds WHERE CompetitionId = ?)
        WHERE p.Active = 1
        GROUP BY p.Id, p.Name, p.GroupNumber
        ORDER BY p.GroupNumber ASC, TotalPoints DESC
    """, (competition_id,))
    
    players = [
        {
            "id": r[0],
            "name": r[1],
            "TotalPoints": r[2],
            "GroupNumber": r[3]
            # "reason_id": r[3],
            # "reason": r[4],
            # "RoundId": r[5]
        } for r in rows

    ]
    
    # Get all possible results/reasons
    ranking_date = date[0] if date else "-"
    return render_template("player_ranking.html", players=players, date = ranking_date)

@app.route("/speler/<int:player_id>")
def player_results(player_id):
    competition_id = get_current_competition_id()
    RefreshPlayersResults(DATABASE)
    # Fetch player name
    player_name_row = query_db("SELECT name FROM Players WHERE id = ?", (player_id,), one=True)
    player_name = player_name_row[0] if player_name_row else f"Speler {player_id}"

    # Get results from PlayerResults
    rows = query_db("""
        SELECT b.Name as OpponentName, c.Name as ResultsName,c.Points,d.Date,d.Period
        FROM PlayersResults a
        LEFT JOIN Players b ON a.OpponentId = b.Id
        LEFT JOIN Results c ON a.ResultId = c.Id
        LEFT JOIN Rounds d ON a.RoundId = d.Id
        WHERE a.PlayerId = ? AND d.CompetitionId = ?
        ORDER BY a.Roundid DESC
    """, (player_id, competition_id))

    results = [
        {"opponent": r[0], "result_id": r[1], "Points": r[2],"Date": r[3], "Period": r[4]}
        for r in rows
    ]

    return render_template("player_results.html", player_id=player_id, player_name=player_name, results=results)

@app.route("/competition")
def competition():
    competition_id = get_current_competition_id()
    competition_row = query_db(
        """SELECT Name, Year, NumberOfRounds, NumberOfNonCompete
           FROM Competitions WHERE Id = ?""",
        (competition_id,),
        one=True,
    )
    rounds = query_db(
        """SELECT RoundNumber, Date, Played FROM Rounds
           WHERE CompetitionId = ? ORDER BY RoundNumber ASC""",
        (competition_id,),
    )
    return render_template(
        "competition.html",
        rounds=rounds,
        competition_name=competition_row[0],
        number_of_rounds=competition_row[2],
        non_compete=competition_row[3],
        competition_year=competition_row[1],
    )

@app.route("/competition/settings", methods=["GET", "POST"])
def competition_settings():
    competition_id = get_current_competition_id()
    competition_row = query_db(
        "SELECT Name FROM Competitions WHERE Id = ?",
        (competition_id,),
        one=True,
    )

    if request.method == "GET":
        return render_template(
            "competition_settings.html",
            competition_name=competition_row[0],
            settings_data=get_competition_settings_data(competition_id),
            error=None,
            saved=request.args.get("saved") == "1",
        )

    raw_configuration = request.form.get("configuration", "")
    try:
        configuration = json.loads(raw_configuration)
    except (TypeError, json.JSONDecodeError):
        configuration = {}

    def render_error(message):
        return render_template(
            "competition_settings.html",
            competition_name=competition_row[0],
            settings_data=configuration,
            error=message,
            saved=False,
        ), 400

    if not isinstance(configuration, dict):
        return render_error("De instellingen konden niet worden gelezen")

    name = configuration.get("name")
    settings_type = configuration.get("settings_type")
    groups = configuration.get("groups")
    if not isinstance(name, str) or not name.strip():
        return render_error("Vul een naam voor deze instellingen in")
    name = name.strip()
    if settings_type not in {"swiss", "percentage"}:
        return render_error("Kies Zwitsers of Percentage als competitietype")
    if not isinstance(groups, list) or not groups:
        return render_error("Voeg minimaal één groep toe")

    validated_groups = []
    seen_group_numbers = set()
    for group in groups:
        if not isinstance(group, dict):
            return render_error("Een groep bevat ongeldige gegevens")

        group_number = group.get("group_number")
        if (
            not isinstance(group_number, int)
            or isinstance(group_number, bool)
            or group_number < 1
        ):
            return render_error("Ieder groepnummer moet een positief geheel getal zijn")
        if group_number in seen_group_numbers:
            return render_error(f"Groep {group_number} komt meer dan één keer voor")
        seen_group_numbers.add(group_number)

        point_values = []
        for field_name, label in (
            ("win_points", "winst"),
            ("draw_points", "remise"),
            ("loss_points", "verlies"),
        ):
            try:
                points = float(group.get(field_name))
            except (TypeError, ValueError):
                return render_error(
                    f"Vul geldige punten voor {label} in bij groep {group_number}"
                )
            if not math.isfinite(points):
                return render_error(
                    f"Vul geldige punten voor {label} in bij groep {group_number}"
                )
            point_values.append(points)

        reasons = group.get("absence_reasons", [])
        if not isinstance(reasons, list):
            return render_error(
                f"De redenen voor afwezigheid bij groep {group_number} zijn ongeldig"
            )

        validated_reasons = []
        seen_reason_names = set()
        for reason in reasons:
            if not isinstance(reason, dict):
                return render_error(
                    f"Een reden voor afwezigheid bij groep {group_number} is ongeldig"
                )
            reason_name = reason.get("name")
            if not isinstance(reason_name, str) or not reason_name.strip():
                return render_error(
                    f"Vul alle redenen voor afwezigheid bij groep {group_number} in"
                )
            reason_name = reason_name.strip()
            normalized_name = reason_name.casefold()
            if normalized_name in seen_reason_names:
                return render_error(
                    f"De reden '{reason_name}' komt meer dan één keer voor bij groep {group_number}"
                )
            seen_reason_names.add(normalized_name)
            try:
                reason_points = float(reason.get("points"))
            except (TypeError, ValueError):
                return render_error(
                    f"Vul geldige punten in voor '{reason_name}' bij groep {group_number}"
                )
            if not math.isfinite(reason_points):
                return render_error(
                    f"Vul geldige punten in voor '{reason_name}' bij groep {group_number}"
                )
            validated_reasons.append((reason_name, reason_points))

        validated_groups.append(
            (
                group_number,
                point_values[0],
                point_values[1],
                point_values[2],
                validated_reasons,
            )
        )

    conn = sqlite3.connect(DATABASE)
    try:
        cur = conn.cursor()
        settings_row = cur.execute(
            "SELECT Id FROM CompetitionSettings WHERE CompetitionId = ?",
            (competition_id,),
        ).fetchone()
        if settings_row:
            settings_id = settings_row[0]
            cur.execute(
                """
                UPDATE CompetitionSettings
                SET Name = ?, SettingsType = ?, Last_Update = CURRENT_TIMESTAMP
                WHERE Id = ?
                """,
                (name, settings_type, settings_id),
            )
            cur.execute(
                """
                DELETE FROM CompetitionAbsenceReasons
                WHERE CompetitionGroupPointsId IN (
                    SELECT Id FROM CompetitionGroupPoints
                    WHERE CompetitionSettingsId = ?
                )
                """,
                (settings_id,),
            )
            cur.execute(
                "DELETE FROM CompetitionGroupPoints WHERE CompetitionSettingsId = ?",
                (settings_id,),
            )
        else:
            cur.execute(
                """
                INSERT INTO CompetitionSettings
                    (CompetitionId, Name, SettingsType)
                VALUES (?, ?, ?)
                """,
                (competition_id, name, settings_type),
            )
            settings_id = cur.lastrowid

        for (
            group_number,
            win_points,
            draw_points,
            loss_points,
            reasons,
        ) in validated_groups:
            cur.execute(
                """
                INSERT INTO CompetitionGroupPoints
                    (CompetitionSettingsId, GroupNumber, WinPoints,
                     DrawPoints, LossPoints)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    settings_id,
                    group_number,
                    win_points,
                    draw_points,
                    loss_points,
                ),
            )
            group_points_id = cur.lastrowid
            cur.executemany(
                """
                INSERT INTO CompetitionAbsenceReasons
                    (CompetitionGroupPointsId, Name, Points, SortOrder)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (group_points_id, reason_name, reason_points, index)
                    for index, (reason_name, reason_points) in enumerate(reasons)
                ],
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        return render_error("De instellingen konden niet worden opgeslagen")
    finally:
        conn.close()

    return redirect(url_for("competition_settings", saved=1))

@app.route("/competition/reset-results", methods=["POST"])
def competition_reset_results():
    competition_id = get_current_competition_id()
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    # Clear all historical round outcomes while keeping competition structure.
    cur.execute(
        "DELETE FROM PlayersResults WHERE RoundId IN "
        "(SELECT Id FROM Rounds WHERE CompetitionId = ?)",
        (competition_id,),
    )
    cur.execute(
        "UPDATE Pairings SET ResultsType = NULL WHERE RoundId IN "
        "(SELECT Id FROM Rounds WHERE CompetitionId = ?)",
        (competition_id,),
    )
    cur.execute(
        "UPDATE Rounds SET Played = 0 WHERE CompetitionId = ?",
        (competition_id,),
    )
    cur.execute(
        "DELETE FROM TempPairing WHERE RoundId IN "
        "(SELECT Id FROM Rounds WHERE CompetitionId = ?)",
        (competition_id,),
    )

    conn.commit()
    conn.close()
    return redirect("/competition")

@app.route("/competition/create", methods=["POST"])
def competition_create():
    name = request.form.get("name", "").strip()
    number_of_rounds = request.form.get("number_of_rounds", type=int)
    non_compete = request.form.get("non_compete_rounds", type=int)
    round_dates = request.form.getlist("round_date")

    if number_of_rounds is None or number_of_rounds < 1:
        return jsonify({"success": False, "error": "Het aantal rondes moet minimaal 1 zijn"}), 400
    if non_compete is None or non_compete < 0:
        return jsonify({"success": False, "error": "Het aantal rondes tussen dezelfde indeling moet 0 of hoger zijn"}), 400
    if len(round_dates) != number_of_rounds:
        return jsonify({"success": False, "error": "Vul voor iedere ronde precies één datum in"}), 400

    parsed_dates = []
    for d in round_dates:
        try:
            parsed_dates.append(datetime.strptime(d, "%Y-%m-%d").date())
        except ValueError:
            return jsonify({"success": False, "error": f"Ongeldige datumnotatie: {d}"}), 400

    year = parsed_dates[0].year
    if not name:
        name = f"Competitie {year}"

    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO Competitions
            (Name, Year, NumberOfRounds, NumberOfNonCompete)
        VALUES (?, ?, ?, ?)
        """,
        (name, year, number_of_rounds, non_compete),
    )
    competition_id = cur.lastrowid
    first_round_id = cur.execute(
        "SELECT COALESCE(MAX(Id), 0) + 1 FROM Rounds"
    ).fetchone()[0]

    rounds_to_insert = [
        (
            first_round_id + idx - 1,
            "1",
            idx,
            year,
            parsed_dates[idx - 1].isoformat(),
            0,
            competition_id,
        )
        for idx in range(1, number_of_rounds + 1)
    ]
    cur.executemany(
        """INSERT INTO Rounds
           (Id, Period, RoundNumber, Year, Date, Played, CompetitionId)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rounds_to_insert,
    )

    cur.execute("SELECT Id FROM Players WHERE Active = 1 ORDER BY Id ASC")
    player_rows = cur.fetchall()
    present_rows = [
        (str(player_id), round_id, 1, None)
        for (player_id,) in player_rows
        for round_id in range(first_round_id, first_round_id + number_of_rounds)
    ]
    cur.executemany(
        "INSERT INTO Present (PlayerId, RoundId, Present, ReasonAbsentId) VALUES (?, ?, ?, ?)",
        present_rows,
    )

    conn.commit()
    conn.close()
    session["competition_id"] = competition_id
    return redirect("/competition")

@app.route("/round-editor")
def round_editor():
    competition_id = get_current_competition_id()
    selected_round = request.args.get("round_id", type=int)
    if selected_round is None:
        selected_round = get_latest_editable_round(competition_id)

    rounds = query_db(
        """
        SELECT r.Id, r.Date, r.Played
        FROM Rounds r
        WHERE r.CompetitionId = ?
          AND (r.Played = 1
           OR EXISTS (SELECT 1 FROM Pairings p WHERE p.RoundId = r.Id))
        ORDER BY r.Id DESC
        """,
        (competition_id,),
    )
    editable_round_ids = {r[0] for r in rounds}
    if selected_round not in editable_round_ids:
        selected_round = get_latest_editable_round(competition_id)

    if selected_round is None:
        group_numbers = query_db("SELECT DISTINCT GroupNumber FROM Players ORDER BY GroupNumber ASC")
        return render_template(
            "round_editor.html",
            round_id=None,
            rounds=rounds,
            date=None,
            round_played=None,
            group_numbers=[g[0] for g in group_numbers],
            players=[],
            reasons=[],
            pairings=[],
            present_players=[],
            unpaired_present_players=[],
        )

    round_meta = query_db(
        "SELECT Date, Played FROM Rounds WHERE Id = ? AND CompetitionId = ?",
        (selected_round, competition_id),
        one=True,
    )
    group_numbers = query_db("SELECT DISTINCT GroupNumber FROM Players ORDER BY GroupNumber ASC")
    rows = query_db(
        """
        SELECT p.Id, p.Name, pr.Present, pr.ReasonAbsentId, r.Name
        FROM Players p
        LEFT JOIN Present pr ON p.Id = pr.PlayerId AND pr.RoundId = ?
        LEFT JOIN Results r ON pr.ReasonAbsentId = r.Id
        ORDER BY p.Name ASC
        """,
        (selected_round,),
    )
    players = [
        {
            "id": r[0],
            "name": r[1],
            "present": 1 if r[2] is None else r[2],
            "reason_id": r[3],
            "reason": r[4],
        }
        for r in rows
    ]
    reasons = query_db("SELECT Id, Name FROM Results ORDER BY Id ASC")
    pairing_rows = query_db(
        """SELECT
               COALESCE(b.Name, CASE WHEN a.PlayerId1 = 'NONE' THEN 'Geen speler' ELSE a.PlayerId1 END) as Player1Name,
               COALESCE(c.Name, CASE WHEN a.PlayerId2 = 'NONE' THEN 'Geen speler' ELSE a.PlayerId2 END) as Player2Name,
               a.GroupNumber, a.ResultsType, a.Id, a.PlayerId1, a.PlayerId2
           FROM Pairings a
           LEFT JOIN Players b ON a.PlayerId1 = b.Id
           LEFT JOIN Players c ON a.PlayerId2 = c.Id
           WHERE a.RoundId = ?
           ORDER BY a.GroupNumber ASC""",
        (selected_round,),
    )
    pairings = [
        {
            "player1": r[0],
            "player2": r[1],
            "group": r[2],
            "Result": r[3],
            "Id": r[4],
            "player1_id": r[5],
            "player2_id": r[6],
            "has_placeholder": (r[5] == "NONE" or r[6] == "NONE"),
        }
        for r in pairing_rows
    ]
    present_players = [
        {"id": p["id"], "name": p["name"]}
        for p in players
        if p["present"] == 1
    ]
    paired_ids = set()
    for p in pairings:
        if p["player1_id"] != "999":
            paired_ids.add(str(p["player1_id"]))
        if p["player2_id"] != "999":
            paired_ids.add(str(p["player2_id"]))
    unpaired_present_players = [
        p for p in present_players if str(p["id"]) not in paired_ids
    ]
    round_date = round_meta[0] if round_meta else "-"
    round_played = round_meta[1] if round_meta else 0
    return render_template(
        "round_editor.html",
        round_id=selected_round,
        rounds=rounds,
        date=round_date,
        round_played=round_played,
        group_numbers=[g[0] for g in group_numbers],
        players=players,
        reasons=reasons,
        pairings=pairings,
        present_players=present_players,
        unpaired_present_players=unpaired_present_players,
    )

@app.route("/round-editor/add-pairing", methods=["POST"])
def round_editor_add_pairing():
    competition_id = get_current_competition_id()
    round_id = request.form.get("round_id", type=int)
    group_number = request.form.get("group_number", type=int)
    if round_id is None or group_number is None:
        return jsonify({"success": False, "error": "De ronde of groep ontbreekt"}), 400
    if group_number not in (1, 2):
        return jsonify({"success": False, "error": "De groep moet 1 of 2 zijn"}), 400

    round_row = query_db(
        "SELECT Id FROM Rounds WHERE Id = ? AND CompetitionId = ?",
        (round_id, competition_id),
        one=True,
    )
    if not round_row:
        return jsonify({"success": False, "error": "De ronde is niet gevonden"}), 400

    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO Pairings (PlayerId1, PlayerId2, RoundId, GroupNumber, ResultsType)
        VALUES ('NONE', 'NONE', ?, ?, NULL)
        """,
        (round_id, group_number),
    )
    conn.commit()
    conn.close()
    return redirect(f"/round-editor?round_id={round_id}")

@app.route("/round-editor/update-presence", methods=["POST"])
def round_editor_update_presence():
    competition_id = get_current_competition_id()
    round_id = request.form.get("round_id", type=int)
    if round_id is None:
        return jsonify({"success": False, "error": "Het rondenummer ontbreekt"}), 400
    editable_round = query_db(
        """
        SELECT r.Id
        FROM Rounds r
        WHERE r.Id = ?
          AND r.CompetitionId = ?
          AND (r.Played = 1 OR EXISTS (SELECT 1 FROM Pairings p WHERE p.RoundId = r.Id))
        """,
        (round_id, competition_id),
        one=True,
    )
    if not editable_round:
        return jsonify({"success": False, "error": "Alleen gespeelde rondes of rondes met een indeling kunnen hier worden bewerkt"}), 400

    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    for key, value in request.form.items():
        if not key.startswith("present_"):
            continue
        player_id = int(key.split("_")[1])
        present = int(value)
        reason_key = f"reason_{player_id}"
        reason_value = request.form.get(reason_key, "")
        reason_id = int(reason_value) if reason_value not in (None, "") else None

        cur.execute(
            """
            INSERT INTO Present (PlayerId, RoundId, Present, ReasonAbsentId)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(PlayerId, RoundId) DO UPDATE SET
                Present = excluded.Present,
                ReasonAbsentId = excluded.ReasonAbsentId
            """,
            (player_id, round_id, present, None if present == 1 else reason_id),
        )
    conn.commit()
    conn.close()
    RefreshPlayersResults(DATABASE)
    return redirect(f"/round-editor?round_id={round_id}")

@app.route("/round-editor/update-result", methods=["POST"])
def round_editor_update_result():
    competition_id = get_current_competition_id()
    data = request.json
    pairing_id = data.get("pairing_id")
    result_id = data.get("result_id")
    if not pairing_id:
        return jsonify({"success": False, "error": "Het partijnummer ontbreekt"}), 400

    pairing_round = query_db(
        """SELECT p.RoundId FROM Pairings p
           INNER JOIN Rounds r ON p.RoundId = r.Id
           WHERE p.Id = ? AND r.CompetitionId = ?""",
        (pairing_id, competition_id),
        one=True,
    )
    if not pairing_round:
        return jsonify({"success": False, "error": "De partij is niet gevonden"}), 400
    pairing_players = query_db("SELECT PlayerId1, PlayerId2 FROM Pairings WHERE Id = ?", (pairing_id,), one=True)
    if not pairing_players:
        return jsonify({"success": False, "error": "De partij is niet gevonden"}), 400
    if pairing_players[0] == "NONE" or pairing_players[1] == "NONE":
        return jsonify({"success": False, "error": "Vul beide spelers in voordat je een resultaat kiest"}), 400

    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    if result_id in (None, ""):
        cur.execute("UPDATE Pairings SET ResultsType = NULL WHERE Id = ?", (pairing_id,))
    else:
        cur.execute(
            "UPDATE Pairings SET ResultsType = ? WHERE Id = ?",
            (int(result_id), pairing_id),
        )
    conn.commit()
    conn.close()
    RefreshPlayersResults(DATABASE)
    return jsonify({"success": True})

@app.route("/round-editor/swap-players", methods=["POST"])
def round_editor_swap_players():
    competition_id = get_current_competition_id()
    data = request.json
    round_id = data.get("round_id")
    player_a_id = data.get("player_a_id")
    player_b_id = data.get("player_b_id")

    if not round_id or not player_a_id or not player_b_id:
        return jsonify({"success": False, "error": "Er ontbreken gegevens"}), 400
    if str(player_a_id) == str(player_b_id):
        return jsonify({"success": False, "error": "Selecteer twee verschillende spelers"}), 400

    round_row = query_db(
        """
        SELECT r.Id
        FROM Rounds r
        WHERE r.Id = ?
          AND r.CompetitionId = ?
          AND EXISTS (SELECT 1 FROM Pairings p WHERE p.RoundId = r.Id)
        """,
        (round_id, competition_id),
        one=True,
    )
    if not round_row:
        return jsonify({"success": False, "error": "Deze ronde heeft geen indelingen om te bewerken"}), 400

    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    cur.execute(
        """SELECT PlayerId, Present
           FROM Present
           WHERE RoundId = ? AND PlayerId IN (?, ?)""",
        (round_id, str(player_a_id), str(player_b_id)),
    )
    present_rows = {row[0]: row[1] for row in cur.fetchall()}
    if present_rows.get(str(player_a_id), 0) != 1 or present_rows.get(str(player_b_id), 0) != 1:
        conn.close()
        return jsonify({"success": False, "error": "Beide spelers moeten eerst als aanwezig zijn gemarkeerd"}), 400

    cur.execute("SELECT GroupNumber FROM Players WHERE Id = ?", (str(player_a_id),))
    group_a = cur.fetchone()
    cur.execute("SELECT GroupNumber FROM Players WHERE Id = ?", (str(player_b_id),))
    group_b = cur.fetchone()
    if not group_a or not group_b:
        conn.close()
        return jsonify({"success": False, "error": "De speler is niet gevonden"}), 400
    if group_a[0] != group_b[0]:
        conn.close()
        return jsonify({"success": False, "error": "De spelers moeten in dezelfde groep zitten"}), 400

    cur.execute(
        """SELECT Id, PlayerId1, PlayerId2, GroupNumber
           FROM Pairings
           WHERE RoundId = ? AND (PlayerId1 = ? OR PlayerId2 = ?)""",
        (round_id, str(player_a_id), str(player_a_id)),
    )
    match_a = cur.fetchone()
    cur.execute(
        """SELECT Id, PlayerId1, PlayerId2, GroupNumber
           FROM Pairings
           WHERE RoundId = ? AND (PlayerId1 = ? OR PlayerId2 = ?)""",
        (round_id, str(player_b_id), str(player_b_id)),
    )
    match_b = cur.fetchone()

    # Both players already paired.
    if match_a and match_b:
        if match_a[3] != match_b[3]:
            conn.close()
            return jsonify({"success": False, "error": "De spelers zitten niet in indelingen van dezelfde groep"}), 400

        if match_a[0] == match_b[0]:
            row_id, p1, p2, _ = match_a
            if {p1, p2} != {str(player_a_id), str(player_b_id)}:
                conn.close()
                return jsonify({"success": False, "error": "De spelers zijn in deze ronde geen tegenstanders"}), 400
            cur.execute(
                "UPDATE Pairings SET PlayerId1 = ?, PlayerId2 = ?, ResultsType = NULL WHERE Id = ?",
                (p2, p1, row_id),
            )
        else:
            def swap_player_in_match(match, swap_out, swap_in):
                row_id, p1, p2, grp = match
                if p1 == swap_out:
                    p1 = swap_in
                elif p2 == swap_out:
                    p2 = swap_in
                return row_id, p1, p2, grp

            new_a = swap_player_in_match(match_a, str(player_a_id), str(player_b_id))
            new_b = swap_player_in_match(match_b, str(player_b_id), str(player_a_id))
            cur.execute(
                "UPDATE Pairings SET PlayerId1 = ?, PlayerId2 = ?, ResultsType = NULL WHERE Id = ?",
                (new_a[1], new_a[2], new_a[0]),
            )
            cur.execute(
                "UPDATE Pairings SET PlayerId1 = ?, PlayerId2 = ?, ResultsType = NULL WHERE Id = ?",
                (new_b[1], new_b[2], new_b[0]),
            )
    # One player is paired, the other is currently unpaired.
    elif match_a or match_b:
        in_match = match_a if match_a else match_b
        out_id = str(player_a_id) if match_a else str(player_b_id)
        in_id = str(player_b_id) if match_a else str(player_a_id)
        row_id, p1, p2, grp = in_match

        if grp != group_a[0]:
            conn.close()
            return jsonify({"success": False, "error": "De speler kan niet naar een indeling van een andere groep worden verplaatst"}), 400

        if p1 == out_id:
            p1 = in_id
        elif p2 == out_id:
            p2 = in_id
        else:
            conn.close()
            return jsonify({"success": False, "error": "Het wisselen is mislukt"}), 400

        cur.execute(
            "UPDATE Pairings SET PlayerId1 = ?, PlayerId2 = ?, ResultsType = NULL WHERE Id = ?",
            (p1, p2, row_id),
        )
        # Keep attendance in sync with who is actually playing this round.
        cur.execute(
            "UPDATE Present SET Present = 1, ReasonAbsentId = NULL WHERE RoundId = ? AND PlayerId = ?",
            (round_id, in_id),
        )
        cur.execute(
            "UPDATE Present SET Present = 0, ReasonAbsentId = NULL WHERE RoundId = ? AND PlayerId = ?",
            (round_id, out_id),
        )
    else:
        cur.execute(
            """SELECT Id, PlayerId1, PlayerId2, GroupNumber
               FROM Pairings
               WHERE RoundId = ?
                 AND GroupNumber = ?
                 AND (PlayerId1 = 'NONE' OR PlayerId2 = 'NONE')
               ORDER BY Id ASC
               LIMIT 1""",
            (round_id, group_a[0]),
        )
        empty_pairing = cur.fetchone()
        if not empty_pairing:
            conn.close()
            return jsonify({"success": False, "error": "Er is in deze groep geen lege plek gevonden"}), 400

        row_id, p1, p2, _ = empty_pairing
        candidates = [str(player_a_id), str(player_b_id)]
        if p1 == "NONE" and candidates:
            p1 = candidates.pop(0)
        if p2 == "NONE" and candidates:
            p2 = candidates.pop(0)

        if candidates:
            conn.close()
            return jsonify({"success": False, "error": "Er zijn twee lege plekken nodig om beide spelers te plaatsen"}), 400

        cur.execute(
            "UPDATE Pairings SET PlayerId1 = ?, PlayerId2 = ?, ResultsType = NULL WHERE Id = ?",
            (p1, p2, row_id),
        )
        cur.execute(
            "UPDATE Present SET Present = 1, ReasonAbsentId = NULL WHERE RoundId = ? AND PlayerId = ?",
            (round_id, str(player_a_id)),
        )
        cur.execute(
            "UPDATE Present SET Present = 1, ReasonAbsentId = NULL WHERE RoundId = ? AND PlayerId = ?",
            (round_id, str(player_b_id)),
        )

    conn.commit()
    conn.close()
    RefreshPlayersResults(DATABASE)
    return jsonify({"success": True})

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))  # Render sets PORT
    app.run(host="0.0.0.0", port=port, debug=False)
