import sqlite3
from datetime import datetime
from flask import Flask, render_template,request, jsonify,redirect
from genereer_rondes import BuildNextRound,SaveResultsToPlayers,RefreshPlayersResults

app = Flask(__name__)

DATABASE = "database.db"

def query_db(query, args=(), one=False):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute(query, args)
    rows = cursor.fetchall()
    conn.close()
    return rows if not one else rows[0] if rows else None


def get_latest_round_with_pairings():
    row = query_db("SELECT MAX(RoundId) FROM Pairings", one=True)
    return row[0] if row and row[0] is not None else None

def get_latest_played_round():
    row = query_db("SELECT MAX(Id) FROM Rounds WHERE Played = 1", one=True)
    return row[0] if row and row[0] is not None else None

def get_latest_editable_round():
    row = query_db(
        """
        SELECT MAX(r.Id)
        FROM Rounds r
        WHERE r.Played = 1
           OR EXISTS (SELECT 1 FROM Pairings p WHERE p.RoundId = r.Id)
        """,
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


@app.route("/")
def home():
    return render_template("home.html")

@app.route("/spelers")
def spelers():
    rows = query_db("SELECT id, name, rating FROM Players ORDER BY Name ASC")
    
    # Convert tuples -> dict for easier template handling
    players = [{"id": r[0], "name": r[1], "rating": r[2]} for r in rows]
    
    return render_template("spelers.html", players=players)

@app.route("/player-overview")
def player_overview():
    # Get all players and their presence
    date = query_db("SELECT Date FROM Rounds WHERE Played = 0 ORDER BY ID ASC LIMIT 1", one=True)

    rows = query_db("""
        SELECT p.Id, p.Name, pr.Present, pr.ReasonAbsentId, r.Name, pr.RoundId
        FROM Players p
        LEFT JOIN Present pr ON p.Id = pr.PlayerId
        LEFT JOIN Results r ON pr.ReasonAbsentId = r.Id
        WHERE pr.RoundId = (SELECT MIN(Id) FROM Rounds WHERE Played = 0)
        ORDER BY p.Name ASC
    """)
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

    return render_template("player_overview.html", players=players, reasons=reasons, date = date[0])

@app.route("/update-presence", methods=["POST"])
def update_presence():
    for key, value in request.form.items():
        print(key)
        if key.startswith("present_"):
            player_id = key.split("_")[1]
            present = int(value)
            reason_key = f"reason_{player_id}"
            reason_id = request.form.get(reason_key, None)
            # Update the Present table for this player
            conn = sqlite3.connect(DATABASE)
            cur = conn.cursor()
            if present == 1:
                cur.execute("UPDATE Present SET Present=?, ReasonAbsentId=NULL WHERE PlayerId=? and RoundId =(SELECT Id FROM Rounds WHERE Played = 0 ORDER BY Id ASC LIMIT 1) ", (present, player_id))
            else:
                cur.execute("UPDATE Present SET Present=?, ReasonAbsentId=? WHERE PlayerId=? and RoundId =(SELECT Id FROM Rounds WHERE Played = 0 ORDER BY Id ASC LIMIT 1)", (present, reason_id, player_id))
            conn.commit()
            conn.close()
    return redirect("/player-overview")


@app.route("/confirm-attendance", methods=["POST"])
def confirm_attendance():
    active_round = query_db("SELECT MIN(Id) FROM Rounds WHERE Played = 0", one=True)
    if not active_round or active_round[0] is None:
        return jsonify({"success": False, "error": "No active round found"}), 400

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

    BuildNextRound()
    return redirect("/genereer-ronde")

@app.route("/generate-round", methods=["POST"])
def generate_round():
    rows = query_db("""SELECT COUNT(*) FROM Pairings where ResultsType is NULL""")
    if rows[0][0] > 0:
         return jsonify({"success": False, "error": "Missing data"}), 400
    BuildNextRound()
    return redirect("/genereer-ronde")

@app.route("/genereer-ronde")
def genereer_ronde():
    rows = query_db("""SELECT b.Name, c.Name, RoundId, a.GroupNumber,a.Id FROM TempPairing a
                    LEFT JOIN Players b ON a.PlayerId1 = b.Id
                    LEFT JOIN Players c ON a.PlayerId2 = c.Id
                    ORDER BY a.GroupNumber ASC""")

    pairings = [
        {"player1": r[0], "player2": r[1], "round": r[2], "group": r[3]," id": r[4]}
        for r in rows
    ]

    return render_template("genereer_ronde.html", pairings=pairings)


@app.route("/swap-players", methods=["POST"])
def swap_players():
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
    cur.execute("SELECT PlayerId1, PlayerId2, RoundId, GroupNumber,Id FROM TempPairing WHERE PlayerId1=? OR PlayerId2=?", (player_a, player_a))
    match_a = cur.fetchone()
    cur.execute("SELECT PlayerId1, PlayerId2, RoundId, GroupNumber,Id FROM TempPairing WHERE PlayerId1=? OR PlayerId2=?", (player_b, player_b))
    match_b = cur.fetchone()

    if not match_a or not match_b:
        return jsonify({"success": False, "error": "One of the players not found"}), 400

    # Special case: both players are in the same pairing, just flip colors.
    if match_a[4] == match_b[4]:
        p1, p2, rnd, grp, row_id = match_a
        if {p1, p2} != {player_a, player_b}:
            conn.close()
            return jsonify({"success": False, "error": "Players are not opponents in the same match"}), 400

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
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("SELECT RoundId FROM TempPairing LIMIT 1")
    round_row = cur.fetchone()
    if not round_row:
        conn.close()
        return redirect("/genereer-ronde")

    round_id = round_row[0]
    cur.execute("DELETE FROM Pairings WHERE RoundId = ?", (round_id,))
    cur.execute("""
        INSERT INTO Pairings (PlayerId1, PlayerId2, RoundId, GroupNumber)
        SELECT PlayerId1, PlayerId2, RoundId, GroupNumber FROM TempPairing
    """)
    cur.execute("DELETE FROM TempPairing")
    conn.commit()
    conn.close()
    return redirect(f"/finalized_round?round_id={round_id}")

@app.route("/finalized_round")
def finalize_round():
    selected_round = request.args.get("round_id", type=int)
    if selected_round is None:
        selected_round = get_latest_round_with_pairings()

    if selected_round is None:
        return render_template("finalized-round.html", pairings=[], round_id=None)

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
    data = request.json
    # print("Received data:", data)  # Add this line for debugging
    pairing_id = data.get("pairing_id")
    result_id = data.get("result_id")
    print(pairing_id)
    if not pairing_id:
        return jsonify({"success": False, "error": "Missing data"}), 400

    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    if result_id in (None, ""):
        cur.execute("UPDATE Pairings SET ResultsType=NULL WHERE Id=?", (pairing_id,))
    else:
        cur.execute("UPDATE Pairings SET ResultsType=? WHERE Id=?", (int(result_id), pairing_id))
    conn.commit()
    conn.close()
    RefreshPlayersResults()

    return jsonify({"success": True})



@app.route("/save-results", methods=["POST"])
def save_results():
    round_id = request.form.get("round_id", type=int)
    if round_id is None:
        round_id = get_latest_round_with_pairings()

    if round_id is None:
        return jsonify({"success": False, "error": "No round found to save"}), 400

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
            {"success": False, "error": "Not all results are filled in for this round"}
        ), 400

    SaveResultsToPlayers(round_id)
    return redirect(f"/finalized_round?round_id={round_id}")

@app.route("/player_ranking")
def ranking():    
    RefreshPlayersResults()
    date = query_db("SELECT Date FROM Rounds WHERE Played = 1 ORDER BY ID DESC LIMIT 1", one=True)

    rows = query_db("""
        SELECT p.Id, p.Name,SUM(pr.Points) as TotalPoints,p.GroupNumber
        FROM Players p
        LEFT JOIN PlayersResults pr ON p.Id = pr.PlayerId
        GROUP BY p.Id, p.Name, p.GroupNumber
        ORDER BY p.GroupNumber ASC, TotalPoints DESC
    """)
    
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
    RefreshPlayersResults()
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
        WHERE a.PlayerId = ?
        ORDER BY a.Roundid DESC
    """, (player_id,))

    results = [
        {"opponent": r[0], "result_id": r[1], "Points": r[2],"Date": r[3], "Period": r[4]}
        for r in rows
    ]

    return render_template("player_results.html", player_id=player_id, player_name=player_name, results=results)

@app.route("/competition")
def competition():
    settings_rows = query_db(
        "SELECT Name, Value FROM Settings WHERE Name IN ('NumberOfPeriodRounds', 'NumberOfNonCompete', 'Year')"
    )
    settings_map = {name: value for name, value in settings_rows}
    rounds = query_db("SELECT Id, Date, Played FROM Rounds ORDER BY Id ASC")
    round_dates = [r[1] for r in rounds]
    return render_template(
        "competition.html",
        rounds=rounds,
        round_dates=round_dates,
        number_of_rounds=int(settings_map.get("NumberOfPeriodRounds", 0) or 0),
        non_compete=int(settings_map.get("NumberOfNonCompete", 0) or 0),
        competition_year=settings_map.get("Year", "-"),
    )

@app.route("/competition/reset-results", methods=["POST"])
def competition_reset_results():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    # Clear all historical round outcomes while keeping competition structure.
    cur.execute("DELETE FROM PlayersResults")
    cur.execute("UPDATE Pairings SET ResultsType = NULL")
    cur.execute("UPDATE Rounds SET Played = 0")
    cur.execute("DELETE FROM TempPairing")

    conn.commit()
    conn.close()
    return redirect("/competition")

@app.route("/competition/create", methods=["POST"])
def competition_create():
    number_of_rounds = request.form.get("number_of_rounds", type=int)
    non_compete = request.form.get("non_compete_rounds", type=int)
    round_dates = request.form.getlist("round_date")

    if number_of_rounds is None or number_of_rounds < 1:
        return jsonify({"success": False, "error": "Number of rounds must be at least 1"}), 400
    if non_compete is None or non_compete < 0:
        return jsonify({"success": False, "error": "Number of rounds between pairings must be 0 or more"}), 400
    if len(round_dates) != number_of_rounds:
        return jsonify({"success": False, "error": "Provide exactly one date per round"}), 400

    parsed_dates = []
    for d in round_dates:
        try:
            parsed_dates.append(datetime.strptime(d, "%Y-%m-%d").date())
        except ValueError:
            return jsonify({"success": False, "error": f"Invalid date format: {d}"}), 400

    year = parsed_dates[0].year

    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()

    # Start a clean competition.
    cur.execute("DELETE FROM PlayersResults")
    cur.execute("DELETE FROM Pairings")
    cur.execute("DELETE FROM TempPairing")
    cur.execute("DELETE FROM Present")
    cur.execute("DELETE FROM Rounds")

    rounds_to_insert = [
        (idx, "1", idx, year, parsed_dates[idx - 1].isoformat(), 0)
        for idx in range(1, number_of_rounds + 1)
    ]
    cur.executemany(
        "INSERT INTO Rounds (Id, Period, RoundNumber, Year, Date, Played) VALUES (?, ?, ?, ?, ?, ?)",
        rounds_to_insert,
    )

    cur.execute("SELECT Id FROM Players WHERE Active = 1 ORDER BY Id ASC")
    player_rows = cur.fetchall()
    present_rows = [
        (str(player_id), round_id, 1, None)
        for (player_id,) in player_rows
        for round_id in range(1, number_of_rounds + 1)
    ]
    cur.executemany(
        "INSERT INTO Present (PlayerId, RoundId, Present, ReasonAbsentId) VALUES (?, ?, ?, ?)",
        present_rows,
    )

    upsert_setting(cur, "NumberOfPeriodRounds", number_of_rounds, "Number of rounds in competition")
    upsert_setting(cur, "NumberOfNonCompete", non_compete, "Rounds between identical pairings")
    upsert_setting(cur, "Year", year, "Year the competition started in")

    conn.commit()
    conn.close()
    return redirect("/competition")

@app.route("/round-editor")
def round_editor():
    selected_round = request.args.get("round_id", type=int)
    if selected_round is None:
        selected_round = get_latest_editable_round()

    rounds = query_db(
        """
        SELECT r.Id, r.Date, r.Played
        FROM Rounds r
        WHERE r.Played = 1
           OR EXISTS (SELECT 1 FROM Pairings p WHERE p.RoundId = r.Id)
        ORDER BY r.Id DESC
        """
    )
    editable_round_ids = {r[0] for r in rounds}
    if selected_round not in editable_round_ids:
        selected_round = get_latest_editable_round()

    if selected_round is None:
        return render_template(
            "round_editor.html",
            round_id=None,
            rounds=rounds,
            date=None,
            round_played=None,
            players=[],
            reasons=[],
            pairings=[],
            present_players=[],
            unpaired_present_players=[],
        )

    round_meta = query_db("SELECT Date, Played FROM Rounds WHERE Id = ?", (selected_round,), one=True)
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
        """SELECT b.Name, c.Name, a.GroupNumber, a.ResultsType, a.Id, a.PlayerId1, a.PlayerId2
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
        players=players,
        reasons=reasons,
        pairings=pairings,
        present_players=present_players,
        unpaired_present_players=unpaired_present_players,
    )

@app.route("/round-editor/update-presence", methods=["POST"])
def round_editor_update_presence():
    round_id = request.form.get("round_id", type=int)
    if round_id is None:
        return jsonify({"success": False, "error": "Missing round id"}), 400
    editable_round = query_db(
        """
        SELECT r.Id
        FROM Rounds r
        WHERE r.Id = ?
          AND (r.Played = 1 OR EXISTS (SELECT 1 FROM Pairings p WHERE p.RoundId = r.Id))
        """,
        (round_id,),
        one=True,
    )
    if not editable_round:
        return jsonify({"success": False, "error": "Only played rounds or rounds with pairings can be edited here"}), 400

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
    RefreshPlayersResults()
    return redirect(f"/round-editor?round_id={round_id}")

@app.route("/round-editor/update-result", methods=["POST"])
def round_editor_update_result():
    data = request.json
    pairing_id = data.get("pairing_id")
    result_id = data.get("result_id")
    if not pairing_id:
        return jsonify({"success": False, "error": "Missing pairing id"}), 400

    pairing_round = query_db("SELECT RoundId FROM Pairings WHERE Id = ?", (pairing_id,), one=True)
    if not pairing_round:
        return jsonify({"success": False, "error": "Pairing not found"}), 400

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
    RefreshPlayersResults()
    return jsonify({"success": True})

@app.route("/round-editor/swap-players", methods=["POST"])
def round_editor_swap_players():
    data = request.json
    round_id = data.get("round_id")
    player_a_id = data.get("player_a_id")
    player_b_id = data.get("player_b_id")

    if not round_id or not player_a_id or not player_b_id:
        return jsonify({"success": False, "error": "Missing data"}), 400
    if str(player_a_id) == str(player_b_id):
        return jsonify({"success": False, "error": "Select two different players"}), 400

    round_row = query_db(
        """
        SELECT r.Id
        FROM Rounds r
        WHERE r.Id = ?
          AND EXISTS (SELECT 1 FROM Pairings p WHERE p.RoundId = r.Id)
        """,
        (round_id,),
        one=True,
    )
    if not round_row:
        return jsonify({"success": False, "error": "Round has no pairings to edit"}), 400

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
        return jsonify({"success": False, "error": "Both players must be marked present first"}), 400

    cur.execute("SELECT GroupNumber FROM Players WHERE Id = ?", (str(player_a_id),))
    group_a = cur.fetchone()
    cur.execute("SELECT GroupNumber FROM Players WHERE Id = ?", (str(player_b_id),))
    group_b = cur.fetchone()
    if not group_a or not group_b:
        conn.close()
        return jsonify({"success": False, "error": "Player not found"}), 400
    if group_a[0] != group_b[0]:
        conn.close()
        return jsonify({"success": False, "error": "Players must be in the same group"}), 400

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
            return jsonify({"success": False, "error": "Players are not in the same group pairings"}), 400

        if match_a[0] == match_b[0]:
            row_id, p1, p2, _ = match_a
            if {p1, p2} != {str(player_a_id), str(player_b_id)}:
                conn.close()
                return jsonify({"success": False, "error": "Players are not opponents in this round"}), 400
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
            return jsonify({"success": False, "error": "Cannot move player into a different group pairing"}), 400

        if p1 == out_id:
            p1 = in_id
        elif p2 == out_id:
            p2 = in_id
        else:
            conn.close()
            return jsonify({"success": False, "error": "Swap failed"}), 400

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
        conn.close()
        return jsonify({"success": False, "error": "Neither selected player is currently in a pairing"}), 400

    conn.commit()
    conn.close()
    RefreshPlayersResults()
    return jsonify({"success": True})

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))  # Render sets PORT
    app.run(host="0.0.0.0", port=port, debug=False)
