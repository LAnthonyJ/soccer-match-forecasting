import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from epl_agent import analysis, team_history
from epl_agent.web import create_app


def game(home, away, date, score=(None, None), season="2026-27"):
    return {"id":f"{season}_{home.lower()}_{away.lower()}", "home":home, "away":away,
            "date":date, "season":season, "kickoff_utc":date+"T15:00:00Z", "hg":score[0], "ag":score[1]}


def version(row, observed, **changes):
    return {**row, "observed_at":observed+"T19:00:00Z", **changes}


class TeamHistoryTests(TestCase):
    def setUp(self):
        self.target = game("Chelsea", "Arsenal", "2026-10-20")
        self.stamp = "2026-09-25T18:00:00+00:00"

    def test_as_of_revisions_conflict_withdrawal_and_recovery(self):
        a = game("Chelsea","Arsenal","2026-09-01",(1,1),season="2025-26")
        versions = [version(a,"2026-09-02"), version(a,"2026-09-12",hg=2),
                    version(a,"2026-09-16",source_conflict=True), version(a,"2026-09-18",withdrawn=True),
                    version(a,"2026-09-20",hg=2)]
        index = team_history.ResultIndex([self.target],versions)
        cases = [("2026-09-01",None),("2026-09-10",(1,1)),("2026-09-14",(2,1)),
                 ("2026-09-17",None),("2026-09-19",None),("2026-09-22",(2,1))]
        for day, expected in cases:
            with self.subTest(day=day):
                q = index.context({**self.target,"prediction":{"cutoff":day+"T23:00:00Z"}},self.stamp,"prematch")
                rows = q["recent"]["home"]
                self.assertEqual([(r["hg"],r["ag"]) for r in rows], [] if expected is None else [expected])
                self.assertEqual(q["h2h"] and (q["h2h"]["hg"],q["h2h"]["ag"]),expected)
                self.assertEqual(q["missing_recent"]["home"],[a["id"]] if day=="2026-09-17" else [])

    def test_hand_calculated_table_and_shared_ranks(self):
        matches = [game("Arsenal","Chelsea","2026-09-01",(2,1)),game("Chelsea","Fulham","2026-09-02",(3,0)),
                   game("Leeds","Arsenal","2026-09-03",(2,0)),game("Fulham","Leeds","2026-09-04",(2,1)),
                   game("Ipswich","Coventry","2026-09-05",(0,0)), game("Ipswich","Arsenal","2026-09-23")]
        index = team_history.ResultIndex([*matches,self.target])
        ctx = index.context(self.target,self.stamp,"prematch")
        table = ctx["standings"]
        actual = [(r["team"],r["rank"],r["played"],r["wins"],r["draws"],r["losses"],r["gf"],r["ga"],r["points"])
                  for r in table["rows"]]
        self.assertEqual(actual,[("Chelsea",1,2,1,0,1,4,2,3),("Leeds",2,2,1,0,1,3,2,3),
                                 ("Arsenal",3,2,1,0,1,2,3,3),("Fulham",4,2,1,0,1,2,4,3),
                                 ("Coventry",5,1,0,1,0,0,0,1),("Ipswich",5,1,0,1,0,0,0,1)])
        self.assertEqual((table["confirmed"],table["missing_ids"]),(5,[matches[-1]["id"]]))
        self.assertTrue(all(x["stage"]=="prematch" for x in ctx["facts"]))

    def test_playing_order_last_five_sample_shortage_and_cross_season(self):
        opener=game("Chelsea","Everton","2026-05-03",(1,3),season="2025-26")
        promoted=game("Ipswich","Chelsea","2026-09-01",(1,2))
        postponed=game("Chelsea","Leeds","2026-08-05",(0,0))
        changed=version(postponed,"2026-09-14",date="2026-09-13",kickoff_utc="2026-09-13T20:00:00Z",hg=3)
        others=[game("Fulham","Chelsea","2026-09-06",(1,1)),game("Arsenal","Chelsea","2026-09-04",(3,0))]
        missing=game("Liverpool","Chelsea","2026-09-20")
        index=team_history.ResultIndex([self.target,opener,promoted,*others,missing],[changed])
        ctx=index.context(self.target,self.stamp,"prematch")
        home=ctx["recent"]["home"]
        self.assertEqual([x["id"] for x in home],[postponed["id"],others[0]["id"],others[1]["id"],promoted["id"],opener["id"]])
        self.assertEqual((home[0]["gf"],home[0]["outcome"],home[-1]["season"]),(3,"胜","2025-26"))
        self.assertEqual(ctx["missing_recent"]["home"],[missing["id"]])
        self.assertEqual(ctx["h2h"]["id"],others[1]["id"])
        ipswich=index.context(game("Ipswich","Fulham","2026-12-01"),self.stamp,"prematch")
        self.assertEqual(len(ipswich["recent"]["home"]),1)

    def test_current_confirmed_same_day_and_future_result_never_enters_preview(self):
        a=game("Arsenal","Chelsea","2026-09-22",(1,0))
        stamp="2026-09-22T20:00:00Z"
        observed=version(a,"2026-09-22",result_observed_at="2026-09-22T19:00:00Z")
        accidental=version(self.target,"2026-09-21",hg=8,ag=9)
        index=team_history.ResultIndex([self.target],[observed,accidental])
        current=index.context(self.target,stamp,"current")
        pre=index.context(self.target,stamp,"prematch")
        self.assertEqual([r["id"] for r in current["recent"]["home"]],[a["id"]])
        self.assertEqual(pre["recent"]["home"],[])
        self.assertEqual(pre["standings"]["confirmed"],0)
        self.assertNotIn("8–9",json.dumps(pre,ensure_ascii=False))

    def test_scoped_download_matches_flask_reader_and_old_result_is_reproducible(self):
        row=game("Arsenal","Chelsea","2026-09-01",(1,1))
        index=team_history.ResultIndex([row,self.target],[version(row,"2026-09-12",hg=2)])
        with TemporaryDirectory() as tmp:
            for label, day, expected in (("old","2026-09-10",1),("new","2026-09-14",2)):
                dest=Path(tmp)/label
                stamp=day+"T18:00:00+00:00"
                team_history.export_season(dest,"2026-27",[self.target],index,stamp)
                path=dest/"team-history/2026-27/prematch/chelsea.json"
                item=json.loads(path.read_bytes())[self.target["id"]]
                self.assertEqual(item["h2h"]["hg"],expected)
                with patch("epl_agent.service.datetime") as clock, \
                     patch("epl_agent.service.prediction",return_value=self.target), \
                     patch("epl_agent.service.model_inputs",return_value={"archive":[row,self.target],"versions":[version(row,"2026-09-12",hg=2)]}):
                    clock.now.return_value=datetime(2026,11,26,tzinfo=timezone.utc)
                    reply=create_app().test_client().get("/api/matches/"+self.target["id"]+"/team-history",
                                                         query_string={"as_of":stamp})
                    self.assertEqual(reply.json,item)
            old=json.loads((Path(tmp)/"old/team-history/2026-27/prematch/chelsea.json").read_bytes())
            self.assertEqual(old[self.target["id"]]["h2h"]["hg"],1)

    def test_single_scoreline_rounding_tie_and_missing_topic(self):
        p={"a":[.4,.3,.3],"samples":[5,5],"source":"preview","cutoff":self.stamp,"model_version":"m",
           "top_scores":[[1,1,.12974404],[1,0,.12972],[2,1,.08]]}
        g={**self.target,"home_zh":"切尔西","away_zh":"阿森纳","prediction":p}
        pre=analysis.packet(g,self.stamp)
        text=next(f["text"] for f in pre["facts"] if f["id"]=="top_scores")
        self.assertIn("1–1 约 13.0%",text)
        self.assertIn("并列",text)
        self.assertIn("top_scores",pre["available_topics"])
        self.assertNotIn("injuries",pre["available_topics"])
        self.assertNotIn("market",pre["available_topics"])
        self.assertNotIn("top_scores",analysis.packet({**g,"prediction":{**p,"top_scores":[]}},self.stamp)["available_topics"])
