import hashlib
import json
import datetime
import logging
import pickle
import urllib.request
import threading

BASE_URL = "http://api.paladins.com/paladinsapi.svc"
RESPONSE_FORMAT = "Json"

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

class Credentials():
    def __init__(self, obj):
        self.dev_id = obj["devId"]
        self.auth_key = obj["authKey"]
    def __str__(self):
        return f"{self.dev_id}:{self.auth_key}"

class PaladinsAPI(object):
    MAX_MATCH_BATCH = 25
    def __init__(self, credentials, session):
        self.session = session
        self.credentials = credentials

    def base_url(self, method):
        sig, timestamp = signature(self.credentials, method)
        if not self.session.is_alive():
            self.session.renew(self.credentials)

        return f"{BASE_URL}/{method}{RESPONSE_FORMAT}/{self.credentials.dev_id}/{sig}/{self.session.id}/{timestamp}"

    def _request(self, endpoint):
        self.session.handler.allow_request()
        contents = urllib.request.urlopen(endpoint).read()
        return contents

    def get_player(self, player_name):
        method = "getplayer"

        encoded_player_name = urllib.request.quote(player_name.encode('utf-8'))
        endpoint = f"{self.base_url(method)}/{encoded_player_name}"
        logging.debug(endpoint)

        contents = self._request(endpoint)
        response = json.loads(contents)
        logging.debug(response[0])
        return Player(response[0])

    def get_match_history(self, player):
        method = "getmatchhistory"
        logging.debug(player.id)

        endpoint = f"{self.base_url(method)}/{player.id}"
        logging.debug(endpoint)

        contents = self._request(endpoint)
        print(contents.decode('utf-8'))
        # response = json.loads(contents)
        # print(response)

    def get_match_batch(self, match_ids):
        # Create a function called "chunks" with two arguments, l and n:
        def chunks(l, n):
            # For item i in a range that is a length of l,
            for i in range(0, len(l), n):
                # Create an index range for l of n items:
                yield l[i:i+n]

        match_batches = chunks(match_ids, self.MAX_MATCH_BATCH)

        matches = []

        for match_batch in match_batches:
            ms = self.get_match_details_batch(match_batch)
            matches.extend(ms)

        return matches

    def get_match_details_batch(self, match_ids):
        method = "getmatchdetailsbatch"

        match_ids_string = ",".join(match_ids)
        endpoint = f"{self.base_url(method)}/{match_ids_string}"
        logging.debug(endpoint)

        contents = self._request(endpoint)
        matches = json.loads(contents)
        return matches


    def get_match_ids_by_queue(self, gameplay_mode, date, hour):
        method = "getmatchidsbyqueue"

        endpoint = f"{self.base_url(method)}/{gameplay_mode.value}/{date}/{hour}"
        logging.debug(endpoint)

        contents = self._request(endpoint)
        response = json.loads(contents)
        match_ids = [ obj["Match"] for obj in response ]
        return match_ids


    def get_data_used(self):
        method = "getdataused"

        endpoint = f"{self.base_url(method)}"
        logging.debug(endpoint)

        contents = self._request(endpoint)

        print(contents.decode('utf-8'))
        data_usage = json.loads(contents)
        return data_usage

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

class Session():
    _SESSION_LENGTH = 15*60
    def __init__(self, credentials, handler):
        self.handler = handler
        self.id = self._create(credentials)
        self.created = datetime.datetime.now()

    def _request(self, endpoint):
        self.handler.allow_request()
        contents = urllib.request.urlopen(endpoint).read()
        return contents

    def _create(self, credentials):
        method = "createsession"

        sig, timestamp = signature(credentials, method)

        # NOTE: this URL is different from the rest of the API calls, since it
        # lacks the session id (obviously).
        endpoint = f"{BASE_URL}/{method}{RESPONSE_FORMAT}/{credentials.dev_id}/{sig}/{timestamp}"

        contents = self._request(endpoint)
        session_obj = json.loads(contents)
        logging.debug(session_obj)
        if session_obj['ret_msg'] != 'Approved':
            logging.error(session_obj['ret_msg'])
            return

        return session_obj['session_id']

    def is_alive(self):
        diff = datetime.datetime.now()-self.created
        return diff.seconds < self._SESSION_LENGTH

    def renew(self, credentials):
        self.id = self._create(credentials)

    def save(self):
        with open('/tmp/asdf.pickle', 'wb') as fp:
            pickle.dump(self, fp)

class AtomicInteger():
    def __init__(self, value=0):
        self._value = value
        self._lock = threading.Lock()

    def inc(self):
        with self._lock:
            self._value += 1
            return self._value

    def dec(self):
        with self._lock:
            self._value -= 1
            return self._value

    @property
    def value(self):
        with self._lock:
            return self._value

class RequestLimitException(Exception):
    pass

class SessionHandler(object):
    _CONCURRENT_SESSION = 50
    _SESSIONS_PER_DAY = 500
    _REQUESTS_DAY_LIMIT = 7500-48

    def __init__(self, credentials):
        self.sessions = []
        self.credentials = credentials
        self.total_requests = AtomicInteger(0)

    def create(self):
        # TODO(godbit): Fix this.
        session = Session(self.credentials, self)

        self.sessions.append(session)
        return session

    def allow_request(self):
        if self.total_requests.inc() >= self._REQUESTS_DAY_LIMIT:
            self.total_requests.dec()
            raise 
            return False
            # Say no.

        return True

from enum import Enum
class GameMode(Enum):
    siege     = 424
    onslaught = 452
    tdm       = 469 # Team deatchmatch
    ranked    = 428

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
