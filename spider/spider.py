import hashlib
import json
import datetime
import logging
import pickle
import psycopg2
import urllib.request

BASE_URL = "http://api.paladins.com/paladinsapi.svc"
RESPONSE_FORMAT = "Json"

class Credentials():
    def __init__(self, obj):
        self.dev_id = obj["devId"]
        self.auth_key = obj["authKey"]
    def __str__(self):
        return f"{self.dev_id}:{self.auth_key}"

class Player():
    def __init__(self, response):
        self.created_datetime = response["Created_Datetime"]
        self.hours_played = response["HoursPlayed"]
        self.id = response["Id"]
        self.last_login_datetime = response["Last_Login_Datetime"]
        self.leaves = response["Leaves"]
        self.level = response["Level"]
        self.losses = response["Losses"]
        self.mastery_level = response["MasteryLevel"]
        self.name = response["Name"]
        self.personal_status_message = response["Personal_Status_Message"]
        self.platform = response["Platform"]
        self.ranked_conquest = response["RankedConquest"]
        self.region = response["Region"]
        self.team_name = response["Team_Name"]
        self.teamid = response["TeamId"]
        self.tier_conquest = response["Tier_Conquest"]
        self.total_achievements = response["Total_Achievements"]
        self.total_worshippers = response["Total_Worshippers"]
        self.wins = response["Wins"]

CREDENTIALS = None
with open('dev-key.json', 'r') as fp:
    json_credentials = json.load(fp)
    CREDENTIALS = Credentials(json_credentials)

def signature(credentials, method_name):
    logging.debug(credentials)
    logging.debug(method_name)

    now = datetime.datetime.utcnow()
    timestamp = now.strftime("%Y%m%d%H%M%S")

    logging.debug(now)
    logging.debug(timestamp)

    payload = f"{credentials.dev_id}{method_name}{credentials.auth_key}{timestamp}".encode('utf-8')
    signature = hashlib.md5(payload).hexdigest()
    logging.debug(signature)

    return signature, timestamp

def create_session(credentials):
    method = "createsession"
    sig, timestamp = signature(credentials, method)
    endpoint = f"{BASE_URL}/{method}{RESPONSE_FORMAT}/{credentials.dev_id}/{sig}/{timestamp}"

    contents = urllib.request.urlopen(endpoint).read()
    response = json.loads(contents)
    logging.debug(response)
    if response['ret_msg'] != 'Approved':
        print(contents)
        print(response['ret_msg'])
        return
        # raise ValueError()

    return response['session_id']

def get_player(player_name, credentials, session):
    method = "getplayer"
    sig, timestamp = signature(credentials, method)
    encoded_player_name = urllib.request.quote(player_name.encode('utf-8'))
    endpoint = f"{BASE_URL}/{method}{RESPONSE_FORMAT}/{credentials.dev_id}/{sig}/{session.id}/{timestamp}/{encoded_player_name}"
    logging.debug(endpoint)

    contents = urllib.request.urlopen(endpoint).read()
    response = json.loads(contents)
    logging.debug(response[0])
    return Player(response[0])

def get_match_history(player, credentials, session):
    method = "getmatchhistory"
    sig, timestamp = signature(credentials, method)
    logging.debug(player.id)

    endpoint = f"{BASE_URL}/{method}{RESPONSE_FORMAT}/{credentials.dev_id}/{sig}/{session.id}/{timestamp}/{player.id}"
    logging.debug(endpoint)

    contents = urllib.request.urlopen(endpoint).read()
    print(contents.decode('utf-8'))
    # response = json.loads(contents)
    # print(response)

def get_match_details(match_id, credentials, session):
    method = "getmatchdetails"
    sig, timestamp = signature(credentials, method)

    endpoint = f"{BASE_URL}/{method}{RESPONSE_FORMAT}/{credentials.dev_id}/{sig}/{session.id}/{timestamp}/{match_id}"
    logging.debug(endpoint)

    contents = urllib.request.urlopen(endpoint).read()
    response = json.loads(contents)

    conn = psycopg2.connect("dbname=docker user=docker password=docker host=192.168.99.101")
    cur = conn.cursor()
    for obj in response:
        md = MatchDetails(obj)
        print(md.as_tuple())

        insert_query = "INSERT INTO match_details (account_level,assists,champion,damage_dealt,damage_taken,deaths,credits,match_date,self_healing,healing,shielding,loadout_card1,loadout_card2,loadout_card3,loadout_card4,loadout_card5,loadout_card1_level,loadout_card2_level,loadout_card3_level,loadout_card4_level,loadout_card5_level,item1,item2,item3,item4,item1_level,item2_level,item3_level,item4_level,talent,streak,kills,map,match_id,match_duration,highest_multi_kill,objective_time,party_id,platform,region,team1_score,team2_score,team,win_status,player_id,player_name,master_level) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);"

        values = md.as_tuple()
        cur.execute(insert_query, values)

    conn.commit()

    cur.close()
    conn.close()


def get_data_used(credentials, session):
    method = "getdataused"
    sig, timestamp = signature(credentials, method)

    endpoint = f"{BASE_URL}/{method}{RESPONSE_FORMAT}/{credentials.dev_id}/{sig}/{session.id}/{timestamp}"
    logging.debug(endpoint)

    contents = urllib.request.urlopen(endpoint).read()
    print(contents.decode('utf-8'))
    # response = json.loads(contents)
    # print(response)

class Session():
    SESSION_LENGTH = 15*60
    def __init__(self, credentials):
        self.id = create_session(credentials)
        self.created = datetime.datetime.now()

    def is_alive(self):
        diff = datetime.datetime.now()-self.created
        return diff.seconds < self.SESSION_LENGTH

    def save(self):
        with open('/tmp/asdf.pickle', 'wb') as fp:
            pickle.dump(self, fp)

def load_or_create_session():
    session = None
    try:
        with open('/tmp/asdf.pickle', 'rb') as fp:
            session = pickle.load(fp)
            if not session.is_alive():
                print("NOT ALIVE")
                session = Session(CREDENTIALS)
                session.save()
    except Exception as e:
        logging.error(e)
        session = Session(CREDENTIALS)
        session.save()

    return session


from enum import Enum
class GameMode(Enum):
    siege     = 424
    onslaught = 452
    tdm       = 469 # Team deatchmatch
    ranked    = 428

def get_match_ids_by_queue(gameplay_mode, date, hour, credentials, session):
    method = "getmatchidsbyqueue"
    sig, timestamp = signature(credentials, method)

    endpoint = f"{BASE_URL}/{method}{RESPONSE_FORMAT}/{credentials.dev_id}/{sig}/{session.id}/{timestamp}/{gameplay_mode.value}/{date}/{hour}"
    logging.debug(endpoint)

    contents = urllib.request.urlopen(endpoint).read()
    print(contents.decode('utf-8'))
    # response = json.loads(contents)
    # print(response)

class MatchDetails():
    def __init__(self, response):
        self.account_level = response['Account_Level']
        self.assists = response['Assists']
        self.champion = response['Reference_Name']
        self.damage_dealt = response['Damage_Player']
        self.damage_taken = response['Damage_Taken']
        self.deaths = response['Deaths']
        self.credits = response['Gold_Earned']
        self.date = response['Entry_Datetime']
        self.self_healing = response['Healing_Player_Self']
        self.healing = response['Healing']
        self.shielding = response['Damage_Mitigated']
        self.loadout_card1 = response['Item_Purch_1']
        self.loadout_card2 = response['Item_Purch_2']
        self.loadout_card3 = response['Item_Purch_3']
        self.loadout_card4 = response['Item_Purch_4']
        self.loadout_card5 = response['Item_Purch_5']
        self.loadout_card1_level = response['ItemLevel1']
        self.loadout_card2_level = response['ItemLevel2']
        self.loadout_card3_level = response['ItemLevel3']
        self.loadout_card4_level = response['ItemLevel4']
        self.loadout_card5_level = response['ItemLevel5']
        self.item1 = response['Item_Active_1']
        self.item2 = response['Item_Active_2']
        self.item3 = response['Item_Active_3']
        self.item4 = response['Item_Active_4']
        self.item1_level = response['ActiveLevel1']
        self.item2_level = response['ActiveLevel2']
        self.item3_level = response['ActiveLevel3']
        self.item4_level = response['ActiveLevel4']
        self.talent = response['Item_Purch_6']
        self.streak = response['Killing_Spree']
        self.kills = response['Kills_Player']
        self.map = response['Map_Game']
        self.match_id = response['Match']
        self.match_duration = response['Time_In_Match_Seconds']
        self.highest_multi_kill = response['Multi_kill_Max']
        self.objective_time = response['Objective_Assists']
        self.party_id = response['PartyId']
        self.platform = response['Platform']
        self.region = response['Region']
        self.team1_score = response['Team1Score']
        self.team2_score = response['Team2Score']
        self.team = response['TaskForce']
        self.win_status = response['Win_Status']
        self.player_id = response['playerId']
        self.player_name = response['playerName']
        self.master_level = response['Mastery_Level']

    def as_tuple(self):
        return list(self.__dict__.values())

def wrap():
    session = load_or_create_session()

    # player_name = "döskalle"

    # gameplay_mode = GameMode.siege
    # date = "20181026"
    # hour = "17,00"

    match_id = 291040887
    get_match_details(match_id, CREDENTIALS, session)
    # get_match_ids_by_queue(gameplay_mode, date, hour, CREDENTIALS, session)

    # get_data_used(CREDENTIALS, session)
    # player = get_player(player_name, CREDENTIALS, session)
    # match_history = get_match_history(player, CREDENTIALS, session)

if __name__ == "__main__":
    wrap()

