import sqlite3
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

    # Extract original data
    def swap_player_in_match(match, swap_out, swap_in):
        p1, p2, rnd, grp, id = match
        if p1 == swap_out:
            p1 = swap_in
        elif p2 == swap_out:
            p2 = swap_in
        return p1, p2, rnd, grp ,id

    # Swap players in both matches
    new_a = swap_player_in_match(match_a, player_a, player_b)
    new_b = swap_player_in_match(match_b, player_b, player_a)

    # Update DB for both matches
    cur.execute("""
        UPDATE TempPairing
        SET PlayerId1=?, PlayerId2=?
        WHERE RoundId=? AND GroupNumber=? and Id = ?
    """, (new_a[0], new_a[1], new_a[2], new_a[3],new_a[4]))
    cur.execute("""
        UPDATE TempPairing
        SET PlayerId1=?, PlayerId2=?
        WHERE RoundId=? AND GroupNumber=? and Id = ?
    """, (new_b[0], new_b[1], new_b[2], new_b[3],new_b[4]))

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

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))  # Render sets PORT
    app.run(host="0.0.0.0", port=port, debug=False)
