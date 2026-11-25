"""Regressions for the public result overlay, source conflicts and receipt clocks."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from epl_agent import data, source_sync


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "fixtures.sqlite3"
        self.season = "2026-27"
        self.base = [r for r in data.load_matches() if r["season"] == self.season]
        self.fixture = next(r for r in self.base if r["hg"] is None)
        self.fid = self.fixture["id"]

    def provider_row(self, h=2, a=1):
        row = {k:self.fixture[k] for k in ("id","season","home","away","date","kickoff_utc")}
        return dict(row, fixture_id="123", hg=h, ag=a)

    def synchronize(self, primary, secondary):
        p = {"side_effect":primary} if isinstance(primary,Exception) else {"return_value":(primary,"a"*40)}
        s = {"side_effect":secondary} if isinstance(secondary,Exception) else {"return_value":(secondary,"b"*64)}
        with patch.object(data,"_primary",**p), patch.object(source_sync,"_secondary",**s):
            return data.sync(self.db,season=self.season)

    def latest(self, cutoff=None):
        return next(r for r in data.load_matches(self.db,as_of=cutoff) if r["id"] == self.fid)

    def test_accept_only_exact_regulation_final_from_correct_competition(self):
        source = dict(leagueShortcut="pl",leagueSeason=2026,matchID=123,
                      matchDateTimeUTC=self.fixture["kickoff_utc"],matchIsFinished=True,
                      team1={"teamName":"FC Arsenal"},team2={"teamName":"Brentford F.C."},
                      matchResults=[dict(resultTypeKind="After90Minutes",pointsTeam1=3,pointsTeam2=1),
                                    dict(resultTypeKind="HalfTime",pointsTeam1=1,pointsTeam2=0)])
        def parsed(**updates):
            return source_sync.parse_secondary(json.dumps([{**source,**updates}]),self.season)[0]
        row = parsed()
        self.assertEqual((row["home"],row["away"],row["hg"],row["ag"]),("Arsenal","Brentford",3,1))
        self.assertIsNone(parsed(matchIsFinished=False)["hg"])
        self.assertIsNone(parsed(matchResults=source["matchResults"][1:])["hg"])
        self.assertIsNone(parsed(matchResults=[dict(resultTypeKind="AfterExtraTime",pointsTeam1=2,pointsTeam2=1)])["hg"])
        with self.assertRaises(ValueError):
            parsed(leagueSeason=2025)
        with self.assertRaises(ValueError):
            parsed(leagueShortcut="bl1")
        with self.assertRaises(ValueError):
            source_sync.parse_secondary(json.dumps([source,source]),self.season)

    def test_partial_overlay_is_idempotent_and_survives_failures(self):
        result = self.synchronize(self.base,[self.provider_row()])
        first = self.latest()
        self.assertEqual(first["result_source"],"openligadb")
        self.assertEqual(first["schedule_source"],"openfootball")
        self.assertEqual(first["result_observed_at"],first["source_observations"]["openligadb"]["observed_at"])
        self.assertIsNone(self.latest("2020-01-01T00:00:00Z")["hg"])
        self.assertEqual(result["completed"],51)
        self.assertEqual(self.synchronize(self.base,[])["changed"],0)
        self.assertEqual(data.openligadb_overlay(self.db,as_of="2020-01-01T00:00:00Z")["records"],[])
        overlay = data.openligadb_overlay(self.db,self.season)
        self.assertEqual((overlay["license"],len(overlay["records"])),("ODbL-1.0",1))
        self.assertNotIn("teamIconUrl",str(overlay))
        wrong = self.provider_row()
        wrong.update(id=wrong["id"]+"-reserves",away=wrong["away"]+" Reserves")
        self.assertEqual(self.synchronize(OSError("offline"),[wrong])["changed"],0)
        self.assertEqual(self.synchronize(OSError("offline"),OSError("offline"))["status"],"offline")
        self.assertEqual(self.latest(),first)
        versions = data.load_version_history(self.db)
        self.assertEqual(len(versions[self.fid]),1)
        self.assertEqual(len(data.load_versions(self.db)),380)
        self.assertEqual(len(data.load_versions(self.db,as_of=first["observed_at"])),380)

    def test_dispute_preserves_receipts_until_agreement_or_documented_resolution(self):
        self.synchronize(self.base,[self.provider_row(2,1)])
        first = self.latest()
        self.fixture.update(hg=1, ag=1)
        self.synchronize(self.base,[self.provider_row(2,1)])
        disputed = self.latest()
        self.assertIsNone(disputed["hg"])
        self.assertEqual(disputed["source_conflict"],"score_conflict")
        self.assertEqual(disputed["source_observations"]["openfootball"]["hg"],1)
        self.assertEqual(disputed["source_observations"]["openligadb"]["hg"],2)
        self.assertTrue(self.fid in data.season_health(self.db,self.season)["conflict_ids"])
        with self.assertRaises(ValueError):
            source_sync.resolve(self.db,self.season,self.fid,"openfootball","","")
        source_sync.resolve(self.db,self.season,self.fid,"openligadb","https://example.org/match","Verified full-time correction")
        resolved = self.latest()
        self.assertIsNone(resolved["source_conflict"])
        self.assertIn("resolution",resolved)
        self.assertEqual(resolved["hg"],2)
        self.assertGreater(resolved["result_observed_at"],disputed["observed_at"])
        self.assertEqual(self.synchronize(self.base,[self.provider_row(2,1)])["changed"],0)
        self.fixture.update(hg=3)
        self.synchronize(self.base,[self.provider_row(2,1)])
        self.assertEqual(self.latest()["source_conflict"],"score_conflict")
        self.synchronize(self.base,[self.provider_row(3,1)])
        self.assertIsNone(self.latest()["source_conflict"])
        self.assertEqual(self.latest()["hg"],3)
        self.assertEqual(self.latest(first["observed_at"]),first)
        self.assertEqual(self.latest(disputed["observed_at"]),disputed)

    def test_overdue_requires_known_kickoff_and_more_than_24_hours(self):
        now="2026-10-01T15:00:00Z"
        def row(fid,kickoff,hg=None):
            return dict(id=fid,season=self.season,date="2026-09-30",hg=hg,ag=hg,kickoff_utc=kickoff)
        rows=[row("y","2026-09-30T14:59:59Z"),row("boundary","2026-09-30T15:00:00Z"),
              row("unknown",None),row("settled","2026-09-30T14:59:59Z",0)]
        report = data.season_health(None,self.season,matches=rows,now=now)
        self.assertEqual(report["overdue_ids"],["y"])
        self.assertEqual(report["completed"],1)
        self.assertIsNone(report["last_sync_at"])

    def test_partial_secondary_only_never_witnesses_bundled_schedule(self):
        self.synchronize(OSError("offline"),[self.provider_row()])
        self.assertIsNone(self.latest()["schedule_observed_at"])
        self.assertIsNotNone(self.latest()["result_observed_at"])
        self.synchronize(self.base,[])
        self.assertIsNotNone(self.latest()["schedule_observed_at"])

    def test_legacy_worker_cannot_erase_supplementary_results(self):
        self.synchronize(self.base,[self.provider_row()])
        with closing(sqlite3.connect(self.db)) as db, self.assertRaisesRegex(sqlite3.IntegrityError, "v1 fixture"):
            db.execute("INSERT INTO fixture_versions VALUES(?,?,?)",
                       (self.fid,"2031-01-01T00:00:00.000000Z",json.dumps(self.fixture)))
        data.record_observation(self.db,self.season,self.base,source_commit="new",
                                observed="2031-01-01T00:00:00Z")
        self.assertEqual(self.latest()["result_source"],"openligadb")
