from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

from epl_agent.evaluation import formal_report
from epl_agent.evidence import audit_public, comment_api, _summary
from epl_agent.market import PROTOCOL, canonical, digest


class VerificationTests(unittest.TestCase):
    reg = {"protocol":PROTOCOL,"model":"test","created":"2026-11-20T00:00:00Z","seasons":["2027-28","2028-29"]}
    url = "https://github.com/test/project/issues/2#issuecomment-"
    now = datetime(2029,7,1,tzinfo=timezone.utc)

    def ledger(self):
        return {"registration":self.reg,"registration_evidence":{"at":self.reg["created"],"url":self.url+"1"},
                "health":None,"records":[]}

    def row(self, season, i, n):
        start = datetime(int(season[:4]),8,1,16,tzinfo=timezone.utc)
        kickoff = start+timedelta(weeks=i//10)
        cutoff = kickoff-timedelta(days=1)
        frozen = cutoff-timedelta(minutes=7)
        m = {"id":season+"-"+str(i),"season":season,"hg":1,"ag":0,"home":"Example H","away":"Example A",
             "kickoff_utc":kickoff.isoformat(),"schedule_revision":1}
        a, market = [.8,.1,.1],[.4,.3,.3]
        p = {"id":m["id"],"season":season,"kickoff":kickoff.isoformat(),"cutoff":cutoff.isoformat(),
             "frozen":frozen.isoformat(),"observed":frozen.isoformat(),"a":a,"market":market,
             "b":[.48,.26,.26],"model_version":"test","prediction":{"probs":a,"model_version":"test"},
             "schedule_observed_at":frozen.isoformat(),"schedule_revision":1,"protocol_hash":digest(self.reg)}
        row = {"id":m["id"],"kickoff":m["kickoff_utc"],"revision":1,"observed":p["observed"],"reason":"paired",
               "hash":digest(p),"payload":p,"witnessed":frozen.isoformat(),"evidence":self.url+str(n)}
        return m,row

    def test_independent_audit_and_untrusted_reference(self):
        ledger = self.ledger()
        m,r = self.row("2027-28",0,2)
        # Two hashes can be timestamped by one server-side comment.
        _,r2 = self.row("2027-28",1,2)
        ledger["records"] = [r,r2]
        def body(*pairs):
            return "EPL T-24h · SHA-256\n\n"+"\n".join(x for pair in pairs for x in pair)
        def comment(url,at,body):
            return {"html_url":url,"created_at":at,"updated_at":at,"body":body}
        docs = {comment_api(self.url+"1"):comment(self.url+"1",self.reg["created"],
                      body(("protocol "+digest(self.reg),canonical(self.reg)))),
                comment_api(r["evidence"]):comment(r["evidence"],r["witnessed"],body(*[
                    ("forecast "+row["hash"],canonical(_summary(row["payload"]))) for row in (r,r2)]))}
        http = Mock()
        def fetch(url,**kw):
            self.assertFalse(kw["allow_redirects"])
            return Mock(status_code=200,json=lambda:docs[url])
        http.get.side_effect = fetch
        audit = audit_public(ledger,http)
        self.assertEqual(audit["verified"],2)
        self.assertTrue(audit["ok"])
        self.assertEqual(http.get.call_count,2)
        docs[comment_api(r["evidence"])]["body"] += "modified-summary"
        self.assertFalse(audit_public(ledger,http)["ok"])
        docs[comment_api(r["evidence"])]["updated_at"] = self.now.isoformat()
        self.assertEqual(audit_public(ledger,http)["verified"],0)
        for foreign in ("https://github.com.evil/org/a/issues/2#issuecomment-1",
                        "https://github.com/test/../issues/1#issuecomment-2","http://127.0.0.1"):
            with self.assertRaises(ValueError):
                comment_api(foreign)
        http.get.side_effect = lambda *args,**kw: Mock(status_code=404)
        failed = audit_public(ledger,http)
        self.assertFalse(failed["registration_ok"])
        self.assertIn("404",failed["errors"][0]["error"])

    def test_rehashed_misaligned_forecast_is_excluded(self):
        m, row = self.row("2027-28",0,2)
        ledger = self.ledger()
        for update in ({"id":"other"},{"season":"other"},{"schedule_revision":9},
                       {"model_version":"other"},{"cutoff":row["kickoff"]},{"b":[.45,.28,.27]},
                       {"a":[1.,.2,.1]},{"frozen":row["kickoff"]}):
            bad = deepcopy(row)
            bad["payload"].update(update)
            bad["hash"] = digest(bad["payload"])
            ledger["records"] = [bad]
            self.assertEqual(formal_report([m],ledger,self.now)["n_pairs"],0, update)
        for field in ("evidence","witnessed","observed"):
            ledger["records"] = [dict(row,**{field:"bogus"})]
            self.assertEqual(formal_report([m],ledger,self.now)["n_pairs"],0)

    def test_two_full_seasons_and_each_season_coverage_and_public_proof(self):
        fixtures = []
        ledger = self.ledger()
        for s in self.reg["seasons"]:
            for i in range(380):
                m,r = self.row(s,i,2+len(fixtures))
                fixtures.append(m)
                if i < 342:
                    ledger["records"].append(r)
        self.assertEqual(formal_report(fixtures,ledger,self.now)["status"],"unproven")
        # Cached outcome of an independent audit; network evidence verified separately above.
        ledger["public_audit"] = {"ok":True,"registration_ok":True,"registration_hash":digest(self.reg),
            "registration_evidence":ledger["registration_evidence"],
            "forecasts":{r["hash"]:{"url":r["evidence"],"at":r["witnessed"]} for r in ledger["records"]}}
        report = formal_report(fixtures,ledger,self.now)
        self.assertEqual(report["status"],"leading")
        self.assertEqual(report["verification"]["public_pairs"],684)
        self.assertEqual(formal_report(fixtures[:-1],ledger,self.now)["status"],"accumulating")
        ledger["records"].pop()
        self.assertEqual(formal_report(fixtures,ledger,self.now)["status"],"unproven")
