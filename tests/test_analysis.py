import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from epl_agent import analysis, service
from epl_agent.web import create_app
from scripts.analysis_release import checked_report


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env=patch.dict(os.environ,{"EPL_AGENT_HOME":tmp.name,"EPL_ANALYSIS_ORIGIN":""})
        env.start()
        self.addCleanup(env.stop)
        self.fid="2026-27_fulham_manchester-united"
        self.stamp="2026-11-25T12:00:00+00:00"
        self.game=service.prediction(self.fid)

    def test_prematch_hash_survives_later_results_and_clock_ticks(self):
        before=analysis.packet(self.game,self.stamp)
        later=copy.deepcopy(self.game)
        later.update(hg=17,ag=13,result_source="private-result",market_note="final score 17-13")
        later["trace"].update(score={"log_loss":0.2,"brier":0.1},result_at=self.stamp)
        later["prediction"]["cutoff"]=self.stamp
        after=analysis.packet(later,self.stamp)
        self.assertEqual(before["fact_hash"],after["fact_hash"])
        self.assertNotEqual(before["as_of"],after["as_of"])
        self.assertNotIn("private-result",json.dumps(after))
        self.assertTrue(all(f["stage"]=="prematch" for f in after["facts"]))
        later["trace"]["revision"]+=1
        self.assertNotEqual(before["fact_hash"],analysis.packet(later,self.stamp)["fact_hash"])

    def test_local_report_followup_and_stale_version(self):
        client=create_app().test_client()
        report=client.get("/api/matches/"+self.fid+"/analysis").json
        self.assertEqual(report["source"],"template")
        self.assertTrue(any("probabilities" in s["fact_ids"] for s in report["sections"]))
        fields={"fixture_id":self.fid,"fact_hash":report["fact_hash"],"question":"目前有哪些伤停？","history":[]}
        self.assertIn("缺少",client.post("/api/analysis/ask",json=fields).json["answer"])
        self.assertEqual(client.post("/api/analysis/ask",json={**fields,"fact_hash":"0"*64}).status_code,409)
        self.assertEqual(client.get("/api/matches/"+self.fid+"-x/analysis").status_code,404)
        self.assertEqual(client.post("/api/analysis/ask",json={**fields,"history":"bad"}).status_code,400)
        self.assertEqual(client.get("/analysis/facts/"+self.fid+".json").json["fact_hash"],report["fact_hash"])
        next_game={"id":"other","label":"阿森纳 对 切尔西"}
        with patch.object(service,"ask",return_value={"fixture_id":"other","answer":"阿森纳 对 切尔西","choices":[next_game]}):
            follow=client.post("/api/analysis/ask",json={**fields,"question":"阿森纳下一场踢谁"}).json
            self.assertEqual((follow["fixture_id"],follow["choices"]),(self.fid,[next_game]))

    def test_catalog_and_report_queue_require_confirmed_unplayed_fixtures(self):
        game={**self.game,"hg":None,"ag":None,"schedule_status":"verified"}
        rows=[game,{**game,"id":"preview","schedule_status":"unconfirmed"},
              {**game,"id":"conflict","schedule_status":"conflict"},{**game,"id":"final","hg":2,"ag":0}]
        with tempfile.TemporaryDirectory() as tmp:
            analysis.export_assets(tmp,{"updated_at":self.stamp,"fixtures":rows,"current_id":game["id"],
                                        "report":{"history":{},"status":"accumulating","n_pairs":0}})
            catalog=json.loads((Path(tmp)/"analysis/catalog.json").read_text("utf8"))
            queue=json.loads((Path(tmp)/"analysis/current.json").read_text("utf8"))
            self.assertEqual([g["id"] for g in catalog["fixtures"]],[game["id"]])
            self.assertEqual(queue["fixture_ids"],[game["id"]])

    def test_fixed_scenarios_keep_dates_probabilities_market_and_results_in_their_own_view(self):
        outcomes = (("主胜", [0.55,0.25,0.20]), ("平局", [0.21,0.51,0.28]), ("客胜", [0.22,0.26,0.52]))
        for favourite, odds in outcomes:
            for observed in (None, [0.38,0.30,0.32]):
                for source in ("preview","frozen","replay"):
                    for revised in (False,True):
                        with self.subTest(favourite=favourite, market=bool(observed), source=source, revised=revised):
                            game=copy.deepcopy(self.game)
                            game.update(hg=19,ag=13,kickoff_utc="2026-11-28T20:00:00+00:00",
                                        schedule_status="verified")
                            if revised:
                                game.update(hg=23,kickoff_utc="2026-11-29T16:30:00+00:00")
                                game["trace"]["revision"]+=1
                            game["trace"].pop("cutoff",None)
                            game.pop("schedule_window",None)
                            game["prediction"].update(a=odds,source=source,market=observed,b=None)
                            bundle=analysis.packet(game,self.stamp)
                            facts={f["id"]:f["text"] for f in bundle["facts"]}
                            self.assertIn(favourite+"概率最高",facts["favourite"])
                            for label,prob in zip(("主胜","平局","客胜"),odds):
                                self.assertIn(f"{label} {prob:.1%}",facts["probabilities"])
                            self.assertEqual("market_gap" in facts,observed is not None)
                            self.assertIn("同期去水" if observed else "缺少",facts["market"])
                            self.assertIn("2026-11-28" if revised else "2026-11-27",facts["protocol_cutoff"])
                            self.assertIn("缺少",facts["injuries"])
                            self.assertNotIn("result",facts)
                            result=next(f for f in analysis._review(game,self.stamp)["facts"] if f["id"]=="result")
                            self.assertIn(f'{game["hg"]} 比 13',result["text"])

    def test_mirror_publishes_only_text_supported_by_prematch_facts(self):
        bundle=analysis.packet(self.game,self.stamp)
        fact=next(f for f in bundle["facts"] if f["id"]=="probabilities")
        section={"fact_ids":["probabilities"],"title":"赛果判断","text":fact["text"]}
        report={"source":"deepseek","model":"deepseek-flash","fixture_id":self.fid,
                "fact_hash":bundle["fact_hash"],"generated_at":self.stamp,"sections":[section]}
        self.assertEqual(checked_report(report,bundle)["answer"],fact["text"])
        self.assertIsNone(checked_report({**report,"sections":[{**section,"text":"球员确定伤缺。"}]},bundle))
        self.assertIsNone(checked_report({**report,"view":"postmatch"},bundle))
        injuries=next(f for f in bundle["facts"] if f["id"]=="injuries")
        supplement={"title":"资料边界","text":injuries["text"],"fact_ids":["injuries"]}
        complete={**report,"sections":[section,supplement]}
        self.assertEqual(checked_report(complete,bundle)["sections"],complete["sections"])
        self.assertIsNone(checked_report({**complete,"sections":[section,{**supplement,"title":"市场对照"}]},bundle))
        self.assertIsNone(checked_report({**complete,"sections":[section,section]},bundle))
        self.assertIsNone(checked_report({**complete,"generated_at":None},bundle))

    def test_static_archive_packet_and_review_are_separate(self):
        from epl_agent.__main__ import export_site
        with tempfile.TemporaryDirectory() as tmp:
            site=Path(export_site(tmp))
            fid=self.fid
            pre=json.loads((site/"analysis"/"facts"/(fid+".json")).read_text("utf8"))
            season=fid.split("_")[0]
            review=json.loads((site/"analysis"/"reviews"/(season+".json")).read_text("utf8"))[fid]
            self.assertTrue(all(f["stage"]=="postmatch" for f in review["facts"]))
            self.assertNotIn("result",{f["id"] for f in pre["facts"]})
            archive=json.loads((site/"analysis"/"archive"/"2002-03.json").read_text("utf8"))
            self.assertEqual(len(archive),380)
            self.assertEqual(json.loads((site/"analysis"/"reports"/"index.json").read_text("utf8")),{})
            self.assertTrue((site/"analysis-evidence.html").is_file())
