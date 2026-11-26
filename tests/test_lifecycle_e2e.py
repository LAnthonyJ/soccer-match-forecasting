"""Exercise both prospective cohorts through a real frozen model and an isolated issue."""
import json
import hashlib
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from epl_agent import data, evidence, models, service, snapshot, source_sync
from epl_agent.__main__ import export_site
from epl_agent.market import BOOKS, collect, database, freeze_protocol, instant


class LifecycleTest(unittest.TestCase):
    def test_two_freezes_late_score_correction_and_clean_pages(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {
            "EPL_AGENT_HOME": folder, "EPL_EVIDENCE_REPO":"example/epl",
            "EPL_EVIDENCE_ISSUE":"42", "EPL_GITHUB_TOKEN":"test", "ODDS_API_KEY":"",
        }):
            self.run_lifecycle(Path(folder))

    def run_lifecycle(self, root):
        season = "2024-25"
        fixtures_path = root/"fixtures.sqlite3"
        schedule = [{k:m[k] for k in data.FIELDS} for m in data.load_matches() if m["season"]==season]
        for m in schedule:
            m.update(hg=None, ag=None)
        ordered = sorted((m for m in schedule if m["kickoff_utc"]),key=lambda m:m["kickoff_utc"])
        first = ordered[0]
        second = next(m for m in ordered if instant(m["kickoff_utc"])-instant(first["kickoff_utc"]) > timedelta(hours=3))
        start = instant(first["kickoff_utc"])-timedelta(days=5)
        now = start

        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):
                return now.astimezone(tz)

        # Both acquisition and audit use an isolated issue with GitHub-shaped receipts.
        comments = []
        issue = Mock()
        def get(url, **_):
            if url.endswith("/issues/42/comments"):
                payload = list(comments)
            elif url.endswith("/user"):
                payload = {"id":7}
            else:
                self.assertEqual(url,"https://api.github.com/repos/example/epl")
                payload = {"private":False}
            return Mock(status_code=200,json=lambda:payload)
        def post(url,json,**_):
            self.assertTrue(url.endswith("/example/epl/issues/42/comments"))
            comment = {"body":json["body"], "created_at":now.isoformat(), "updated_at":now.isoformat(),
                       "user":{"id":7}, "author_association":"OWNER",
                       "html_url":"https://github.com/example/epl/issues/42#issuecomment-"+str(len(comments)+1)}
            comments.append(comment)
            return Mock(status_code=201,json=lambda:comment)
        issue.get.side_effect = get
        issue.post.side_effect = post

        def sync_scores():
            corroboration = [dict(m,fixture_id=str(i)) for i,m in enumerate(schedule)]
            with (patch.object(source_sync,"datetime",Clock), patch.object(data,"_primary",return_value=(schedule,"a"*40)),
                  patch.object(source_sync,"_secondary",return_value=(corroboration,"b"*64))):
                result = source_sync.synchronize(fixtures_path,season)
            self.assertEqual(result["status"],"ok")

        sync_scores()
        weights = json.loads((models.MODEL_DIR/"manifest.json").read_text("utf8"))
        version = models._select_artifact(weights,start)
        with database(root) as db:
            freeze_protocol(db,version,start)
        evidence.publish(root,session=issue,clock=lambda:now)

        def freeze(game, with_market):
            nonlocal now
            now = instant(game["kickoff_utc"])-timedelta(days=1,seconds=80)
            matches = data.load_matches(fixtures_path)
            target = next(m for m in matches if m["id"]==game["id"])
            event = {"sport_key":"soccer_epl","id":"fixture-event","home_team":game["home"],
                     "away_team":game["away"],"commence_time":game["kickoff_utc"],
                     "bookmakers":[{"key":name,"last_update":now.isoformat(),"markets":[{"key":"h2h",
                         "outcomes":[{"name":team,"price":price} for team,price in
                                     [(game["home"],2.0),("Draw",3.0),(game["away"],4.0)]]}]} for name in BOOKS[:3]]}
            fetch = Mock(return_value=Mock(headers={},json=lambda:[event]))
            def predict(m,when,version):
                return models.forecast(m,matches,when,version=version,archive=data.load_matches(),
                                       versions=data.load_versions(fixtures_path))
            with patch.object(models,"datetime",Clock), patch.dict(os.environ,{"ODDS_API_KEY":"simulated" if with_market else ""}):
                self.assertEqual(collect([target],predict,root,clock=lambda:now,get=fetch)["captured"],1)
                self.assertEqual(collect([target],predict,root,clock=lambda:now,get=fetch)["captured"],0)
            self.assertEqual(fetch.call_count,int(with_market))
            evidence.publish(root,session=issue,clock=lambda:now)
        freeze(first,False)
        freeze(second,True)

        # An immutable prediction first settles on a draw, then on a corrected home win.
        now = instant(second["kickoff_utc"])+timedelta(hours=48)
        first.update(hg=1,ag=1)
        second.update(hg=1,ag=0)
        sync_scores()
        initial = root/"original"
        with patch.object(snapshot,"datetime",Clock):
            snapshot.export_bundle(initial,root=root,session=issue)
        original = json.loads((initial/snapshot.NAME).read_bytes())
        before = original["scores"]["pilot"]
        self.assertEqual((before["independent"]["n_scored"],before["n_pairs"]),(2,1))
        hashes = {r["id"]:r["hash"] for r in original["data"]["ledger"]["records"]}
        now += timedelta(days=1)
        first["hg"] = 2
        sync_scores()
        corrected = root/"corrected"
        with patch.object(snapshot,"datetime",Clock):
            snapshot.export_bundle(corrected,root=root,session=issue)
        local = service.prediction(first["id"])
        self.assertEqual(local["trace"]["hash"],hashes[first["id"]])
        self.assertEqual(local["market_reason"],"missing_api_key")
        self.assertEqual(local["trace"]["score"]["actual"],"H")
        home_probability = local["prediction"]["a"][0]
        self.assertAlmostEqual(local["trace"]["score"]["log_loss"],-math.log(home_probability))
        clean = root/"independent-reader"
        with patch.dict(os.environ,{"EPL_AGENT_HOME":str(clean)}), patch("requests.Session",return_value=issue):
            verified = snapshot.verify(initial)
            rows = verified["ledger"]["records"]
            self.assertEqual({r["id"]:r["hash"] for r in rows},hashes)
            original_game = next(m for m in verified["fixtures"] if m["id"]==first["id"])
            self.assertEqual((original_game["hg"],original_game["ag"]),(1,1))
            site = root/"site"
            for batch in (initial, corrected):
                export_site(site,snapshot_dir=batch)
                public_bytes = (batch/snapshot.NAME).read_bytes()
                digest = hashlib.sha256(public_bytes).hexdigest()
                marker = json.loads((site/"batch.json").read_bytes())["batch_id"]
                board = json.loads((site/"board.json").read_bytes())
                self.assertEqual((marker,board["batch_id"]),(digest,digest))
                self.assertEqual((site/"snapshots"/(digest+".json")).read_bytes(),public_bytes)
            original_bytes = (initial/snapshot.NAME).read_bytes()
            self.assertEqual((site/"snapshots"/(hashlib.sha256(original_bytes).hexdigest()+".json")).read_bytes(),original_bytes)
            dashboard = json.loads((root/"site/dashboard.json").read_bytes())
            published = dashboard["fixtures"]
            if not any(m["id"]==first["id"] for m in published):
                published = json.loads((root/"site/seasons"/(season+".json")).read_bytes())
            fixture = next(m for m in published if m["id"]==first["id"])
            self.assertEqual(fixture["trace"]["score"],local["trace"]["score"])
            self.assertEqual(fixture["trace"]["hash"],hashes[first["id"]])
            self.assertGreaterEqual(fixture["trace"]["score_processing_seconds"],0)
            self.assertLessEqual(instant(fixture["trace"]["result_at"]),instant(fixture["trace"]["score_committed_at"]))
            scorecard = dashboard["report"]["pilot"]
            self.assertEqual((scorecard["independent"]["n_scored"],scorecard["n_pairs"]),(2,1))
            self.assertEqual(len(comments),3)
            self.assertFalse(clean.exists())
