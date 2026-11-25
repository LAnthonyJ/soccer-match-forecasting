import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from epl_agent import data


class PublicDataTests(unittest.TestCase):
    def test_pinned_real_data_coverage_and_hash(self):
        report = data.read_coverage()
        self.assertEqual(report["source_commit"], data.PIN)
        self.assertEqual(hashlib.sha256((data.ASSETS / "matches.csv").read_bytes()).hexdigest(),
                         report["csv_sha256"])
        history = [s for s in report["seasons"] if s["season"] in data.HISTORY]
        self.assertEqual(len(history), 26)
        for season in history:
            self.assertEqual((season["completed"], season["fixtures"], season["teams"]), (380, 380, 20))
            self.assertEqual(season["missing"], [])
            self.assertEqual(season["duplicates"], [])
        self.assertEqual(len({m["id"] for m in data.load_matches()}), 10260)

    def test_dst_season_rollover_alias_and_missing_clock(self):
        text = """▪ Matchday 1
Sat Aug 16 2025
  15:00 Manchester City FC v Arsenal FC 3-0 (1-0)
▪ Matchday 14
Sat Dec 6
  15:00 Spurs v Liverpool FC
▪ Matchday 30
Sat Apr 4
  Chelsea FC v Everton FC
"""
        aug, dec, apr = data.parse_season(text, "2025-26")
        self.assertEqual(aug["kickoff_utc"], "2025-08-16T14:00:00.000000Z")
        self.assertEqual(dec["kickoff_utc"], "2025-12-06T15:00:00.000000Z")
        self.assertEqual(apr["date"], "2026-04-04")
        self.assertIsNone(apr["kickoff_utc"])
        self.assertIsNone(apr["ag"])
        self.assertEqual(data.fixture_id("2025-26", "曼城", "阿森纳"), aug["id"])
        self.assertEqual(dec["home"], "Tottenham Hotspur")

    def test_2025_explicit_offsets_override_mixed_wall_times(self):
        rows = {r["id"]: r for r in data.load_matches()}
        home = rows[data.fixture_id("2025-26", "Liverpool", "Bournemouth")]
        villa = rows[data.fixture_id("2025-26", "Aston Villa", "Newcastle")]
        self.assertEqual(home["kickoff_utc"], "2025-08-15T19:00:00.000000Z")
        self.assertEqual(villa["kickoff_utc"], "2025-08-16T11:30:00.000000Z")
        self.assertNotIn("schedule_observed_at", home)

    def test_revisions_known_at_the_time_and_no_partial_overwrite(self):
        rows = [r for r in data.load_matches() if r["season"] == "2026-27"]
        match = next(r for r in rows if r["hg"] is None)
        fid = match["id"]
        def lookup(path, cutoff=None):
            return next(r for r in data.load_matches(path, as_of=cutoff) if r["id"] == fid)
        def record(path, time):
            return data.record_observation(path, "2026-27", rows, source_commit="test", observed=time)
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "schedule.sqlite"
            record(db, "2026-07-20T00:00:00Z")
            old = lookup(db)
            match["kickoff_utc"] = None
            record(db, "2026-07-21T00:00:00Z")
            revised = lookup(db)
            self.assertEqual(old["schedule_revision"] + 1, revised["schedule_revision"])
            self.assertIsNone(revised["kickoff_utc"])
            self.assertEqual(lookup(db, "2026-07-20T12:00:00Z"), old)
            match.update(hg=1, ag=0)
            record(db, "2026-10-20T00:00:00Z")
            self.assertEqual(lookup(db)["schedule_observed_at"], revised["schedule_observed_at"])
            self.assertEqual(lookup(db)["result_observed_at"], "2026-10-20T00:00:00.000000Z")
            match["ag"] = 1
            record(db, "2026-10-21T00:00:00Z")
            self.assertEqual(lookup(db, "2026-10-20T12:00:00Z")["ag"], 0)
            self.assertEqual(lookup(db)["revised_fields"], ["ag"])
            rows.pop()
            with self.assertRaises(ValueError):
                record(db, "2026-10-22T00:00:00Z")
            self.assertEqual(lookup(db)["ag"], 1)

    def test_sync_rejects_invalid_upstream_pin_before_writing(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(data, "_get", return_value=b'{"sha":"not-a-sha"}'):
            db = Path(folder) / "test.sqlite"
            with self.assertRaisesRegex(ValueError, "revision"):
                data.sync(db, season="2026-27")
            self.assertFalse(db.exists())
