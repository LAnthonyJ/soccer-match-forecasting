"""Anonymous public-site measurements from three independently operated mainland China networks."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from urllib.parse import urljoin, urlsplit

import requests

UTC = timezone.utc
NETWORKS = {"telecom":{4134,4809}, "unicom":{4837,9929}, "mobile":{9808}}
SHA = re.compile("[0-9a-f]{40}")
FID = re.compile(r"20\d{2}-\d{2}_[a-z0-9-]+_[a-z0-9-]+")
SLOT = 300
SLOTS = 7*24*3600//SLOT
ROOT = Path(__file__).resolve().parents[1]


def stamp():
    return datetime.now(UTC)


def instant(value):
    t = datetime.fromisoformat(value.replace("Z","+00:00"))
    if t.tzinfo is None:
        raise ValueError("Timezone required")
    return t.astimezone(UTC)


def canonical(row):
    return json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()


def key_for(net):
    value=os.getenv("EPL_MONITOR_"+net.upper()+"_KEY","")
    if len(value)<32:
        raise ValueError("Configure a separate probe signing key of at least 32 characters for "+net)
    return value.encode()


def origin(raw):
    u=urlsplit(raw)
    if u.scheme!="https" or not u.hostname or u.username or u.password or u.path not in ("","/") or u.fragment or u.query:
        raise ValueError("A public HTTPS site origin is required")
    return raw.rstrip("/")


def read(session,url,*,limit=1_000_000):
    with session.get(url,timeout=(4,12),headers={"Cache-Control":"no-cache"},stream=True,allow_redirects=False) as res:
        if res.status_code!=200:
            raise ValueError("HTTP "+str(res.status_code))
        buf=bytearray()
        for chunk in res.iter_content(16_384):
            buf.extend(chunk)
            if len(buf)>limit:
                raise ValueError("Oversized probe response")
        return bytes(buf)


def network(session,url,net):
    info=json.loads(read(session,url+"/api/monitor/network",limit=4096))
    if info.get("country")!="CN":
        raise ValueError("unverified_mainland_egress")
    if info.get("asn") not in NETWORKS[net]:
        raise ValueError("unverified_carrier_asn")
    return [info["asn"]]


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths={}

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=="script" and attrs.get("type")=="module":
            self.paths["js"]=attrs.get("src")
        if tag=="link" and attrs.get("rel")=="stylesheet":
            self.paths["css"]=attrs.get("href")


def health(session,url,fixed):
    html=read(session,url+"/",limit=256_000).decode()
    assets=Assets()
    assets.feed(html)
    if set(assets.paths)!={"css","js"} or 'id="root"' not in html:
        raise ValueError("invalid_application_shell")
    for kind,path in assets.paths.items():
        dest=urljoin(url+"/",path)
        if urlsplit(dest).netloc!=urlsplit(url).netloc or not urlsplit(dest).path.endswith("."+kind):
            raise ValueError("unexpected_asset")
        prefix=read(session,dest,limit=2_000_000)[:100].lstrip()
        if not prefix or prefix.startswith(b"<"):
            raise ValueError("invalid_asset")
    sha=read(session,url+"/public-data-commit.txt",limit=100).decode().strip()
    if not SHA.fullmatch(sha):
        raise ValueError("missing_public_commit")
    round_ids=json.loads(read(session,url+"/analysis/current.json",limit=8192))["fixture_ids"]
    fid=round_ids[0] if round_ids else fixed
    if not FID.fullmatch(fid):
        raise ValueError("invalid_fixture_id")
    facts=json.loads(read(session,url+"/analysis/facts/"+fid+".json",limit=96_000))
    projected={k:facts[k] for k in ("schema","fixture_id","revision","source","facts")}
    if (facts["fixture_id"]!=fid or facts["schema"]!="epl-analysis-facts/v1" or facts["source"]=="schedule"
            or hashlib.sha256(canonical(projected)).hexdigest()!=facts["fact_hash"]
            or not any(f["id"]=="probabilities" for f in facts["facts"])
            or any(f["stage"]!="prematch" for f in facts["facts"])):
        raise ValueError("invalid_prediction_packet")
    # Range avoids downloading the whole public archive 6,048 times each week.
    with session.get(url+"/snapshot.json",headers={"Range":"bytes=0-511"},timeout=(4,12),allow_redirects=False,stream=True) as r:
        if r.status_code not in (200,206) or "json" not in r.headers.get("Content-Type","").lower():
            raise ValueError("download_unavailable")
        prefix=next(r.iter_content(512),b"").lstrip()
        if not prefix.startswith(b'{"data":'):
            raise ValueError("download_not_a_snapshot")
    if read(session,url+"/public-data-commit.txt",limit=100).decode().strip()!=sha:
        raise ValueError("batch_changed_during_probe")
    return {"sha":sha,"fixture_id":fid,"fact_hash":facts["fact_hash"],"sha_seen_at":stamp().isoformat()}


def live_ai(session,url,row):
    payload={"question":"这场的赛前概率和主要依据是什么？","fixture_id":row["fixture_id"],"fact_hash":row["fact_hash"]}
    with session.post(url+"/api/analysis/ask",json=payload,timeout=(4,25),allow_redirects=False) as r:
        r.raise_for_status()
        data=r.json()
    valid=(data.get("source")=="deepseek" and data.get("fixture_id")==row["fixture_id"] and data.get("fact_hash")==row["fact_hash"]
           and data.get("model")=="deepseek-flash" and bool(data.get("sections")) and data.get("view")=="prematch")
    return "deepseek" if valid else "fallback"


def open_db(path):
    conn=sqlite3.connect(path,timeout=10)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS claims(network TEXT,site TEXT,utc_day TEXT,slot INTEGER,PRIMARY KEY(network,site,utc_day));
        CREATE TABLE IF NOT EXISTS slot_claims(network TEXT,site TEXT,slot INTEGER,PRIMARY KEY(network,site,slot));
        CREATE TABLE IF NOT EXISTS samples(network TEXT,site TEXT,slot INTEGER,body TEXT,signature TEXT,PRIMARY KEY(network,site,slot));
    """)
    return conn


def sample(db,net,site,fixed,do_browser=False):
    key=key_for(net)
    started=stamp()
    slot=int(started.timestamp())//SLOT
    with db:
        if db.execute("SELECT 1 FROM samples WHERE network=? AND site=? AND slot=?",(net,site,slot)).fetchone():
            return
        # A crashed probe leaves a missing observation. A retry must not replace a failed slot.
        if not db.execute("INSERT OR IGNORE INTO slot_claims VALUES(?,?,?)",(net,site,slot)).rowcount:
            return
    row={"network":net,"site":site,"at":started.isoformat(),"slot":slot,"asns":[],"ok":False,"ai":None,"browser_at":None}
    with requests.Session() as session:
        session.trust_env=False
        try:
            row["asns"]=network(session,site,net)
            row.update(health(session,site,fixed))
            row["ok"]=True
        except (OSError,ValueError,KeyError,StopIteration) as exc:
            row["error"]=str(exc) if type(exc) is ValueError and re.fullmatch(r"[a-z_]+",str(exc)) else type(exc).__name__
        if row["ok"]:
            day=started.date().isoformat()
            with db:
                first=db.execute("INSERT OR IGNORE INTO claims VALUES(?,?,?,?)",(net,site,day,slot)).rowcount==1
            if first and not do_browser:
                try:
                    row["ai"]=live_ai(session,site,row)
                except (OSError,ValueError,KeyError):
                    row["ai"]="failed"
            if first and do_browser:
                try:
                    run=subprocess.run(["node",str(ROOT/"scripts"/"monitor_browser.cjs"),site,
                                        row["fixture_id"],row["fact_hash"],str(row["asns"][0])],
                                       timeout=120,check=True,capture_output=True)
                    result=json.loads(run.stdout)
                    row["ai"]=result["ai"] if result["ai"] in ("deepseek","fallback") else "failed"
                    if result["completed"]:
                        row["browser_at"]=stamp().isoformat()
                    else:
                        row["browser_error"]=True
                except (OSError,subprocess.SubprocessError,ValueError,KeyError):
                    row["ai"]="failed"
                    row["browser_error"]=True
    sig=hmac.new(key,canonical(row),hashlib.sha256).hexdigest()
    with db:
        db.execute("INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?)",(net,site,slot,canonical(row).decode(),sig))
    return row


def summarize(records,site,end,keys,manifest):
    start=end-timedelta(days=7)
    if end.time().isoformat()!="00:00:00" or end+timedelta(minutes=10)>stamp():
        raise ValueError("Use a completed UTC midnight with ten minutes for end-of-window publication checks")
    first,last=(int(t.timestamp())//SLOT for t in (start,end))
    by_net={n:{} for n in NETWORKS}
    for raw,sig in records:
        row=json.loads(raw)
        net,slot=row["network"],row["slot"]
        if (row["site"]!=site or net not in keys or not first<=slot<last+2 or int(instant(row["at"]).timestamp())//SLOT!=slot
                or not hmac.compare_digest(hmac.new(keys[net],canonical(row),hashlib.sha256).hexdigest(),sig)):
            continue
        existing=by_net[net].get(slot)
        if existing is not None and existing!=row:
            raise ValueError("Conflicting observations for the same probe and time slot")
        by_net[net][slot]=row
    coverage=bool(manifest and manifest.get("schema")=="epl-upload-times/v1" and
                  manifest.get("from_utc") and manifest.get("through_utc") and
                  instant(manifest["from_utc"])<=start and instant(manifest["through_utc"])>=end)
    uploads=[]
    if coverage:
        seen=set()
        for u in manifest["uploads"]:
            t=instant(u["uploaded_at"])
            if not SHA.fullmatch(u["sha"]) or u["sha"] in seen:
                raise ValueError("Invalid or duplicate collector upload receipt")
            seen.add(u["sha"])
            if start<=t<end:
                uploads.append((u["sha"],t))
    summary={}
    for net,items in by_net.items():
        observed=[r for r in items.values() if r["asns"] and set(r["asns"]).issubset(NETWORKS[net])]
        verified=[r for r in observed if r["slot"]<last]
        good=[r for r in verified if r["ok"]]
        ai_days={r["at"][:10] for r in good if r["ai"]=="deepseek"}
        browser=max((r["browser_at"] for r in good if r.get("browser_at")),default=None)
        ai_counts={outcome:sum(r["ai"]==outcome for r in verified) for outcome in ("deepseek","fallback","failed")}
        attempts=sum(ai_counts.values())
        delays={}
        for sha,uploaded in uploads:
            found=[instant(r["sha_seen_at"]) for r in observed if r["ok"] and r.get("sha")==sha and instant(r["sha_seen_at"])>=uploaded]
            delays[sha]=(min(found)-uploaded).total_seconds() if found else None
        sha_ok=bool(coverage and uploads and all(d is not None and d<=600 for d in delays.values()))
        recorded=sum(slot<last for slot in items)
        summary[net]={"expected_slots":SLOTS,"recorded_slots":recorded,"missing_slots":SLOTS-recorded,
                      "verified_slots":len(verified),"healthy_slots":len(good),"availability":len(good)/SLOTS,
                      "unverified_slots":recorded-len(verified),"failed_core_slots":len(verified)-len(good),
                      "ai_days":sorted(ai_days),"ai_attempts":attempts,"ai_outcomes":ai_counts,
                      "ai_error_rate":(attempts-ai_counts["deepseek"])/attempts if attempts else None,
                      "browser_at":browser,"upload_delay_seconds":delays,
                      "passed":len(good)/SLOTS>=.995 and len(ai_days)==7 and bool(browser) and sha_ok}
    return {"from_utc":start.isoformat(),"through_utc":end.isoformat(),"upload_manifest_verified_range":coverage,
            "carriers":summary,"passed":all(c["passed"] for c in summary.values())}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest="action",required=True)
    run=subs.add_parser("sample")
    run.add_argument("--network",choices=tuple(NETWORKS),required=True)
    run.add_argument("--db",type=Path,required=True)
    run.add_argument("--origin",required=True,type=origin)
    run.add_argument("--fallback-fixture",default="2026-27_fulham_manchester-united")
    run.add_argument("--browser",action="store_true",help="Run the genuine browser workflow once per UTC day on this connection")
    report=subs.add_parser("report")
    report.add_argument("--db",type=Path,nargs="+",required=True)
    report.add_argument("--origin",required=True,type=origin)
    report.add_argument("--end",required=True,help="Exclusive UTC date YYYY-MM-DD")
    report.add_argument("--uploads",type=Path)
    report.add_argument("--require-pass",action="store_true",help="Exit with status 1 when any carrier fails")
    args=parser.parse_args()
    if args.action=="sample":
        with closing(open_db(args.db)) as conn:
            result=sample(conn,args.network,args.origin,args.fallback_fixture,args.browser)
    else:
        rows=[]
        for path in args.db:
            with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)) as conn:
                rows.extend(conn.execute("SELECT body,signature FROM samples"))
        manifest=json.loads(args.uploads.read_text("utf-8-sig")) if args.uploads else None
        end=instant(args.end+"T00:00:00Z")
        result=summarize(rows,args.origin,end,{net:key_for(net) for net in NETWORKS},manifest)
    if result is not None:
        print(json.dumps(result,ensure_ascii=False))
    if args.action=="report" and args.require_pass and not result["passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
