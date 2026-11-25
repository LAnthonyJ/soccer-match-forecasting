from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from epl_agent import data, freezing, market, schedule, snapshot, source_sync


class ScheduleReviewTests(unittest.TestCase):
    fid = "2026-27_nottingham-forest_chelsea"
    original = "2026-11-28T15:00:00.000000Z"
    revised = "2026-11-27T20:00:00.000000Z"

    def source_pair(self):
        rows = {p:{"kickoff_utc":t, "observed_at":"2026-11-20T12:00:00.000000Z"}
                for p,t in zip(schedule.PROVIDERS,(self.original,self.revised))}
        return {"id":self.fid, "source_observations":rows}

    def test_fixed_review_requires_both_exact_candidates_and_never_backdates(self):
        row = self.source_pair()
        sig = schedule.fingerprint(self.fid,row["source_observations"])
        review = schedule.reviews()["decisions"][sig]
        before = data._utc(datetime.fromisoformat(review["observed_at"])-timedelta(seconds=1))
        early = schedule.select(deepcopy(row),before)
        self.assertEqual((early["schedule_status"],early["kickoff_utc"]),("conflict",None))
        corrected = schedule.select(deepcopy(row),review["observed_at"])
        self.assertEqual(corrected["schedule_review"]["fingerprint"],sig)
        self.assertEqual((corrected["date"],corrected["kickoff_utc"]),("2026-11-27",self.revised))
        self.assertEqual(market.scheduled([corrected],datetime(2026,11,26,19,50,tzinfo=timezone.utc))[0][0],
                         datetime(2026,11,26,20,tzinfo=timezone.utc))
        self.assertEqual(market.scheduled([corrected],datetime(2026,11,27,14,50,tzinfo=timezone.utc)),[])
        row["source_observations"]["openligadb"]["kickoff_utc"]="2026-11-27T20:30:00.000000Z"
        pending = schedule.select(row,review["observed_at"])
        self.assertEqual((pending["schedule_status"],pending["kickoff_utc"]),("conflict",None))
        self.assertEqual(market.scheduled([pending],datetime(2026,11,26,20,tzinfo=timezone.utc)),[])

    def test_edition_keeps_original_and_corrects_only_after_local_receipt(self):
        rows = [r for r in data.load_matches() if r["season"] == "2026-27"]
        primary = next(r for r in rows if r["id"] == self.fid)
        primary.update(kickoff_utc=self.original,date="2026-11-28")
        second = dict(primary,fixture_id="789",kickoff_utc=self.revised,date="2026-11-27")
        first = "2026-11-24T12:00:00.000000Z"
        second_time = "2026-11-26T10:00:00.000000Z"
        with TemporaryDirectory() as folder:
            path = Path(folder)/"fixtures.sqlite3"
            with closing(sqlite3.connect(path)) as db,db:
                source_sync._setup(db)
                data._record_view(db,"2026-27",rows,source_commit="old",observed=first)
                source_sync._store(db,"2026-27","openfootball",rows,first,"a")
                source_sync._store(db,"2026-27","openligadb",[second],first,"b")
                revised = source_sync._merged(db,"2026-27",second_time)
                data._record_view(db,"2026-27",revised,source_commit="new",observed=second_time)
            def read(at):
                return next(m for m in data.load_matches(path,as_of=at) if m["id"]==self.fid)
            old,new = read(first),read(second_time)
            self.assertEqual((old["kickoff_utc"],new["kickoff_utc"]),(self.original,self.revised))
            self.assertEqual(new["schedule_revision"],old["schedule_revision"]+1)
            self.assertEqual(new["schedule_observed_at"],second_time)
            with patch.object(freezing,"root_dir",return_value=Path(folder)):
                with freezing.current_schedule(old) as current:
                    self.assertFalse(current)
                with freezing.current_schedule(new) as current:
                    self.assertTrue(current)
            snapshot._fixture_shape(new,snapshot.FORMAT)
            tampered=deepcopy(new)
            tampered["schedule_review"]["provider"]="openfootball"
            with self.assertRaisesRegex(ValueError,"Schedule decision"):
                snapshot._fixture_shape(tampered,snapshot.FORMAT)
            with closing(sqlite3.connect(path)) as db:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("INSERT INTO fixture_versions VALUES(?,?,?)",(self.fid,"2099",json.dumps(old)))

    def test_pending_is_different_from_two_observed_sources_in_agreement(self):
        row = self.source_pair()
        when = "2026-11-26T10:00:00.000000Z"
        row["source_observations"].pop("openligadb")
        self.assertEqual(schedule.select(deepcopy(row),when)["schedule_status"],"unconfirmed")
        row["source_observations"]["openligadb"]=dict(row["source_observations"]["openfootball"])
        self.assertEqual(schedule.select(row,when)["schedule_status"],"verified")

    def test_schedule_arrived_after_cutoff_cannot_be_charged_as_a_missed_worker(self):
        with TemporaryDirectory() as folder, market.database(Path(folder)) as db:
            late = {"id":self.fid, "kickoff_utc":self.revised, "schedule_revision":3,
                    "schedule_observed_at":"2026-11-26T20:12:00Z","schedule_status":"verified"}
            market._record_missed(db,[late],datetime(2026,11,27,tzinfo=timezone.utc),
                                  datetime(2026,11,20,tzinfo=timezone.utc))
            row = db.execute("SELECT freeze_reason,payload FROM captures WHERE fixture=?",(self.fid,)).fetchone()
            self.assertEqual(tuple(row),("schedule_unverified",None))
