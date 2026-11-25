from datetime import datetime, timezone
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

    def test_missing_key_and_missed_freeze_have_independent_explanations(self):
        fixture = {"id":"game-1","kickoff_utc":"2026-09-10T15:00:00Z","date":"2026-09-10",
                   "home":"Arsenal","away":"Chelsea","hg":None,"ag":None,"season":"2026-27"}
        now = datetime(2026,9,12,tzinfo=timezone.utc)
        # v2 records preserve both reasons. A legacy market-only record can still explain
        # missing odds; a legacy missed-window record gives no evidence about the odds key.
        for fields, market in [
            ({"reason":"missed_window","freeze_reason":"missed_window","market_reason":"missing_api_key"},"missing_api_key"),
            ({"reason":"missing_api_key"},"missing_api_key"),
            ({"reason":"missed_window"},"missing_market"),
        ]:
            with self.subTest(fields=fields):
                ledger = {"records":[dict(fields,id=fixture["id"],revision=0,kickoff=fixture["kickoff_utc"])]}
                with patch.object(service,"model_inputs",return_value={}), patch.object(service,"model_dir"), \
                        patch.object(service,"_history_for",return_value=({},{})), \
                        patch.object(service.models,"forecast_many",return_value={}):
                    game = service._present([fixture],[fixture],ledger,now)[0]
                service.lifecycle.attach([game],[fixture],[],ledger,{},[],now)
                self.assertEqual(game["trace"]["freeze_reason"],"missed_window")
                self.assertEqual(game["trace"]["market_reason"],market)
                self.assertEqual(game["market_note"],service.REASONS[market])
                self.assertIn(game["freeze_note"],game["trace_answer"])
                self.assertIn(game["market_note"],game["trace_answer"])
                with patch.object(service,"fixtures",return_value=[fixture]), patch.object(service,"prediction",return_value=game):
                    self.assertIn(game["market_note"],service.ask("为什么没有赔率？",fixture["id"])["answer"])
                    self.assertIn(game["freeze_note"],service.ask("本场预测？",fixture["id"])["answer"])
