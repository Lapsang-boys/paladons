import json
import logging

import psycopg2

from paladins import PaladinsAPI, Credentials, GameMode, MatchDetails
from paladins import RequestLimitException, SessionHandler
from match_ids import MATCH_IDS

CREDENTIALS = None
with open('dev-key.json', 'r') as fp:
    json_credentials = json.load(fp)
    CREDENTIALS = Credentials(json_credentials)

class Spider(object):
    def __init__(self):
        self.conn = psycopg2.connect(
            "dbname=docker user=docker password=docker host=192.168.99.101")

    def destroy(self):
        self.conn.close()

    def insert_matches(self, matches):
        cur = self.conn.cursor()
        for match_obj in matches:
            md = MatchDetails(match_obj)
            print(md.as_tuple())

            insert_query = "INSERT INTO match_details (account_level,assists,champion,damage_dealt,damage_taken,deaths,credits,match_date,self_healing,healing,shielding,loadout_card1,loadout_card2,loadout_card3,loadout_card4,loadout_card5,loadout_card1_level,loadout_card2_level,loadout_card3_level,loadout_card4_level,loadout_card5_level,item1,item2,item3,item4,item1_level,item2_level,item3_level,item4_level,talent,streak,kills,map,match_id,match_duration,highest_multi_kill,objective_time,party_id,platform,region,team1_score,team2_score,team,win_status,player_id,player_name,master_level) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (match_id, player_name) do nothing;"

            values = md.as_tuple()
            cur.execute(insert_query, values)

        self.conn.commit()
        cur.close()

def main():
    spider = Spider()
    session_handler = SessionHandler(CREDENTIALS)

    session = session_handler.create()
    api = PaladinsAPI(CREDENTIALS, session)

    # match_ids = api.get_match_ids_by_queue(GameMode.siege, date, hour)
    # matches = api.get_match_batch(match_ids)

    player_name = "döskalle"
    try:
        player = api.get_player(player_name)
        history = api.get_match_history(player)
    except RequestLimitException as re:
        logging.info("Reached request limit for today, good job!")
        return

    # date = "20181128"
    # hour = "-1"

    # spider.insert_matches(matches)

if __name__ == "__main__":
    main()
