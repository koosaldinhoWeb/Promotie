import sqlite3
from collections import defaultdict
from itertools import combinations
import pandas as pd

def RefreshPlayersResults():
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    cur.execute("DELETE FROM PlayersResults")

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
        if player1_id == 999 or player2_id == 999:
            real_player = player2_id if player1_id == 999 else player1_id
            real_player_present = present_by_round_player.get((round_id, real_player), 1)
            if real_player_present != 1:
                continue
            uneven = points_by_type_group.get((5, group_number))
            if uneven is None:
                continue
            points, result_id = uneven
            cur.execute(
                """INSERT INTO PlayersResults (PlayerId, OpponentId, ResultId, GroupNumber, RoundId, Points)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (real_player, 999, result_id, group_number, round_id, points),
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
        player1_present = present_by_round_player.get((round_id, player1_id), 1)
        player2_present = present_by_round_player.get((round_id, player2_id), 1)

        if player1_present == 1:
            cur.execute(
                """INSERT INTO PlayersResults (PlayerId, OpponentId, ResultId, GroupNumber, RoundId, Points)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (player1_id, player2_id, white_result_id, group_number, round_id, white_points),
            )
        if player2_present == 1:
            cur.execute(
                """INSERT INTO PlayersResults (PlayerId, OpponentId, ResultId, GroupNumber, RoundId, Points)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (player2_id, player1_id, black_result_id, group_number, round_id, black_points),
            )

    cur.execute(
        """SELECT a.PlayerId, a.Present, a.ReasonAbsentId, b.GroupNumber, a.RoundId
           FROM Present a
           LEFT JOIN Players b ON a.PlayerId = b.Id
           INNER JOIN Rounds c ON a.RoundId = c.Id
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
            """INSERT INTO PlayersResults (PlayerId, OpponentId, ResultId, GroupNumber, RoundId, Points)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (player_id, 998, result_id, group_number, round_id, points),
        )

    conn.commit()
    conn.close()

def BuildNextRound():
    RefreshPlayersResults()

    # Verbinden met de bestaande database
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    # Instellen van de nieuwe ronde

    # 1. Haal de laatste ronde op
    cur.execute("SELECT min(Id) FROM Rounds where Played = 0")
    RoundId = cur.fetchone()[0]

    # 2. Bepaal de presentie van de spelers en het aantal punten
    cur.execute("""SELECT a.PlayerId,a.Present,a.ReasonAbsentId,c.GroupNumber,COALESCE(c.Rating,0) as Rating,SUM(COALESCE(b.Points,0)) as TotalPoints FROM Present a
                left join PlayersResults b on a.PlayerId = b.PlayerId
                left join Players c on a.PlayerId = c.Id
                WHERE a.RoundId = ?
                GROUP BY a.PlayerId,a.Present,a.ReasonAbsentId,c.GroupNumber,c.Rating
                """, (RoundId,))

    All_players= cur.fetchall()
    Headers = [desc[0] for desc in cur.description]

    df_All_players = pd.DataFrame(All_players, columns=Headers)
    df_All_players['Matched']=0

    # 3. Maak een lijst alle tegenstanders van vorige rondes

    cur.execute("""SELECT Value FROM settings WHERE Name = 'NumberOfNonCompete'""")
    NumberOfNonCompete = cur.fetchone()[0]

    cur.execute("""SELECT Value FROM settings WHERE Name = 'Year'""")
    Year = cur.fetchone()[0]

    cur.execute("""SELECT a.PlayerId,a.OpponentId FROM PlayersResults a
                inner join Rounds b on a.RoundId = b.Id
                WHERE a.RoundId-? = ?
                and b.year = ?

                """, (NumberOfNonCompete,RoundId,Year))

    nonMatchingPlayers = cur.fetchall()
    Headers = [desc[0] for desc in cur.description]

    df_nonMatchingPlayers = pd.DataFrame(nonMatchingPlayers, columns=Headers)

    TempPairings = []

    df_To_SortPlayers = df_All_players[df_All_players['Present'] == 1]
    extra_players = []

    for group, group_players in df_To_SortPlayers.groupby('GroupNumber'):
        num_present = len(group_players)
        if num_present % 2 != 0:
            print(f"Group {group} has an uneven number of present players: {num_present}")
            UnevenPlayer = {
                'PlayerId': 999, # Cant add the same ID twice. But this needs to be solved differently
                'Present': 1,
                'ReasonAbsentId': None,
                'GroupNumber': group,
                'Rating': 0,
                'TotalPoints': 0,
                'Matched': 0
            }
            extra_players.append(UnevenPlayer)
        else:   
            print(f"Group {group} has an even number of present players: {num_present}")
    if extra_players:
        df_To_SortPlayers = pd.concat([df_To_SortPlayers, pd.DataFrame(extra_players)], ignore_index=True)

    df_To_MatchPlayers = df_To_SortPlayers.sort_values(by=['TotalPoints','Rating'], ascending=[False,False])
    print(df_To_MatchPlayers)
    for idx,player in df_To_MatchPlayers.iterrows():
        player_id = player['PlayerId']
        group_number = player['GroupNumber']
        if df_To_MatchPlayers.loc[df_To_MatchPlayers['PlayerId'] == player_id, 'Matched'].iloc[0] == 1:
            continue
        # Get all opponents that player cannot play against
        forbidden_opponents = df_nonMatchingPlayers[df_nonMatchingPlayers['PlayerId'] == player_id]['OpponentId'].tolist()
        # Find first opponent not in forbidden_opponents and not the player himself
        opponent = df_To_MatchPlayers[
            (~df_To_MatchPlayers['PlayerId'].isin(forbidden_opponents)) &
            (df_To_MatchPlayers['PlayerId'] != player_id) &
            (df_To_MatchPlayers['GroupNumber'] == group_number) &
            (df_To_MatchPlayers['Matched'] == 0)
        ].head(1)
        print(opponent)
        if not opponent.empty:
            opponent_id = opponent.iloc[0]['PlayerId']
            print(f"Player {player_id} can play against {opponent.iloc[0]['PlayerId']}")
            df_To_MatchPlayers.loc[
                (df_To_MatchPlayers['PlayerId'] == player_id) & 
                (df_To_MatchPlayers['GroupNumber'] == group_number)
                ,'Matched'] = 1
            df_To_MatchPlayers.loc[
                (df_To_MatchPlayers['PlayerId'] == opponent_id) &
                (df_To_MatchPlayers['GroupNumber'] == group_number)
                ,'Matched'] = 1
            TempPairings.append((player_id, opponent_id,group_number))
        else:
            print(f"Player {player_id} has no available opponents.{group_number}")

    cur.execute("DELETE FROM TempPairing")
    conn.commit()
    insert_query = """
    INSERT INTO TempPairing (PlayerId1, PlayerId2, RoundId, GroupNumber)
    VALUES (?,?, ?, ?)
    """

    data_to_insert = [(pair[0], pair[1],RoundId,pair[2]) for pair in TempPairings]

    cur.executemany(insert_query, data_to_insert)
    conn.commit()


def SaveResultsToPlayers(round_id=None):
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    # Determine round to process (explicit round or active unplayed round)
    if round_id is None:
        cur.execute("SELECT min(Id) FROM Rounds where Played = 0")
        ActiveRound = cur.fetchone()[0]
    else:
        ActiveRound = round_id

    if ActiveRound is None:
        conn.close()
        return

    # Only close/finalize the round. Points/results are recalculated on demand.
    cur.execute("UPDATE Rounds SET Played = 1 WHERE Id = ?", (ActiveRound,))

    conn.commit()
    conn.close()
