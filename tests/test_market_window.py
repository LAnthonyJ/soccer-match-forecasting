from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from epl_agent.evaluation import public_ledger
from epl_agent.market import BOOKS, collect, database, freeze_protocol, market_consensus, read_value


class QuoteWindowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = folder.name
        self.cutoff = datetime(2027, 9, 1, 12, tzinfo=timezone.utc)
        self.now = self.cutoff - timedelta(minutes=14)
        self.match = {"id":"epl-1", "season":"2027-28", "home":"Arsenal", "away":"Brentford",
                      "hg":None, "ag":None, "kickoff_utc":(self.cutoff+timedelta(days=1)).isoformat(),
                      "schedule_revision":1, "schedule_observed_at":(self.now-timedelta(days=4)).isoformat()}
        self.predict = Mock(return_value={"model_version":"locked", "probs":[.5,.2,.3]})
        with database(self.root) as db:
            freeze_protocol(db, "locked", self.now-timedelta(days=35))
        self.fetch = Mock()
        self.fetch.return_value.headers = {}
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"ODDS_API_KEY":"mock"}).start()

    def quote(self, home_price):
        return {"id":"q", "sport_key":"soccer_epl", "home_team":"Arsenal", "away_team":"Brentford",
                "commence_time":self.match["kickoff_utc"], "bookmakers":[
                    {"key":key, "last_update":self.now.isoformat(), "markets":[{"key":"h2h","outcomes":[
                        {"name":"Draw","price":3},{"name":"Arsenal","price":home_price},
                        {"name":"Brentford","price":4}]}]} for key in BOOKS[:3]]}

    def tick(self, minutes_before):
        self.now = self.cutoff-timedelta(minutes=minutes_before)
        return collect([self.match],self.predict,self.root,clock=lambda:self.now,get=self.fetch)

    def test_recovers_from_early_timeout_and_uses_latest_good_observation(self):
        self.fetch.side_effect = requests.Timeout
        self.assertEqual(self.tick(14)["captured"], 0)
        self.predict.assert_not_called()
        self.fetch.side_effect = None
        self.fetch.return_value.json.return_value = [self.quote(2)]
        self.assertEqual(self.tick(11)["captured"],0)
        latest = self.quote(2.5)
        self.fetch.return_value.json.return_value = [latest]
        self.assertEqual(self.tick(5)["captured"],0)
        latest_time = self.now
        self.fetch.return_value.json.return_value = []
        self.assertEqual(self.tick(1)["status"],"paired")
        row = public_ledger(self.root)["records"][0]
        self.assertEqual(row["payload"]["market"],market_consensus(latest,latest_time))
        self.assertEqual(row["payload"]["observed"],latest_time.isoformat())
        self.predict.assert_called_once_with(self.match,latest_time,"locked")
        with database(self.root) as db:
            raw = db.execute("SELECT raw FROM captures WHERE fixture=?", (self.match["id"],)).fetchone()[0]
        self.assertEqual(json.loads(raw),latest)
        self.assertNotIn("raw",row)
        self.assertEqual(self.tick(.5)["captured"],0)
        self.assertEqual(public_ledger(self.root)["records"][0]["hash"],row["hash"])

    def test_revision_cannot_reuse_early_quote_and_missed_window_is_recorded(self):
        self.fetch.return_value.json.return_value = [self.quote(2)]
        self.tick(14)
        self.match["schedule_revision"] = 2
        self.fetch.return_value.json.return_value = []
        self.assertEqual(self.tick(1)["status"],"event_missing")
        rows = public_ledger(self.root)["records"]
        self.assertIsNone(next(r for r in rows if r["revision"]==2)["payload"]["b"])
        self.match["schedule_revision"] = 3
        self.tick(-10)
        rows = public_ledger(self.root)["records"]
        missed = next(r for r in rows if r["revision"]==3)
        self.assertEqual(missed["reason"],"missed_window")
        self.assertIsNone(missed["payload"])
        self.assertEqual(self.predict.call_count,1)

    def test_malformed_events_and_books_do_not_suppress_valid_events(self):
        event = self.quote(2)
        malformed = self.quote(3)
        malformed["home_team"] = None
        event["bookmakers"].extend([None, {"key":BOOKS[4],"markets":5},
                                    {"key":BOOKS[5],"markets":[None,{"key":"h2h","outcomes":"?"}]}])
        self.fetch.return_value.json.return_value = [None, malformed, event]
        self.assertEqual(self.tick(1)["status"],"paired")

    def test_concurrent_workers_reserve_once_and_freeze_one_snapshot(self):
        self.now = self.cutoff-timedelta(minutes=1)
        self.fetch.return_value.json.return_value = [self.quote(2)]
        with ThreadPoolExecutor(8) as workers:
            results = list(workers.map(lambda _: collect([self.match],self.predict,self.root,
                                              clock=lambda:self.now,get=self.fetch), range(20)))
        self.assertEqual(sum(r["captured"] for r in results),1)
        self.fetch.assert_called_once()
        self.assertEqual(len(public_ledger(self.root)["records"]),1)
        self.predict.assert_called_once()
        with database(self.root) as db:
            self.assertEqual(read_value(db,"quota:2027-09"),499)
