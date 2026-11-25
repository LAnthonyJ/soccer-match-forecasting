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

    def test_mirror_publishes_only_text_supported_by_prematch_facts(self):
        bundle=analysis.packet(self.game,self.stamp)
        fact=next(f for f in bundle["facts"] if f["id"]=="probabilities")
        section={"fact_ids":["probabilities"],"title":"赛果判断","text":fact["text"]}
        report={"source":"deepseek","model":"deepseek-flash","fixture_id":self.fid,
                "fact_hash":bundle["fact_hash"],"generated_at":self.stamp,"sections":[section]}
        self.assertEqual(checked_report(report,bundle)["answer"],fact["text"])
        self.assertIsNone(checked_report({**report,"sections":[{**section,"text":"球员确定伤缺。"}]},bundle))
        self.assertIsNone(checked_report({**report,"view":"postmatch"},bundle))

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
