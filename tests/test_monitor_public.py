from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase
from unittest.mock import patch

from scripts import monitor_public as monitor
from scripts.verify_analysis_restore import inspect


class PublicProbeTests(TestCase):
    end = datetime(2025, 1, 8, tzinfo=timezone.utc)
    start = end-timedelta(days=7)
    site = "https://monitor.example"
    sha = "a"*40
    keys = {n:n.encode()*12 for n in monitor.NETWORKS}

    def observation(self, net, when, **changes):
        row = {"at":when.isoformat(), "site":self.site, "network":net,
               "slot":int(when.timestamp())//300, "asns":[min(monitor.NETWORKS[net])],
               "sha":self.sha, "sha_seen_at":(when+timedelta(seconds=2)).isoformat(),
               "ok":True, "ai":None, "browser_at":None, **changes}
        raw = monitor.canonical(row)
        return raw.decode(), hmac.new(self.keys[net],raw,hashlib.sha256).hexdigest()

    def report(self, observations, uploads=True):
        manifest = {"schema":"epl-upload-times/v1", "from_utc":self.start.isoformat(),
                    "through_utc":self.end.isoformat(),
                    "uploads":[{"sha":self.sha, "uploaded_at":self.start.isoformat()}]}
        if not uploads:
            manifest.update(from_utc=None, uploads=[])
        return monitor.summarize(observations,self.site,self.end,self.keys,manifest)

    def test_all_networks_full_week_and_each_upload(self):
        records = []
        for net in monitor.NETWORKS:
            for i in range(monitor.SLOTS):
                when = self.start + timedelta(seconds=i*300)
                first_today = i%288==0
                records.append(self.observation(net,when,ai="deepseek" if first_today else None,
                                                 browser_at=when.isoformat() if first_today else None))
        result = self.report(records)
        self.assertTrue(result["passed"])
        self.assertEqual(result["carriers"]["mobile"]["ai_error_rate"],0)
        self.assertEqual(result["carriers"]["mobile"]["upload_delay_seconds"][self.sha],2)
        # Dropping 11 / 2,016 leaves this carrier below 99.5%, even if the others remain healthy.
        result = self.report(records[11:])
        self.assertFalse(result["passed"])
        self.assertEqual(result["carriers"]["telecom"]["missing_slots"],11)

    def test_distinguishes_missing_unverified_failed_and_ai_fallback(self):
        mobile = lambda i, **kw: self.observation("mobile",self.start+timedelta(minutes=5*i),**kw)
        records = [mobile(0,ai="fallback"), mobile(1,ok=False), mobile(2,asns=[4134]),
                   mobile(3,ai="failed"), mobile(4,ai="deepseek")]
        records += [records[0],(mobile(5)[0],"bad signature")]
        report = self.report(records,uploads=False)
        stats = report["carriers"]["mobile"]
        self.assertFalse(report["upload_manifest_verified_range"])
        self.assertEqual((stats["missing_slots"],stats["unverified_slots"],stats["failed_core_slots"]),
                         (monitor.SLOTS-5,1,1))
        self.assertEqual(stats["ai_attempts"],3)
        self.assertAlmostEqual(stats["ai_error_rate"],2/3)
        self.assertFalse(stats["passed"])
        with self.assertRaisesRegex(ValueError,"Conflicting"):
            self.report(records+[mobile(0,ai="deepseek")])

    def test_network_requires_same_origin_edge_country_and_asn(self):
        for country,asn,ok in [("CN",9808,True),("CN",4837,False),("HK",9808,False)]:
            with self.subTest(country=country,asn=asn), patch.object(
                    monitor,"read",return_value=json.dumps({"country":country,"asn":asn}).encode()) as get:
                if ok:
                    self.assertEqual(monitor.network(None,self.site,"mobile"),[9808])
                else:
                    with self.assertRaises(ValueError):
                        monitor.network(None,self.site,"mobile")
                get.assert_called_once_with(None,self.site+"/api/monitor/network",limit=4096)

    def test_missing_and_late_uploads_are_not_timely(self):
        when = self.start+timedelta(minutes=10)
        report = self.report([self.observation("mobile",when)])
        self.assertEqual(report["carriers"]["mobile"]["upload_delay_seconds"][self.sha],602)
        self.assertIsNone(report["carriers"]["unicom"]["upload_delay_seconds"][self.sha])
        self.assertFalse(report["carriers"]["mobile"]["passed"])

    def test_each_slot_once_each_daily_followup_once_even_on_failure(self):
        with closing(monitor.open_db(":memory:")) as db, patch.object(monitor,"key_for",return_value=b"test"), \
             patch.object(monitor,"network",return_value=[9808]), patch.object(monitor,"health",return_value={}), \
             patch.object(monitor,"live_ai",side_effect=OSError) as ask, patch.object(monitor,"stamp") as now:
            now.return_value=self.start
            self.assertEqual(monitor.sample(db,"mobile",self.site,"fixture")["ai"],"failed")
            self.assertIsNone(monitor.sample(db,"mobile",self.site,"fixture"))
            now.return_value=self.start+timedelta(minutes=5)
            self.assertIsNone(monitor.sample(db,"mobile",self.site,"fixture")["ai"])
            self.assertEqual(ask.call_count,1)
            now.return_value=self.start+timedelta(days=1)
            self.assertEqual(monitor.sample(db,"mobile",self.site,"fixture")["ai"],"failed")
            self.assertEqual(ask.call_count,2)

    def test_offline_ledger_restore_preserves_unsettled_reservations_and_cached_report(self):
        with tempfile.TemporaryDirectory() as work, closing(sqlite3.connect(":memory:")) as db:
            db.executescript((monitor.ROOT/"cloudflare/migrations/0001_analysis.sql").read_text("utf8"))
            db.execute("INSERT INTO analysis_calls VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("q","2025-01","2025-01-07","anonymous","ask","k",50,"pending",700,700))
            db.execute("INSERT INTO analysis_reports VALUES(?,?,?)",("k",'{"fact_hash":"offline-example"}',"stamp"))
            sql="\n".join(db.iterdump())
            reference=Path(work)/"source.sql"
            reference.write_text(sql,encoding="utf8")
            saved=inspect(reference)
            self.assertEqual(saved["monthly_usage"][0]["pending_microyuan"],700)
            restored=Path(work)/"restored.sql"
            with closing(sqlite3.connect(Path(work)/"restore.sqlite3")) as copy:
                copy.executescript(sql)
                restored.write_text("\n".join(copy.iterdump()),encoding="utf8")
                self.assertEqual(saved,inspect(restored))
                copy.execute("UPDATE analysis_calls SET charged=0")
                restored.write_text("\n".join(copy.iterdump()),encoding="utf8")
            self.assertNotEqual(saved,inspect(restored))
