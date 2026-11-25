from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from epl_agent import data, service
from epl_agent.__main__ import export_site
from epl_agent.web import create_app


class PublicWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ,{"EPL_AGENT_HOME":temp.name,"ODDS_API_KEY":""})
        env.start()
        self.addCleanup(env.stop)
        self.client=create_app().test_client()

    def test_anonymous_real_replay_and_questions(self):
        fixture="2026-27_fulham_manchester-united"
        with patch("socket.create_connection",side_effect=AssertionError("Unexpected external request")):
            dashboard=self.client.get("/api/dashboard").get_json()
            p=self.client.get("/api/fixtures/"+fixture).get_json()["prediction"]
            self.assertEqual(p["source"],"replay")
            self.assertAlmostEqual(sum(p["a"]),1,places=6)
            self.assertEqual(len(p["top_scores"]),3)
            self.assertIsNone(p["market"])
            self.assertGreater(dashboard["report"]["history"]["n"],9000)
            self.assertEqual(dashboard["report"]["n_pairs"],0)
            for q,expected in (("为什么模型更看好客队",f"{p['a'][2]:.1%}"),
                               ("本场伤停","缺少"),("市场分歧","缺少"),("过去一季模型比市场好吗","无法判断")):
                result=self.client.post("/api/ask",json={"question":q,"fixture_id":fixture})
                self.assertEqual(result.status_code,200)
                self.assertIn(expected,result.get_json()["answer"])
            self.assertEqual(self.client.get("/api/fixtures/"+fixture+"2").status_code,404)
            self.assertEqual(self.client.get("/api/dashboard?season=bad").status_code,400)
            self.assertEqual(self.client.post("/api/ask",json={"question":{}}).status_code,400)
            self.assertEqual(self.client.get("/api/ledger").json["records"],[])

    def test_source_revision_invalidates_old_replay(self):
        fixture="2026-27_fulham_manchester-united"
        rows=data.load_matches()
        changed=next(m for m in rows if m["id"]==fixture)
        original=changed["ag"]
        changed["ag"]=original+1
        with patch.object(service,"fixtures",return_value=rows):
            self.assertIsNone(service.prediction(fixture)["prediction"])
            row=next(m for m in service.dashboard("2026-27")["fixtures"] if m["id"]==fixture)
            self.assertIsNone(row["prediction"])

    def test_static_export_excludes_secret_material(self):
        target=self.root/"pages"
        export_site(target)
        self.assertIn('content="static"',(target/"index.html").read_text("utf8"))
        site=json.loads((target/"dashboard.json").read_text("utf8"))
        self.assertTrue(any(m["prediction"] for m in site["fixtures"]))
        self.assertEqual(len(site["available_seasons"]),2)
        self.assertFalse(any(f.name.endswith((".sqlite3",".db")) for f in target.rglob("*")))
        self.assertTrue(list((target/"assets").glob("*.js")))
