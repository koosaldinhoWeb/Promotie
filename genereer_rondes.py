import sqlite3
from collections import defaultdict
from itertools import combinations
import pandas as pd

def BuildNextRound():
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


def SaveResultsToPlayers():
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()

    # Haal de laatste ronde op
    cur.execute("SELECT min(Id) FROM Rounds where Played = 0")
    ActiveRound = cur.fetchone()[0]

    # Haal de resultaten op
    cur.execute("""SELECT a.PlayerId1, a.PlayerId2, a.ResultsType, a.GroupNumber,a.RoundId FROM Pairings a
                WHERE a.RoundId = ?""", (ActiveRound,))
    
    results = cur.fetchall()
    cur.execute("""SELECT Resultstype,GroupNumber,Points,Id FROM Results a""")
    points = cur.fetchall()

    #Reminder for the result: 1 White, wins 2 Black wins, 3 Draw, 5 uneven oppoent

    print(points)
    for player1_id, player2_id, result_type, group_number,RoundId in results:
        resultBlack = 0
        PointsBlack=0
        Points = 0
        print(player1_id, player2_id, result_type, group_number, RoundId)
        if result_type is not None:
            if player1_id == 999 or player2_id == 999:
                result_type=5
                resultBlack =5
            resultWhite = {1: 1, 2: 3, 3: 2}.get(result_type)
            resultBlack = {1: 3, 2: 1, 3: 2}.get(result_type)
        
            print(resultBlack, result_type)
            # print(resultBlack)
            for ResultType,GroupNumber,PointsResult,Id in points:
                if ResultType == resultWhite and GroupNumber == group_number:
                    # print(PointsResult)
                    Points = PointsResult
                    ResultsId = Id
                    break
            for ResultType,GroupNumber,PointsResult,Id in points:
                if ResultType == resultBlack and GroupNumber == group_number:
                    PointsBlack = PointsResult
                    ResultsIdBlack = Id
                    break
                 
            cur.execute("""INSERT INTO PlayersResults (PlayerId, OpponentId, ResultId, GroupNumber, RoundId,Points)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (player1_id, player2_id, ResultsId, group_number, RoundId, Points))
            cur.execute("""INSERT INTO PlayersResults (PlayerId, OpponentId, ResultId, GroupNumber, RoundId,Points)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (player2_id, player1_id, ResultsIdBlack, group_number, RoundId, PointsBlack))

    # Also update the results for the absent players. First lets get the number of times a player was absent in this year
    cur.execute("""SELECT Value FROM settings WHERE Name = 'Year'""")
    Year = cur.fetchone()[0]
    cur.execute("""SELECT PlayerId,Present,ReasonAbsentId,b.GroupNumber,a.RoundId FROM Present a
                LEFT JOIN Players b ON a.PlayerId = b.Id
                INNER JOIN Rounds c ON a.RoundId = c.Id
                WHERE a.Present = 0 AND c.Year = ?
                """, (Year,))
    absent_players = cur.fetchall()
    print(absent_players)
    for player_id, present, reason_id, group_number, RoundId in absent_players:
        if present == 0 and RoundId == ActiveRound:

            if reason_id =='':
                result_type = 4
                for r in points:
                    if r[0] == result_type and r[1] == group_number:
                        Points = r[2]
                        ResultsId = r[3]
                        break  
            else:
                for r in points:
                    if r[3] == reason_id and r[1] == group_number:
                        Points = r[2]
                        ResultsId = r[3]
                        break 

            cur.execute("""INSERT INTO PlayersResults (PlayerId, OpponentId, ResultId, GroupNumber, RoundId,Points)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (player_id, 998, ResultsId, group_number, RoundId, Points))
    cur.execute("UPDATE Rounds SET Played = 1 WHERE Id = ?", (ActiveRound,))

    conn.commit()
    conn.close()