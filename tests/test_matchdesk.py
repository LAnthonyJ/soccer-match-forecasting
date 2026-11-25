from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from epl_agent import data, service


class MatchdeskTests(TestCase):
    def test_unknown_kickoff_and_frozen_model_without_prices(self):
        original = next(m for m in data.load_matches() if m["id"]=="2026-27_fulham_manchester-united")
        replay = service.history()[0][original["id"]]
        now = datetime.now(timezone.utc)
        no_time = {**original,"kickoff_utc":None}
        record = {"id":original["id"],"kickoff":original["kickoff_utc"],"revision":0,
                  "payload":{"prediction":replay,"b":None,"market":None},"reason":"missing_api_key"}
        results = service._present([no_time], [no_time], {"records":[record]}, now)
        self.assertIsNone(results[0]["prediction"])
        result = service._present([original], [original], {"records":[record]}, now)[0]
        self.assertEqual(result["prediction"]["source"],"frozen")
        self.assertIn("密钥",result["market_note"])
        self.assertIn("4 场",result["explanation"])
        self.assertIn("缺少合格",result["market_explanation"])

    def test_model_specific_market_explanations_and_score_questions(self):
        original = next(m for m in data.load_matches() if m["id"]=="2026-27_fulham_manchester-united")
        game = service.item(original,service.history()[0][original["id"]],"frozen",[.4,.3,.3],[.38,.29,.33])
        with patch.object(service,"prediction",return_value=game):
            for q,field in [("为什么更看好客队","explanation"),("平局概率","draw_explanation"),
                            ("模型 A 与市场","independent_market_explanation"),("模型 B 与市场","market_explanation")]:
                self.assertIn(game[field],service.ask(q,original["id"])["answer"])
