import json
import logging
import os
import datetime
import queue
import time
import threading
import os.path
import pickle
from collections import deque

import psycopg2

from paladins import PaladinsAPI, Credentials, GameMode, MatchDetails
from paladins import RequestLimitException, SessionHandler

# Every ten minutes we will save overwatcher to disk.
PERSIST_INTERVAL = 600 * 1

# Every day we remove all old intervals from overwatcher fetched.
REMOVE_INTERVALS_INTERVAL = 24*3600*1

# Every minute we generate all possible intervals for overwatcher.
GENERATE_INTERVALS_INTERVAL = 60*1

def path(filename):
    """Return an absolute path to a file in the current directory."""
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), filename)

import logging.config
logging.config.fileConfig(path("logging_config.ini"))

CREDENTIALS = None
with open('dev-key.json', 'r') as fp:
    json_credentials = json.load(fp)
    CREDENTIALS = Credentials(json_credentials)

class Fetcher(object):
    def __init__(self, session):
        postgres_username = os.getenv("POSTGRES_USERNAME")
        postgres_password = os.getenv("POSTGRES_PASSWORD")
        postgres_database = os.getenv("POSTGRES_DATABASE")
        postgres_hostname = os.getenv("POSTGRES_HOSTNAME")

        self.conn = psycopg2.connect(
            f"dbname={postgres_database} user={postgres_username} password={postgres_password} host={postgres_hostname}")

        self.api = PaladinsAPI(CREDENTIALS, session)

    def destroy(self):
        self.conn.close()

    def insert_matches(self, matches):
        cur = self.conn.cursor()
        for match_obj in matches:
            md = MatchDetails(match_obj)

            insert_query = "INSERT INTO match_details (account_level,assists,champion,damage_dealt,damage_taken,deaths,credits,match_date,self_healing,healing,shielding,loadout_card1,loadout_card2,loadout_card3,loadout_card4,loadout_card5,loadout_card1_level,loadout_card2_level,loadout_card3_level,loadout_card4_level,loadout_card5_level,item1,item2,item3,item4,item1_level,item2_level,item3_level,item4_level,talent,streak,kills,map,match_id,match_duration,highest_multi_kill,objective_time,party_id,platform,region,team1_score,team2_score,team,win_status,player_id,player_name,master_level) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (match_id, player_name) do nothing;"

            values = md.as_tuple()
            try:
                cur.execute(insert_query, values)
            except psycopg2.IntegrityError as ie:
                logging.error(f"Unexpected error: {ie}")
                continue

        self.conn.commit()
        cur.close()


    def track_exists(self, track_id):
        cur = self.conn.cursor()
        cur.execute("SELECT fma_track_id FROM tracks WHERE fma_track_id = %s", (track_id,))

    def is_already_fetched(self, match_id):
        cur = self.conn.cursor()
        cur.execute("SELECT exists (select 1 FROM match_details WHERE match_id = %s)", (match_id,))
        self.conn.commit()
        cur.close()
        return cur.fetchone()[0]


class CheckableQueue(queue.PriorityQueue):
    def __contains__(self, item):
        with self.mutex:
            return item in self.queue

class Interval(object):
    def __init__(self, date, hour):
        self.date = date
        self.hour = hour
        self.fail_count = 0

    def key(self):
        return f"{self.date}{self.hour}"

    def __str__(self):
        return f"{self.date}{self.hour}, fails: {self.fail_count}"

    def __lt__(self, other):
        return self.date < other.date


class Overwatch(object):
    # TODO(_): Change to real path.
    folder = "/tmp"
    if os.getenv("IS_DOCKER"):
        folder = "/persist"

    _COMPLETED_MATCH_FRESH_FILE     = f"{folder}/completed-match-fresh.pickle"
    _COMPLETED_INTERVALS_FRESH_FILE = f"{folder}/completed-intervals-fresh.pickle"
    _COMPLETED_MATCH_FINAL_FILE     = f"{folder}/completed-match-final.pickle"
    _COMPLETED_INTERVALS_FINAL_FILE = f"{folder}/completed-intervals-final.pickle"

    _MAX_FAILS = 5
    def __init__(self):
        self.fetched = {}
        self.working = {}
        self.intervals = CheckableQueue()
        self.session_handler = SessionHandler(CREDENTIALS)
        self.match_ids = deque()

    def interval_generator(self):
        day = datetime.datetime.now() - datetime.timedelta(days=31)
        for i in range(31):
            date_str = day.strftime("%Y%m%d")
            for hour in range(24):
                for minute_range in range(6):
                    hour_str = "%02d,%02d" % (hour, minute_range*10)
                    yield Interval(date_str, hour_str)

            day += datetime.timedelta(days=1)

    def today_interval_generator(self):
        now = datetime.datetime.now()
        date_str = now.strftime("%Y%m%d")

        # All full hours today.
        for hour in range(now.hour-1):
            for minute_range in range(6):
                hour_str = "%02d,%02d" % (hour, minute_range*10)
                yield Interval(date_str, hour_str)

        # The current hour today.
        for minute_range in range(6):
            # Is this ten minute range full?
            if now.minute < (1+minute_range)*10:
                break

            hour_str = "%02d,%02d" % (hour, minute_range*10)
            yield Interval(date_str, hour_str)

    def load(self):
        def _load(final_path, fresh_path):
            final, fresh = None, None
            try:
                with open(final_path, 'rb') as fp:
                    final = pickle.load(fp)
            except Exception as e:
                logging.error(e)
            try:
                with open(fresh_path, 'rb') as fp:
                    fresh = pickle.load(fp)
            except Exception as e:
                logging.error(e)

            if final == None and fresh == None:
                logging.warning(f"Unable to load overwatcher from both {final_path} or {fresh_path}.")
                return

            if final == None:
                logging.warning(f"Unable to load main overwatcher persistent backup, using {fresh_path} file.")
                return fresh

            logging.info(f"Using {final_path} previous main overwatcher backup.")
            return final

        fetched = _load(self._COMPLETED_INTERVALS_FINAL_FILE, self._COMPLETED_INTERVALS_FRESH_FILE)
        if fetched:
            self.fetched = fetched
        match_ids = _load(self._COMPLETED_MATCH_FINAL_FILE, self._COMPLETED_MATCH_FRESH_FILE)
        if match_ids:
            self.match_ids = match_ids

        self.remove_old_intervals()

    def save(self):
        # We can only recover intervals, but not matches. Therefore, it is of
        # most importance to ensure that matches are correctly persisted. Since
        # in the worst case we can reproduce them from the intervals that have
        # yet to be fetched.
        try:
            with open(self._COMPLETED_MATCH_FRESH_FILE, 'wb') as fp:
                pickle.dump(self.match_ids, fp)
            with open(self._COMPLETED_INTERVALS_FRESH_FILE, 'wb') as fp:
                pickle.dump(self.fetched, fp)
            time.sleep(1)
            with open(self._COMPLETED_MATCH_FINAL_FILE, 'wb') as fp:
                pickle.dump(self.match_ids, fp)
            with open(self._COMPLETED_INTERVALS_FINAL_FILE, 'wb') as fp:
                pickle.dump(self.fetched, fp)
        except Exception as e:
            logging.error(e)

    def generate_intervals(self):
        def is_new(key):
            # Skip already fetched intervals.
            if key in self.fetched:
                return False
            if key in self.working:
                return False
            # We know this is a race condition.
            if key in self.intervals:
                return False
            return True

        # Generate all previous intervals (1 month back).
        prio = 1
        for interval in self.interval_generator():
            key = interval.key()
            if not is_new(key):
                return
            self.intervals.put((prio, interval))
            prio += 1

        # Generate todays intervals, at most 10 minutes behind.
        for interval in self.today_interval_generator():
            key = interval.key()
            if not is_new(key):
                return
            self.intervals.put((prio, interval))
            prio += 1

    def get_interval(self):
        while True:
            prio, interval = self.intervals.get()
            if interval.fail_count >= self._MAX_FAILS:
                logging.error("Abandoning this shit: {interval.key()}")
                continue
            logging.debug(f"PriorityQueue prio: {prio}")
            break

        self.working[interval.key()] = True
        return interval

    def put_back_interval(self, interval):
        interval.fail_count += 1
        self.intervals.put((0, interval))
        pass

    def register_finish(self, interval):
        del self.working[interval.key()]
        self.fetched[interval.key()] = True

    def remove_old_intervals(self):
        now = datetime.datetime.now()
        def is_old(interval_str):
            date = datetime.datetime.strptime(interval_str, '%Y%m%d%H,%S')
            difference = now - date
            return difference > datetime.timedelta(days=32)

        for k in self.fetched.keys():
            if not is_old(k):
                continue
            # Remove old intervals.
            del self.fetched[k]

    def info(self):
        logging.info(self.fetched)
        logging.info(self.match_ids)

    def create_session(self):
        return self.session_handler.create()

    def put_matches(self, match_ids):
        for id in match_ids:
            self.match_ids.append(id)

    def put_back_matches(self, matches):
        for match in matches:
            self.match_ids.append(match)

    def get_match(self):
        return self.match_ids.popleft()

def time_to_next_day():
    # Sleep until midnight.
    now = datetime.datetime.now()
    tomorrow = datetime.datetime(
        year=now.year,
        month=now.month,
        day=now.day+1,
        minute=1)

    til_next_day = tomorrow - now
    return til_next_day

def remove_old_intervals(overwatcher):
    logging.info("Starting persist_overwatcher")
    while True:
        time.sleep(REMOVE_INTERVALS_INTERVAL)
        logging.info("Removing old intervals from overwatcher")
        overwatcher.remove_old_intervals()

def persist_overwatcher(overwatcher):
    logging.info("Starting remove_old_intervals")
    while True:
        # time.sleep(PERSIST_INTERVAL)
        time.sleep(15)
        logging.info("Saving overwatcher")
        try:
            overwatcher.save()
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            continue
        logging.info("Saved overwatcher")

def generate_intervals(overwatcher):
    logging.info("Starting generate_intervals")
    while True:
        logging.info("Generating intervals for overwatcher")
        overwatcher.generate_intervals()
        logging.info("Finished generating intervals")
        time.sleep(GENERATE_INTERVALS_INTERVAL)

def fetch_intervals(fetcher, overwatcher):
    logging.info("Starting fetch_intervals")
    while True:
        try:
            interval = overwatcher.get_interval()
        except queue.Empty as e:
            logging.debug(e)
            time.sleep(60)
            continue

        logging.debug(f"Got interval: {interval}")

        try:
            match_ids = fetcher.api.get_match_ids_by_queue(
                GameMode.siege,
                interval.date,
                interval.hour)
        except RequestLimitException as re:
            # Return interval we couldn't fetch.
            overwatcher.put_back_interval(interval)

            logging.info("Reached request limit for today, good job!")
            til_next_day = time_to_next_day()

            # Sleep at most one hour.
            if til_next_day > 3600:
                time.sleep(3600)
            else:
                time.sleep(til_next_day.seconds)
            continue
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            continue

        logging.debug(match_ids)
        overwatcher.put_matches(match_ids)

        # Do requests.
        overwatcher.register_finish(interval)

# We know that a crash + save could lose information here.
def fetch_matches(fetcher, overwatcher):
    logging.info("Starting fetch_matches")
    matches = []
    while True:
        try:
            match = overwatcher.get_match()
        except IndexError as e:
            logging.debug(e)
            time.sleep(60)
            continue

        if fetcher.is_already_fetched(match):
            continue

        matches.append(match)

        logging.debug(f"Got match: {match}")
        if len(matches) < fetcher.api.MAX_MATCH_BATCH:
            continue

        logging.debug(f"Got match: {matches}")

        param = matches
        matches = []
        try:
            match_details = fetcher.api.get_match_batch(param)
        except RequestLimitException as re:
            # Return interval we couldn't fetch.
            overwatcher.put_back_matches(matches)

            logging.info("Reached request limit for today, good job!")
            til_next_day = time_to_next_day()

            # Sleep at most one hour.
            if til_next_day > 3600:
                time.sleep(3600)
            else:
                time.sleep(til_next_day.seconds)
            continue
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            continue

        try:
            fetcher.insert_matches(match_details)
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            continue

def main():
    overwatcher = Overwatch()
    logging.info("Reading old overwatcher")
    overwatcher.load()
    overwatcher.info()

    # matches = api.get_match_batch(match_ids)

    # player_name = "döskalle"
    #     player = fetcher.api.get_player(player_name)
    #     history = fetcher.api.get_match_history(player)
    # try:
    # except RequestLimitException as re:
    #     logging.info("Reached request limit for today, good job!")
    #     return

    # fetcher.insert_matches(matches)

    threading.Thread(
        name='persist_overwatcher',
        target=persist_overwatcher,
        daemon=True,
        args=(overwatcher,)).start()

    threading.Thread(
        name='remove_old_intervals',
        target=remove_old_intervals,
        daemon=True,
        args=(overwatcher,)).start()

    threading.Thread(
        name='generate_intervals',
        target=generate_intervals,
        daemon=True,
        args=(overwatcher,)).start()

    fetcher = None
    for i in range(1):
        session = overwatcher.create_session()
        fetcher = Fetcher(session)

        threading.Thread(
            name='fetch_intervals',
            target=fetch_intervals,
            args=(fetcher,overwatcher)).start()

        threading.Thread(
            name='fetch_matches',
            target=fetch_matches,
            args=(fetcher,overwatcher)).start()

    data_used = fetcher.api.get_data_used()
    logging.info(data_used)

if __name__ == "__main__":
    main()
