import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from epl_agent import models

UTC = timezone.utc


def fixture(i, day, home, away, hg=None, ag=None, season="2023-24"):
    return {"id":str(i),"season":season,"date":day,"kickoff_utc":day+"T15:00:00Z",
            "home":home,"away":away,"hg":hg,"ag":ag}


class ForecastTests(unittest.TestCase):
    def test_score_observation_is_conservative(self):
        match = fixture(1,"2023-09-02","A","B",2,1)
        match["kickoff_utc"] = None
        self.assertEqual(models._available(match),datetime(2023,9,2,23,tzinfo=UTC))
        match["result_observed_at"] = "2024-01-01T12:00:00Z"
        self.assertEqual(models._available(match),datetime(2024,1,1,12,tzinfo=UTC))

    def test_replay_drops_future_scores_and_handles_same_round_and_promotion(self):
        first = fixture(1,"2023-09-01","A","B",2,0)
        second = fixture(2,"2023-09-02","C","A",0,3)
        following = fixture(3,"2023-09-08","A","C",2,2)
        promoted = fixture(4,"2025-09-10","A","D",season="2025-26")
        rows = [first,second,following,promoted]
        _, features = models._timeline(rows)
        self.assertEqual(features["2"][10],0)  # First match was not over at this cutoff.
        self.assertEqual(features["3"][9],2)
        self.assertEqual(features["4"][9:11],[0,0])
        first["result_observed_at"] = "2023-10-01T00:00:00Z"
        _, corrected = models._timeline(rows)
        self.assertEqual(corrected["3"][9],1)

    def test_forecast_ignores_target_result_and_future_model(self):
        rows = [fixture(1,"2023-08-10","A","B",0,1),fixture(2,"2023-08-14","B","C",0,3),
                fixture(3,"2024-08-18","C","A",2,2,season="2024-25")]
        with tempfile.TemporaryDirectory() as folder:
            manifest = models.train(rows,folder)
            before = models.forecast(rows[0],rows,model_dir=folder)
            rows[0]["ag"] = 100
            rows[-1]["hg"] = 100
            self.assertEqual(before,models.forecast(rows[0],rows,model_dir=folder))
            self.assertEqual(before["samples"], {"home":0,"away":0})
            self.assertAlmostEqual(sum(before["probs"]),1)
            self.assertEqual(len(before["top_scores"]),3)
            with self.assertRaises(ValueError):
                models.forecast(rows[0],rows,model_dir=folder,version=manifest["seasons"][-1]["version"])
            with self.assertRaises(ValueError):
                models.forecast(rows[0],rows,cutoff=rows[0]["kickoff_utc"],model_dir=folder)

    def test_shipped_artifact_reproduces_walk_forward_prediction(self):
        from epl_agent.data import load_matches
        rows = load_matches()
        replays = models.load_replays()
        first = next(m for m in rows if m["id"] in replays and m["season"]=="2025-26")
        p = models.forecast(first,rows)
        self.assertTrue(np.allclose(p["probs"],replays[first["id"]]["probs"],rtol=1e-12))
        self.assertEqual(p["model_version"],replays[first["id"]]["model_version"])
        self.assertTrue(models.replay_matches(replays[first["id"]],first))
        self.assertFalse(models.replay_matches(replays[first["id"]],dict(first,date="2025-08-05",hg=101)))
        self.assertEqual(len(replays),models.load_report()["n"])
        self.assertGreater(len(replays),9000)
        self.assertTrue(p["historical_reconstruction"])

    def test_weight_and_candidate_preregistration(self):
        a, m = [.6,.2,.2], [.4,.3,.3]
        self.assertTrue(np.allclose(models.blend(a,m),[.44,.28,.28]))
        with self.assertRaises(ValueError):
            models.blend([.9,.2,.2],m)
        with self.assertRaises(ValueError):
            models.fit_challenger([])
        sample = [{"id":str(i),"timing_verified":True,"cutoff":"2024-10-20T10:00:00Z",
                   "actual":"HDA"[i%3],"probs":a,"market":m} for i in range(380)]
        fit = models.fit_challenger(sample)
        self.assertEqual(len(fit["development_ids"]),380)
        self.assertAlmostEqual(sum(models.challenger_forecast(fit,a,m)),1.)
        with self.assertRaises(ValueError):
            models.fit_challenger(sample+[sample[0]])

    def test_score_versions_at_feature_training_and_report_cutoffs(self):
        first = fixture(1,"2023-09-01","A","B")
        following = fixture(2,"2023-09-10","A","C",0,3)
        later = fixture(3,"2023-09-14","A","D")
        original = dict(first,hg=1,ag=1,result_observed_at="2023-09-02T10:00:00Z",observed_at="2023-09-02T10:00:00Z")
        corrected = dict(original,hg=2,result_observed_at="2023-09-12T10:00:00Z",observed_at="2023-09-12T10:00:00Z")
        archive = [first,following,later,fixture(4,"2021-08-01","X","Y",0,0,"2021-22"),
                   fixture(5,"2022-08-01","X","Y",0,0,"2022-23")]
        schedule = dict(first,observed_at="2023-08-05T10:00:00Z")
        with tempfile.TemporaryDirectory() as folder:
            models.train(archive,folder)
            def replay(versions, at):
                return models.backtest(archive,folder,versions=versions,archive=archive,as_of=at)
            old = replay([schedule,original,corrected],"2023-09-11T18:00:00Z")
            new = replay([schedule,original,corrected],"2023-09-15T18:00:00Z")
            self.assertEqual([r['actual'] for r in old['rows'] if r['id']=='1'],['D'])
            self.assertEqual([r['actual'] for r in new['rows'] if r['id']=='1'],['H'])
            self.assertEqual({r['id']:r['probs'] for r in old['rows']},{r['id']:r['probs'] for r in new['rows']})
            for target, count, goals in ((following,1,1),(later,2,2)):
                p = models.forecast(target,archive,model_dir=folder,archive=archive,versions=[corrected,original,schedule])
                self.assertEqual(p['samples']['home'],count)
                self.assertAlmostEqual(p['form']['home']['gf'],(5.4+goals)/(4+count))
            # A late correction to a previous season cannot affect a model fitted on July 1.
            summer = dict(corrected,observed_at="2024-07-03T10:00:00Z",result_observed_at="2024-07-03T10:00:00Z")
            training = archive+[fixture(6,"2024-08-12","X","Y",1,0,"2024-25")]
            a = models.train(training,folder,archive=training,versions=[schedule,original])
            b = models.train(training,folder,archive=training,versions=[schedule,original,summer])
            self.assertEqual(a['seasons'],b['seasons'])

    def test_withdrawals_reinstatements_and_late_schedule_do_not_enter_prior_features(self):
        initial = fixture(1,"2023-09-01","A","B",1,0)
        targets = [fixture(i,"2023-09-"+day,"A","C") for i,day in ((2,"10"),(3,"14"))]
        versions = [dict(initial,withdrawn=True,observed_at="2023-09-07T11:00:00Z"),
                    dict(initial,observed_at="2023-09-11T11:00:00Z"),
                    dict(targets[0],date="2023-09-11",kickoff_utc="2023-09-11T15:00:00Z",
                         observed_at="2023-09-11T10:00:00Z")]
        source = models._Versions([],versions,[initial,*targets])
        future = versions[-1]
        features = source.features([(models._kickoff(m)-timedelta(days=1),m) for m in targets])
        self.assertEqual(features['2'][9],0)
        self.assertEqual(features['3'][9],1)
        self.assertEqual(source.features([(models._kickoff(future)-timedelta(days=1),future)]),{})

    def test_reference_dixon_coles_grid_and_diagnostics_common_sample(self):
        # penaltyblog 1.13.2 Cython compute_dixon_coles_probabilities, 21 x 21, normalized.
        dc = {"clubs":{},"home":float(np.log(1.7/1.1)),"intercept":float(np.log(1.1)),"rho":-.08}
        match=fixture(1,"2023-09-01","H","A",1,1)
        p, grid, _ = models._matrix(dc,match,[0]*12)
        np.testing.assert_allclose(grid[:2,:2].ravel(),
            [.06990724799395058,.057793883519007175,.09427992109413798,.12281200247789027],rtol=0,atol=1e-13)
        np.testing.assert_allclose(p,[.5049282158006496,.2583195006807707,.23675228351857988],rtol=0,atol=1e-13)
        self.assertAlmostEqual(grid.sum(),1)
        base = {"cutoff":"t","model_version":"v",**{field:p.tolist() for field in models._COMPONENTS.values()}}
        summary, rows = models._diagnostics([(match,base),(dict(match,id="2"),dict(base,tree_probs=None))])
        self.assertEqual(len(rows),2)
        self.assertEqual(summary["overall"]["tree"]["missing"],1)
        for name in models._COMPONENTS:
            self.assertEqual(summary["overall"][name]["n"],1)
            self.assertAlmostEqual(summary["overall"][name]["log_loss"],-np.log(p[1]))
            for outcome in "HDA":
                bins=summary["calibration"][name][outcome]
                self.assertEqual(sum(b["n"] for b in bins),1)
                populated=next(b for b in bins if b["n"])
                self.assertEqual(populated["observed"],int(outcome=="D"))

    def test_candidate_validates_market_receipt_and_models(self):
        from epl_agent.challenger import _verified, train_candidate
        from epl_agent.market import BOOKS, digest
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, "380"):
            train_candidate(folder)
        m = fixture(1, "2023-09-10", "Arsenal", "Fulham", 2, 1)
        m["schedule_revision"] = 1
        t = "2023-09-09T14:55:00Z"
        reg = {"model":"v1"}
        event = {"sport_key":"soccer_epl","commence_time":m["kickoff_utc"],"home_team":"Arsenal","away_team":"Fulham",
                 "bookmakers":[{"key":key, "last_update":t,"markets":[{"key":"h2h","outcomes":[
                       {"name":name,"price":3.0} for name in ("Arsenal","Draw","Fulham")]}]} for key in BOOKS[:3]]}
        market, a = [1/3]*3, [.5,.25,.25]
        p = {"id":m["id"],"season":m["season"],"kickoff":m["kickoff_utc"],"schedule_revision":1,
             "cutoff":"2023-09-09T15:00:00Z","observed":t,"frozen":t,"schedule_observed_at":t,
             "model_version":"v1","protocol_hash":digest(reg),"prediction":{"model_version":"v1","probs":a},
             "a":a,"market":market,"b":models.blend(a,market)}
        ref = "https://github.com/test/test/issues/1#issuecomment-1"
        row = {"payload":json.dumps(p),"raw":json.dumps(event),"received":t,"fixture":m["id"],"kickoff":m["kickoff_utc"],
               "revision":1,"evidence":ref,"hash":digest(p),"witnessed":t,"reason":"paired"}
        witness = {"at":"2023-08-01T00:00:00Z","url":ref}
        self.assertIsNotNone(_verified(row,m,reg,witness))
        self.assertIsNone(_verified(dict(row,witnessed="2023-09-09T15:01:00Z"),m,reg,witness))
        self.assertIsNone(_verified(dict(row,hash="wrong"),m,reg,witness))
        self.assertIsNone(_verified(dict(row,raw=json.dumps(dict(event,bookmakers=[]))),m,reg,witness))
