import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from epl_agent import data, snapshot
from epl_agent.evaluation import formal_report, public_ledger
from epl_agent.evidence import _summary, store_collector_audit
from epl_agent.market import canonical, database, digest, PROTOCOL, write_value


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        env = patch.dict(os.environ,{"EPL_EVIDENCE_REPO":"","EPL_EVIDENCE_ISSUE":"","EPL_AGENT_HOME":str(self.dir/"db")})
        env.start()
        self.addCleanup(env.stop)

    def test_clean_room_round_trip_and_tampering(self):
        snapshot.export_bundle(self.dir/"out")
        path = self.dir/"out"/snapshot.NAME
        original = json.loads(path.read_bytes())
        self.assertEqual(original["format"],"public-snapshot/v2")
        self.assertIn("independent",original["scores"]["formal"])
        self.assertEqual(original["scores"]["formal"]["independent"]["n_scored"],0)
        with patch.dict(os.environ,{"EPL_AGENT_HOME":str(self.dir/"unused")}):
            verified = snapshot.verify(path.parent)
            self.assertEqual(len(verified["fixtures"]),760)
            self.assertFalse((self.dir/"unused").exists())
        for change in ("score","denominator","private","hash"):
            with self.subTest(change=change):
                bad = deepcopy(original)
                if change=="score":
                    bad["scores"]["formal"]["n_pairs"]=1
                elif change=="denominator":
                    bad["data"]["fixtures"].pop()
                elif change=="private":
                    bad["data"]["fixtures"][0]["raw"]={"bookmakers":[]}
                bad["sha256"]=digest(bad["data"]) if change!="hash" else "incorrect"
                path.write_text(canonical(bad),encoding="utf8")
                with self.assertRaises(ValueError):
                    snapshot.verify(path.parent)

    def test_v1_exact_score_shape_and_v2_public_status(self):
        snapshot.export_bundle(self.dir/"out")
        path = self.dir/"out"/snapshot.NAME
        modern = json.loads(path.read_bytes())
        legacy = deepcopy(modern)
        legacy["format"] = "public-snapshot/v1"
        for key in ("deployments","operations"):
            del legacy["data"][key]
        for key in ("formal","pilot"):
            del legacy["scores"][key]["independent"]
        legacy["sha256"] = digest(legacy["data"])
        path.write_text(canonical(legacy),encoding="utf8")
        self.assertEqual(snapshot.verify(path.parent)["as_of"],legacy["data"]["as_of"])
        legacy["scores"]["formal"]["independent"] = {}
        path.write_text(canonical(legacy),encoding="utf8")
        with self.assertRaisesRegex(ValueError,"recomputed scores"):
            snapshot.verify(path.parent)
        stamp = modern["data"]["as_of"]
        modern["data"]["operations"] = {"heartbeat_at":stamp,"sync_status":"ok","deployed_sha":"a"*40}
        modern["sha256"] = digest(modern["data"])
        path.write_text(canonical(modern),encoding="utf8")
        self.assertEqual(snapshot.verify(path.parent)["operations"]["heartbeat_at"],stamp)
        for fields in ({"private_log":"private"},{"upload_sha":"not-a-sha"},{"sync_status":"a/b"}):
            bad = deepcopy(modern)
            bad["data"]["operations"].update(fields)
            bad["sha256"] = digest(bad["data"])
            path.write_text(canonical(bad),encoding="utf8")
            with self.assertRaises(ValueError):
                snapshot.verify(path.parent)
        modern["scores"]["formal"]["independent"]["n_scored"] = 1
        path.write_text(canonical(modern),encoding="utf8")
        with self.assertRaisesRegex(ValueError,"recomputed scores"):
            snapshot.verify(path.parent)

    def test_publish_targets_only_data_and_code_marker(self):
        snapshot.export_bundle(self.dir/"out")
        session = Mock()
        session.get.side_effect = lambda url,**kw:Mock(status_code=200,json=lambda:
            [] if url.endswith("/comments") else {"private":False})
        commit = "1"*40
        calls = []
        def request(method,url,json=None,**kw):
            path = url.split("/repos/example/epl",1)[1]
            calls.append((method,path,json))
            status = 201
            result = {}
            if method=="GET":
                if path=="":
                    result={"private":False}
                elif path=="/git/ref/heads/public-data" and any(c[0]=="POST" and c[1]=="/git/refs" for c in calls):
                    result={"object":{"sha":commit}}
                else:
                    status=404
            elif path=="/git/blobs":
                result={"sha":"a"*40}
            elif path=="/git/trees":
                result={"sha":"b"*40}
            elif path=="/git/commits":
                result={"sha":commit}
            return Mock(status_code=status,json=lambda:result)
        session.request.side_effect = request
        settings={"EPL_EVIDENCE_REPO":"example/epl","EPL_EVIDENCE_ISSUE":"1","EPL_SNAPSHOT_GITHUB_TOKEN":"local-test",
                  "EPL_PAGES_CODE_BRANCH":"epl-v1"}
        with patch.dict(os.environ,settings):
            self.assertEqual(snapshot.publish_bundle(self.dir/"out",session=session),commit)
        self.assertIn(("POST","/git/refs",{"ref":"refs/heads/public-data","sha":commit}),calls)
        markers=[c for c in calls if c[0]=="PUT"]
        self.assertEqual([(c[1],c[2]["branch"]) for c in markers],[("/contents/.github/epl-public-data-sha","epl-v1")])

    def test_pinned_issue_omission_and_independent_scoring(self):
        issue = "https://github.com/example/epl/issues/1"
        registration = {"model":"A","protocol":PROTOCOL,"created":"2023-11-10T00:00:00+00:00","seasons":["2024-25","2025-26"]}
        rows=[r for r in data.load_matches() if r["season"] in registration["seasons"]]
        f=next(m for m in rows if m["kickoff_utc"] and m["hg"] is not None)
        cutoff=datetime.fromisoformat(f["kickoff_utc"])-timedelta(days=1)
        at=(cutoff-timedelta(minutes=1)).isoformat()
        a=[.6,.25,.15]; market=[.5,.3,.2]
        p=dict(id=f["id"],season=f["season"],kickoff=f["kickoff_utc"],cutoff=cutoff.isoformat(),observed=at,
               frozen=at,model_version="A",protocol_hash=digest(registration),schedule_observed_at=at,
               schedule_revision=0,a=a,market=market,b=[.8*m+.2*v for m,v in zip(market,a)],
               prediction=dict(probs=a,model_version="A"))
        def comment(i,at,key,value):
            return {"html_url":issue+"#issuecomment-"+str(i),"created_at":at,"updated_at":at,"user":{"id":9},"author_association":"OWNER",
                    "body":"EPL T-24h · SHA-256\n\n"+key+"\n"+canonical(value)}
        c1=comment(10,registration["created"],"protocol "+digest(registration),registration)
        c2=comment(11,at,"forecast "+digest(p),_summary(p))
        entry=dict(id=f["id"],kickoff=f["kickoff_utc"],revision=0,observed=at,reason="paired",hash=digest(p),
                   payload=p,evidence=c2["html_url"],witnessed=at)
        ledger = dict(registration=registration,registration_evidence={"url":c1["html_url"],"at":registration["created"]},
                      health=None,records=[entry])
        as_of=datetime(2026,7,1,tzinfo=timezone.utc)
        years={s:data.season_health(None,s,rows,as_of) for s in registration["seasons"]}
        batch=dict(as_of=as_of.isoformat(),fixtures=rows,versions=[],ledger=ledger,seasons_health=years,
                   overlay=data.openligadb_overlay(None))
        http=Mock()
        def get(url,**kw):
            if url.endswith("/issues/1/comments"):
                return Mock(status_code=200,json=lambda:[c1,c2])
            return Mock(status_code=200,json=lambda:{"private":False})
        http.get.side_effect=get
        trusted,scores=snapshot._check(batch,issue,http)
        self.assertEqual(scores["formal"]["n_pairs"],1)
        self.assertEqual(scores["pilot"]["n_pairs"],0)
        self.assertEqual(formal_report(rows,ledger,as_of)["verification"]["pending_pairs"],1)
        self.assertEqual(trusted["ledger"]["public_audit"]["verified"],1)
        # A v1 bundle may contain a paired score calculated with the full quote window.
        # Preserve its archived score and hash, while a v2 replay excludes the same early freeze.
        early_batch = deepcopy(batch)
        early_entry = early_batch["ledger"]["records"][0]
        early_at = (cutoff-timedelta(minutes=7)).isoformat()
        early_entry["payload"].update(schedule_observed_at=early_at,observed=early_at,frozen=early_at)
        early_hash = digest(early_entry["payload"])
        early_entry.update(observed=early_at,witnessed=early_at,hash=early_hash)
        early_comment = comment(11,early_at,"forecast "+early_hash,_summary(early_entry["payload"]))
        early_http = Mock()
        early_http.get.side_effect = lambda url,**kw:Mock(status_code=200,json=lambda:
            [c1,early_comment] if url.endswith("/comments") else {"private":False})
        legacy,legacy_scores = snapshot._check(early_batch,issue,early_http,version=snapshot.LEGACY_FORMAT)
        current,current_scores = snapshot._check(early_batch,issue,early_http)
        self.assertEqual(legacy_scores["formal"]["n_pairs"],1)
        self.assertEqual(current_scores["formal"]["n_pairs"],0)
        self.assertEqual(current_scores["formal"]["independent"]["n_scored"],0)
        self.assertEqual(current["ledger"]["records"][0]["hash"],legacy["ledger"]["records"][0]["hash"])
        self.assertNotIn("legacy_pair_window",early_batch["ledger"])
        solo_batch = deepcopy(batch)
        solo = solo_batch["ledger"]["records"][0]
        solo["payload"].update(market=None,b=None)
        solo.update(hash=digest(solo["payload"]),reason="no_api_key",market_reason="no_api_key")
        solo_comment = comment(11,at,"forecast "+solo["hash"],_summary(solo["payload"]))
        solo_http = Mock()
        solo_http.get.side_effect = lambda url,**kw:Mock(status_code=200,json=lambda:
            [c1,solo_comment] if url.endswith("/comments") else {"private":False})
        _,solo_scores = snapshot._check(solo_batch,issue,solo_http)
        self.assertEqual(solo_scores["formal"]["independent"]["n_scored"],1)
        self.assertEqual(solo_scores["formal"]["n_pairs"],0)
        solo_batch["ledger"]["records"].clear()
        with self.assertRaisesRegex(ValueError,"witness audit failed"):
            snapshot._check(solo_batch,issue,solo_http)
        with database() as db:
            write_value(db,"registration",registration)
            write_value(db,"registration_evidence",ledger["registration_evidence"])
            db.execute("""INSERT INTO captures (fixture,kickoff,revision,received,reason,hash,payload,evidence,witnessed)
                          VALUES(?,?,?,?,?,?,?,?,?)""",
                       (f["id"],entry["kickoff"],0,at,"paired",entry["hash"],canonical(p),entry["evidence"],at))
        with patch.dict(os.environ,{"EPL_EVIDENCE_REPO":"example/epl","EPL_EVIDENCE_ISSUE":"1"}):
            exported=snapshot.export_bundle(self.dir/"audited",session=http)
            local_ledger=public_ledger()
            local=formal_report(rows,local_ledger,datetime.fromisoformat(exported["as_of"]))
            self.assertEqual(local["n_pairs"],1)
            self.assertEqual(local["scores"],exported["scores"]["formal"]["scores"])
            self.assertEqual(local_ledger["public_audit"]["as_of"],exported["as_of"])
            # An independent verifier may use a fresh home and must leave it untouched.
            clean_home=self.dir/"reader"
            with patch.dict(os.environ,{"EPL_AGENT_HOME":str(clean_home)}):
                snapshot.verify(self.dir/"audited",session=http)
                self.assertFalse(clean_home.exists())
        # A slower audit must not roll a newer batch back, nor attach it to another issue or model.
        stale=deepcopy(trusted["ledger"]["public_audit"])
        stale["forecasts"].clear()
        self.assertFalse(store_collector_audit(None,ledger,stale,as_of))
        self.assertEqual(public_ledger()["public_audit"],local_ledger["public_audit"])
        for key in ("registration","registration_evidence"):
            foreign=deepcopy(ledger)
            foreign[key]=None
            self.assertFalse(store_collector_audit(None,foreign,stale,datetime.now(timezone.utc)))
        with database() as db:
            write_value(db,"registration_evidence",dict(url=issue+"#issuecomment-99",at=at))
        self.assertFalse(store_collector_audit(None,ledger,stale,datetime.now(timezone.utc)))
        self.assertEqual(public_ledger()["public_audit"],local_ledger["public_audit"])
        # The same type of witnessed receipt in the registration season is pilot-only.
        pilot = deepcopy(trusted["ledger"])
        pilot_reg = dict(registration,created=f["date"][:4]+"-07-01T00:00:00Z",seasons=["2025-26","2026-27"])
        pilot["registration"]=pilot_reg
        pilot["registration_evidence"]=dict(url=c1["html_url"],at=pilot_reg["created"])
        pilot["records"][0]["payload"]["protocol_hash"]=digest(pilot_reg)
        pilot["records"][0]["hash"]=digest(pilot["records"][0]["payload"])
        pilot["public_audit"].update(registration_evidence=pilot["registration_evidence"],registration_hash=digest(pilot_reg),
                                    forecasts={pilot["records"][0]["hash"]:{"at":at,"url":c2["html_url"]}})
        result=formal_report(rows,pilot,as_of)
        self.assertEqual((result["n_pairs"],result["pilot"]["n_pairs"]),(0,1))
        with self.assertRaises(ValueError):
            snapshot._check(batch,None,http)
        for change in ("issue","row","edited","unavailable"):
            with self.subTest(change=change):
                modified=deepcopy(batch)
                target=issue
                if change=="issue":
                    target="https://github.com/another/project/issues/2"
                elif change=="row":
                    modified["ledger"]["records"].clear()
                elif change=="edited":
                    c2["updated_at"]=cutoff.isoformat()
                else:
                    http.get.side_effect=lambda *args,**kwargs:Mock(status_code=503)
                with self.assertRaises(ValueError):
                    snapshot._check(modified,target,http)
