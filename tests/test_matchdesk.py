from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from epl_agent import data, service
from epl_agent.evaluation import public_ledger


class MatchdeskTests(TestCase):
    def test_read_only_ledger_and_local_paged_history(self):
        from epl_agent.web import create_app
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/"absent"
            self.assertEqual(public_ledger(root)["records"],[])
            self.assertFalse(root.exists())
        client = create_app().test_client()
        all_rows = client.get("/api/history-rows?season=2024-25").get_json()
        p1 = client.get("/api/history-rows?season=2024-25&page=1").get_json()
        p2 = client.get("/api/history-rows?season=2024-25&page=2").get_json()
        self.assertEqual((len(all_rows),len(p1),len(p2)),(380,20,20))
        self.assertTrue({r["id"] for r in p1}.isdisjoint(r["id"] for r in p2))
        self.assertEqual(client.get("/api/history-rows?page=-1").status_code,400)

    def test_next_fixture_and_named_cohorts(self):
        now = datetime.now(timezone.utc)
        matches = []
        for i,opponent in enumerate(("Chelsea","Fulham","Liverpool","Brentford")):
            when = now + timedelta(days=i-1,minutes=-1 if i==1 else 0)
            matches.append({"id":str(i),"date":when.date().isoformat(),"kickoff_utc":when.isoformat(),
                            "season":"2026-27","hg":None,"ag":None,"schedule_status":"verified","home":"Arsenal","away":opponent})
        shortly = (now + timedelta(minutes=30)).isoformat()
        matches.extend([{**matches[2],"id":"unconfirmed","away":"Newcastle United","date":shortly[:10],
                         "kickoff_utc":None,"schedule_status":"unconfirmed"},
                        {**matches[2],"id":"conflict","away":"Brighton & Hove Albion","date":shortly[:10],
                         "kickoff_utc":shortly,"schedule_status":"conflict"}])
        with patch.object(service,"fixtures",return_value=matches):
            result = service.ask("阿森纳下一场踢谁","3")
            self.assertEqual(result["fixture_id"],"2")
            self.assertIn("利物浦",result["answer"])
            trial = {"season":"2026-27","paired":2,"model_loss":1.0,"market_loss":1.05}
            stamp = "2026-11-25T18:00:00+00:00"
            with patch.object(service,"formal_report",return_value={"as_of":stamp,"seasons":[],"pilot":{"seasons":[trial]}}), \
                    patch.object(service,"public_ledger",return_value={}):
                self.assertIn("试运行有 2 场",service.ask("2026-27 试运行成绩")["answer"])
                self.assertIn("正式评测尚无",service.ask("2026-27 正式评测")["answer"])
                self.assertIn("历史回放有",service.ask("2025-26 历史回放成绩")["answer"])
                trial.update(paired=0,sample_note="等待确认赛果 2 场，已记录缺少合格同期报价 3 场。")
                answer = service.ask("2026-27 试运行成绩")["answer"]
                self.assertIn(trial["sample_note"],answer)
                self.assertIn(stamp,answer)

    def test_verified_watermark_stops_at_gap_and_advances_after_observed_correction(self):
        rows = [{"season":"2026-27","date":f"2026-09-0{i}","kickoff_utc":f"2026-09-0{i}T15:00:00Z",
                 "hg":1,"ag":0,"schedule_status":"verified"} for i in (1,2,3)]
        rows[1]["result_observed_at"] = "2026-09-06T09:00:00Z"
        def mark(day):
            return service.score_watermark(rows,"2026-27",datetime(2026,9,day,tzinfo=timezone.utc))
        self.assertEqual(mark(5),{"settled_through":"2026-09-01","latest_confirmed_score_date":"2026-09-03"})
        self.assertEqual(mark(7)["settled_through"],"2026-09-03")
        rows[0].update(source_conflict=True)
        self.assertIsNone(mark(7)["settled_through"])

    def test_protocol_window_uses_the_selected_revised_kickoff(self):
        from epl_agent import schedule
        for kickoff,deadline in (("2026-11-27T20:00:00+00:00","2026-11-26T20:00:00+00:00"),
                                 ("2026-11-29T16:30:00+00:00","2026-11-28T16:30:00+00:00")):
            game={"id":"match","date":kickoff[:10],"kickoff_utc":kickoff,"home":"Arsenal","away":"Chelsea",
                  "schedule_status":"verified","trace":{"cutoff":deadline}}
            with patch.object(service,"fixtures",return_value=[game]):
                answer=service.ask("这场的 T−24 截止、冻结窗口是什么时候？",game["id"])
            self.assertEqual(answer["answer"],schedule.protocol_window_text(game))
            self.assertIn(deadline,answer["answer"])
            self.assertIn((datetime.fromisoformat(deadline)-timedelta(minutes=15)).isoformat(),answer["answer"])
            self.assertIn((datetime.fromisoformat(deadline)-timedelta(minutes=2)).isoformat(),answer["answer"])
            self.assertIn("待定",schedule.protocol_window_text(dict(game,schedule_status="conflict")))
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
