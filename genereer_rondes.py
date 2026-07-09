import sqlite3

import pandas as pd


PLAYERS_RESULTS_COLUMNS = """
    PlayerId,
    OpponentId,
    ResultId,
    GroupNumber,
    RoundId,
    Points
"""


def create_players_results_table(cur, table_name, temporary=False):
    table_type = "TEMP TABLE" if temporary else "TABLE"
    cur.execute(
        f"""
        CREATE {table_type} IF NOT EXISTS {table_name}(
            PlayerId TEXT,
            OpponentId TEXT,
            ResultId INTEGER,
            GroupNumber INTEGER,
            RoundId INTEGER,
            Points REAL
        )
        """
    )


def populate_players_results(cur, table_name):
    cur.execute(f"DELETE FROM {table_name}")

    cur.execute("""SELECT Id, Resultstype, GroupNumber, Points FROM Results""")
    results_rows = cur.fetchall()
    points_by_type_group = {}
    points_by_id = {}
    for result_id, result_type, group_number, points in results_rows:
        points_by_type_group[(result_type, group_number)] = (points, result_id)
        points_by_id[result_id] = (group_number, points)

    cur.execute(
        """SELECT PlayerId, RoundId, Present
           FROM Present"""
    )
    present_rows = cur.fetchall()
    present_by_round_player = {
        (round_id, player_id): present for player_id, round_id, present in present_rows
    }

    cur.execute(
        """SELECT a.PlayerId1, a.PlayerId2, a.ResultsType, a.GroupNumber, a.RoundId
           FROM Pairings a
           INNER JOIN Rounds b ON a.RoundId = b.Id
           WHERE b.Played = 1"""
    )
    played_pairings = cur.fetchall()

    for player1_id, player2_id, result_type, group_number, round_id in played_pairings:
        player1_key = str(player1_id)
        player2_key = str(player2_id)

        if player1_key == "NONE" or player2_key == "NONE":
            # Placeholder pairing rows in round editor should not generate points.
            continue

        if player1_key == "999" or player2_key == "999":
            real_player_key = player2_key if player1_key == "999" else player1_key
            real_player_present = present_by_round_player.get((round_id, real_player_key), 1)
            if real_player_present != 1:
                continue
            uneven = points_by_type_group.get((5, group_number))
            if uneven is None:
                continue
            points, result_id = uneven
            cur.execute(
                f"""INSERT INTO {table_name} ({PLAYERS_RESULTS_COLUMNS})
                    VALUES (?, ?, ?, ?, ?, ?)""",
                (real_player_key, "999", result_id, group_number, round_id, points),
            )
            continue

        if result_type is None:
            continue

        result_white = {1: 1, 2: 3, 3: 2}.get(result_type)
        result_black = {1: 3, 2: 1, 3: 2}.get(result_type)
        if result_white is None or result_black is None:
            continue

        white_data = points_by_type_group.get((result_white, group_number))
        black_data = points_by_type_group.get((result_black, group_number))
        if white_data is None or black_data is None:
            continue

        white_points, white_result_id = white_data
        black_points, black_result_id = black_data
        player1_present = present_by_round_player.get((round_id, player1_key), 1)
        player2_present = present_by_round_player.get((round_id, player2_key), 1)

        if player1_present == 1:
            cur.execute(
                f"""INSERT INTO {table_name} ({PLAYERS_RESULTS_COLUMNS})
                    VALUES (?, ?, ?, ?, ?, ?)""",
                (player1_key, player2_key, white_result_id, group_number, round_id, white_points),
            )
        if player2_present == 1:
            cur.execute(
                f"""INSERT INTO {table_name} ({PLAYERS_RESULTS_COLUMNS})
                    VALUES (?, ?, ?, ?, ?, ?)""",
                (player2_key, player1_key, black_result_id, group_number, round_id, black_points),
            )

    cur.execute(
        """SELECT
                a.PlayerId,
                a.Present,
                a.ReasonAbsentId,
                COALESCE(cpg.GroupNumber, b.GroupNumber) AS GroupNumber,
                a.RoundId
           FROM Present a
           LEFT JOIN Players b ON a.PlayerId = b.Id
           INNER JOIN Rounds c ON a.RoundId = c.Id
           INNER JOIN CompetitionPlayers cp
               ON cp.PlayerId = a.PlayerId
              AND cp.CompetitionId = c.CompetitionId
           LEFT JOIN CompetitionPlayerGroups cpg
               ON cpg.PlayerId = a.PlayerId
              AND cpg.CompetitionId = c.CompetitionId
           WHERE a.Present = 0 AND c.Played = 1"""
    )
    absent_players = cur.fetchall()

    for player_id, present, reason_id, group_number, round_id in absent_players:
        if present != 0:
            continue

        if reason_id is None:
            default_absent = points_by_type_group.get((4, group_number))
            if default_absent is None:
                continue
            points, result_id = default_absent
        else:
            reason_data = points_by_id.get(reason_id)
            if reason_data is None:
                default_absent = points_by_type_group.get((4, group_number))
                if default_absent is None:
                    continue
                points, result_id = default_absent
            else:
                _, points = reason_data
                result_id = reason_id

        cur.execute(
            f"""INSERT INTO {table_name} ({PLAYERS_RESULTS_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?)""",
            (player_id, 998, result_id, group_number, round_id, points),
        )


def RefreshPlayersResults(database="database.db"):
    conn = sqlite3.connect(database)
    cur = conn.cursor()
    create_players_results_table(cur, "PlayersResults")
    populate_players_results(cur, "PlayersResults")
    conn.commit()
    conn.close()


def create_temp_players_results(cur):
    table_name = "TempPlayersResults"
    create_players_results_table(cur, table_name, temporary=True)
    populate_players_results(cur, table_name)
    return table_name

def get_next_round_id(cur, competition_id):
    cur.execute(
        "SELECT min(Id) FROM Rounds WHERE Played = 0 AND CompetitionId = ?",
        (competition_id,),
    )
    return cur.fetchone()[0]


def get_competition_settings_type(cur, competition_id):
    cur.execute(
        """
        SELECT SettingsType
        FROM CompetitionSettings
        WHERE CompetitionId = ?
        """,
        (competition_id,),
    )
    row = cur.fetchone()
    if row and row[0] in {"swiss", "percentage"}:
        return row[0]

    cur.execute("SELECT Value FROM Settings WHERE Name = 'CompType'")
    row = cur.fetchone()
    settings_type = (row[0] if row else "percentage").lower()
    return settings_type if settings_type in {"swiss", "percentage"} else "percentage"


def get_rankings_per_group(cur, competition_id, round_id, results_table="PlayersResults"):
    settings_type = get_competition_settings_type(cur, competition_id)
    cur.execute(
        f"""
        SELECT
            a.PlayerId,
            a.Present,
            a.ReasonAbsentId,
            COALESCE(cpg.GroupNumber, c.GroupNumber) AS GroupNumber,
            COALESCE(c.Rating, 0) AS Rating,
            SUM(COALESCE(b.Points, 0)) AS TotalPoints,
            SUM(
                CASE
                    WHEN d.Resultstype = 1 THEN 1.0
                    WHEN d.Resultstype = 2 THEN 0.5
                    ELSE 0
                END
            ) AS MatchScore,
            SUM(CASE WHEN d.Resultstype IN (1, 2, 3) THEN 1 ELSE 0 END) AS MatchesPlayed
        FROM Present a
        LEFT JOIN {results_table} b
            ON a.PlayerId = b.PlayerId
           AND b.RoundId IN (
                SELECT Id FROM Rounds WHERE CompetitionId = ?
           )
        LEFT JOIN Results d ON b.ResultId = d.Id
        LEFT JOIN Players c ON a.PlayerId = c.Id
        INNER JOIN CompetitionPlayers cp
            ON cp.PlayerId = a.PlayerId
           AND cp.CompetitionId = ?
        LEFT JOIN CompetitionPlayerGroups cpg
            ON cpg.PlayerId = a.PlayerId
           AND cpg.CompetitionId = cp.CompetitionId
        WHERE a.RoundId = ?
        GROUP BY a.PlayerId, a.Present, a.ReasonAbsentId, COALESCE(cpg.GroupNumber, c.GroupNumber), c.Rating
        """,
        (competition_id, competition_id, round_id),
    )
    player_rows = cur.fetchall()
    headers = [desc[0] for desc in cur.description]
    players = pd.DataFrame(player_rows, columns=headers)

    if players.empty:
        return {}

    present_players = players[players["Present"] == 1].copy()
    if present_players.empty:
        return {}

    rankings_by_group = {}
    for group_number, group_players in present_players.groupby("GroupNumber"):
        group_players = group_players.copy()
        group_players["MatchPercentage"] = group_players.apply(
            lambda player: (
                player["MatchScore"] / player["MatchesPlayed"]
                if player["MatchesPlayed"]
                else 0
            ),
            axis=1,
        )
        sort_columns = ["TotalPoints", "Rating"]
        if settings_type == "percentage":
            sort_columns = ["MatchPercentage", "MatchScore", "MatchesPlayed"]

        ranked_players = group_players.sort_values(
            by=sort_columns,
            ascending=[False] * len(sort_columns),
        ).copy()
        ranked_players["Matched"] = 0

        if len(ranked_players) % 2 != 0:
            bye_player = pd.DataFrame(
                [
                    {
                        "PlayerId": 999,
                        "Present": 1,
                        "ReasonAbsentId": None,
                        "GroupNumber": group_number,
                        "Rating": 0,
                        "TotalPoints": 0,
                        "MatchScore": 0,
                        "MatchesPlayed": 0,
                        "MatchPercentage": 0,
                        "Matched": 0,
                    }
                ]
            )
            ranked_players = pd.concat(
                [ranked_players, bye_player],
                ignore_index=True,
            )

        rankings_by_group[group_number] = ranked_players.reset_index(drop=True)

    return rankings_by_group


def get_non_matching_players(cur, competition_id, round_id, results_table="PlayersResults"):
    cur.execute(
        "SELECT NumberOfNonCompete FROM Competitions WHERE Id = ?",
        (competition_id,),
    )
    setting_row = cur.fetchone()
    number_of_non_compete = int(setting_row[0]) if setting_row else 0

    cur.execute(
        f"""SELECT a.PlayerId, a.OpponentId
           FROM {results_table} a
           INNER JOIN Rounds b ON a.RoundId = b.Id
           WHERE b.CompetitionId = ?
             AND b.RoundNumber >= (
                 SELECT RoundNumber FROM Rounds WHERE Id = ?
             ) - ?
             AND b.RoundNumber < (
                 SELECT RoundNumber FROM Rounds WHERE Id = ?
             )""",
        (competition_id, round_id, number_of_non_compete, round_id),
    )
    rows = cur.fetchall()
    headers = [desc[0] for desc in cur.description]
    return pd.DataFrame(rows, columns=headers)


def build_pairings_from_rankings(rankings_by_group, non_matching_players):
    temp_pairings = []

    for group_number, ranked_players in rankings_by_group.items():
        for _, player in ranked_players.iterrows():
            player_id = player["PlayerId"]
            if ranked_players.loc[ranked_players["PlayerId"] == player_id, "Matched"].iloc[0] == 1:
                continue

            forbidden_opponents = non_matching_players[
                non_matching_players["PlayerId"] == player_id
            ]["OpponentId"].tolist()
            opponent = ranked_players[
                (~ranked_players["PlayerId"].isin(forbidden_opponents)) &
                (ranked_players["PlayerId"] != player_id) &
                (ranked_players["Matched"] == 0)
            ].head(1)

            if opponent.empty:
                continue

            opponent_id = opponent.iloc[0]["PlayerId"]
            ranked_players.loc[ranked_players["PlayerId"] == player_id, "Matched"] = 1
            ranked_players.loc[ranked_players["PlayerId"] == opponent_id, "Matched"] = 1
            temp_pairings.append((player_id, opponent_id, group_number))

    return temp_pairings


def save_temp_pairings(cur, competition_id, round_id, temp_pairings):
    cur.execute(
        """DELETE FROM TempPairing
           WHERE RoundId IN (SELECT Id FROM Rounds WHERE CompetitionId = ?)""",
        (competition_id,),
    )
    cur.executemany(
        """
        INSERT INTO TempPairing (PlayerId1, PlayerId2, RoundId, GroupNumber)
        VALUES (?, ?, ?, ?)
        """,
        [(pair[0], pair[1], round_id, pair[2]) for pair in temp_pairings],
    )


def BuildNextRound(competition_id, database="database.db"):
    conn = sqlite3.connect(database)
    cur = conn.cursor()

    round_id = get_next_round_id(cur, competition_id)
    if round_id is None:
        conn.close()
        return

    results_table = create_temp_players_results(cur)
    rankings_by_group = get_rankings_per_group(
        cur,
        competition_id,
        round_id,
        results_table,
    )
    non_matching_players = get_non_matching_players(
        cur,
        competition_id,
        round_id,
        results_table,
    )
    temp_pairings = build_pairings_from_rankings(
        rankings_by_group,
        non_matching_players,
    )

    save_temp_pairings(cur, competition_id, round_id, temp_pairings)
    conn.commit()
    conn.close()


def SaveResultsToPlayers(competition_id, round_id=None, database="database.db"):
    conn = sqlite3.connect(database)
    cur = conn.cursor()

    # Determine round to process (explicit round or active unplayed round)
    if round_id is None:
        cur.execute(
            "SELECT min(Id) FROM Rounds WHERE Played = 0 AND CompetitionId = ?",
            (competition_id,),
        )
        ActiveRound = cur.fetchone()[0]
    else:
        ActiveRound = round_id

    if ActiveRound is None:
        conn.close()
        return

    # Only close/finalize the round. Points/results are recalculated on demand.
    cur.execute(
        "UPDATE Rounds SET Played = 1 WHERE Id = ? AND CompetitionId = ?",
        (ActiveRound, competition_id),
    )

    conn.commit()
    conn.close()
