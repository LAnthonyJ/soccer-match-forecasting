from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from epl_agent import fan, service
from epl_agent.web import create_app


class FanBoardTests(TestCase):
    now = datetime(2026,11,26,12,tzinfo=timezone.utc)

    def game(self, away, day, week, **changes):
        return dict({"id":"2026-27_arsenal_"+away.lower(),"home":"Arsenal","away":away,
                     "season":"2026-27","date":day,"kickoff_utc":day+"T20:00:00Z",
                     "schedule_status":"verified","round":week,"hg":None,"ag":None},**changes)

    def test_shared_round_keeps_finished_waiting_and_tba_in_their_own_season(self):
        latest = self.game("Chelsea","2026-11-28",12,
            prediction={"a":[.35,.28,.37],"source":"preview","cutoff":self.now.isoformat(),"xg":[1.1,1.2]})
        tba = self.game("Liverpool","2026-11-27",12,schedule_status="conflict")
        done = self.game("Fulham","2026-11-24",12,hg=2,ag=0)
        waiting = self.game("Everton","2026-11-25",12,prediction=latest["prediction"])
        corrected = self.game("Brentford","2026-11-23",12,hg=2,ag=1,source_conflict={"source":"other"})
        past_round = self.game("Leeds","2026-11-12",2,hg=0,ag=3)
        old_match = dict(past_round,id="2025-26_arsenal_leeds",season="2025-26",round=12)
        games = [latest,tba,done,waiting,corrected,past_round,old_match]
        fid = service._current_id(games,self.now)
        self.assertEqual(fid,latest["id"])
        full={"fixtures":games,"updated_at":self.now.isoformat(),"public_snapshot":True,"current_id":fid}
        exported = fan.export_board(full,games)
        self.assertEqual(exported["week_catalog"]["2026-27"],["第 2 轮","第 12 轮"])
        self.assertEqual(exported["current_week"],"第 12 轮")
        self.assertFalse(exported["season_finished"])
        self.assertNotIn("report",exported)
        cards = {x["id"]:x for x in exported["fixtures"]}
        self.assertEqual(set(cards),{x["id"] for x in games[:5]})
        self.assertIsNone(cards[tba["id"]]["kickoff_utc"])
        self.assertIsNone(cards[waiting["id"]]["prediction"])
        self.assertIsNone(cards[corrected["id"]]["hg"])
        self.assertEqual(cards[done["id"]]["hg"],2)
        self.assertEqual(set(cards[fid]["prediction"]),{"a","source","cutoff"})
        with TemporaryDirectory() as tmp:
            directory=Path(tmp)
            fan.write_season(directory,"2026-27",games[:-1],self.now)
            self.assertEqual(json.loads((directory/"rounds/2026-27/r12.json").read_bytes()),exported["fixtures"])
            self.assertEqual(json.loads((directory/"fixtures"/(fid+".json")).read_bytes()),latest)
            concluded=json.loads((directory/"fixtures"/(done["id"]+".json")).read_bytes())
            self.assertIsNone(concluded["prediction"])
            self.assertEqual(concluded["ag"],0)

    def test_last_round_and_exact_live_score_links(self):
        games=[self.game("Chelsea","2026-05-25",37,hg=1,ag=1),
               self.game("Everton","2026-05-17",38,hg=0,ag=2)]
        client=create_app().test_client()
        with patch.object(service,"fixtures",return_value=games), \
                patch.object(service,"_present",side_effect=AssertionError("completed fixtures need no model")):
            board=client.get("/api/board").get_json()
            self.assertTrue(board["season_finished"])
            self.assertEqual(board["current_week"],"第 38 轮")
            rows=client.get("/api/rounds/2026-27",query_string={"week":"第 37 轮"}).get_json()
            self.assertEqual([r["id"] for r in rows],[games[0]["id"]])
            self.assertEqual(client.get("/api/matches/"+games[1]["id"]).get_json()["ag"],2)
            self.assertEqual(client.get("/api/matches/"+games[1]["id"]+"x").status_code,404)
            self.assertEqual(client.get("/api/rounds/2026-27?week=missing").status_code,404)
