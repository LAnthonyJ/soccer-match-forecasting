import copy
from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from epl_agent.evaluation import formal_report, interval, metrics, public_ledger
from epl_agent.evidence import publish
from epl_agent.market import BOOKS, collect, database, freeze_protocol, market_consensus, write_value


class MarketTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.now = datetime(2027,8,10,12,tzinfo=timezone.utc)
        self.clock = lambda:self.now
        self.kick = self.now+timedelta(days=1,minutes=1)
        self.match = dict(id="g",season="2027-28",home="Arsenal",away="Chelsea",hg=None,ag=None,
                          kickoff_utc=self.kick.isoformat(),schedule_observed_at=(self.now-timedelta(days=2)).isoformat(),
                          schedule_revision=1)
        self.event = {"id":"vendor-1","sport_key":"soccer_epl","home_team":"Arsenal","away_team":"Chelsea",
                      "commence_time":self.kick.isoformat(),"bookmakers":[
                        {"key":b,"last_update":self.now.isoformat(),"markets":[{"key":"h2h","outcomes":[
                            {"name":"Chelsea","price":4},{"name":"Arsenal","price":2},{"name":"Draw","price":3}]}]}
                        for b in BOOKS[:3]]}
        self.fetch = Mock()
        self.fetch.return_value.headers = {"x-requests-remaining":"499"}
        self.fetch.return_value.json.return_value = [self.event]
        self.forecast = Mock(return_value={"model_version":"a-v1","probs":[.6,.25,.15]})
        with database(self.root) as db:
            freeze_protocol(db,"a-v1",datetime(2026,11,25,tzinfo=timezone.utc))
        self.addCleanup(patch.stopall)
        patch.dict(os.environ,{"ODDS_API_KEY":"test"}).start()

    def capture(self):
        return collect([self.match],self.forecast,self.root,clock=self.clock,get=self.fetch)

    def test_consensus_and_single_capture(self):
        self.assertEqual(self.capture()["status"],"paired")
        self.assertEqual(self.capture()["captured"],0)
        self.fetch.assert_called_once()
        ledger = public_ledger(self.root)
        row = ledger["records"][0]
        self.assertNotIn("raw",row)
        self.assertNotIn("bookmakers",str(ledger))
        p = row["payload"]
        self.assertAlmostEqual(p["market"][0],6/13)
        self.assertAlmostEqual(p["b"][0],.8*6/13+.2*.6)
        self.assertAlmostEqual(sum(p["b"]),1)
        self.match.update(hg=1,ag=0)
        not_verified = formal_report([self.match],ledger)
        self.assertEqual(not_verified["n_pairs"],0)
        ledger["registration_evidence"]={"at":(self.now-timedelta(days=30)).isoformat(),
                                         "url":"https://github.com/example/project/issues/1#issuecomment-1"}
        row.update(witnessed=self.now.isoformat(),evidence="https://github.com/example/project/issues/1#issuecomment-2")
        formal = formal_report([self.match],ledger,now=self.kick+timedelta(days=1))
        self.assertEqual(formal["n_pairs"],1)
        self.assertEqual(formal["status"],"accumulating")
        self.match["schedule_revision"] += 1
        self.assertEqual(formal_report([self.match],ledger)["n_pairs"],0)
        self.match["schedule_revision"] -= 1
        row["payload"]["a"] = [.8,.1,.1]
        self.assertEqual(formal_report([self.match],ledger)["n_pairs"],0)

    def test_quota_guard_and_independent_result(self):
        with database(self.root) as db:
            write_value(db,"quota:2027-08",50)
        self.assertEqual(self.capture()["status"],"quota_reserve")
        self.fetch.assert_not_called()
        p = public_ledger(self.root)["records"][0]["payload"]
        self.assertIsNone(p["b"])
        self.assertEqual(p["a"],[.6,.25,.15])

    def test_incomplete_stale_future_and_wrong_event(self):
        event = copy.deepcopy(self.event)
        event["bookmakers"][0]["markets"][0]["outcomes"].pop()
        self.assertIsNone(market_consensus(event,self.now))
        for shift in (timedelta(hours=-25),timedelta(seconds=1)):
            event = copy.deepcopy(self.event)
            event["bookmakers"][0]["last_update"]=(self.now+shift).isoformat()
            self.assertIsNone(market_consensus(event,self.now))
        self.event["commence_time"]=(self.kick+timedelta(minutes=1)).isoformat()
        self.assertEqual(self.capture()["status"],"event_missing")

    def test_slow_response_and_unverified_schedule(self):
        moments = iter([self.now,self.now+timedelta(minutes=10)])
        self.clock=lambda:next(moments)
        self.assertEqual(self.capture()["status"],"late_receipt")
        self.assertIsNone(public_ledger(self.root)["records"][0]["payload"])
        self.clock=lambda:self.now+timedelta(seconds=30)
        self.match["schedule_observed_at"]=None
        self.assertEqual(self.capture()["status"],"schedule_unverified")

    def test_public_witness_publishes_no_raw_prices(self):
        self.capture()
        session=Mock()
        session.get.return_value.json.return_value={"private":False}
        def post(url,headers,json,timeout):
            self.assertNotIn("bookmakers",json["body"])
            response=Mock()
            response.json.return_value={**json,"created_at":self.now.isoformat(),
                                        "updated_at":self.now.isoformat(),"html_url":"https://github.com/x/y/issues/1#issuecomment-1"}
            return response
        session.post.side_effect=post
        with patch.dict(os.environ,{"EPL_EVIDENCE_REPO":"x/y","EPL_GITHUB_TOKEN":"test","EPL_EVIDENCE_ISSUE":"1"}):
            self.assertEqual(publish(self.root,session=session)["published"],1)
            self.assertEqual(publish(self.root,session=session)["published"],0)

    def test_paired_scoring_and_bootstrap(self):
        rows=[{"b":[.6,.2,.2],"market":[.4,.3,.3],"kickoff":(self.kick+timedelta(weeks=i)).isoformat(),"y":0}
              for i in range(6)]
        self.assertLess(metrics(rows,"b")["log_loss"],metrics(rows,"market")["log_loss"])
        delta,ci=interval(rows)
        self.assertGreater(ci[0],0)
        self.assertAlmostEqual(ci[0],delta)
        self.assertEqual(metrics([],"b")["log_loss"],None)
