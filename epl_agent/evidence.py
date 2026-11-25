"""Opt-in public timestamp witness using a dedicated issue in a public GitHub repo."""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import requests

from .market import canonical, database, digest, instant, read_value, write_value

_COMMENT = re.compile(r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)/issues/([1-9]\d*)#issuecomment-([1-9]\d*)\Z")
SUMMARY_KEYS = ("id","season","kickoff","cutoff","frozen","a","market","b","model_version")
MAINTAINER = {"OWNER","MEMBER","COLLABORATOR"}


def comment_api(url):
    """Build only the GitHub REST issue-comment endpoint; never follow caller-supplied URLs."""
    m = _COMMENT.fullmatch(url) if isinstance(url,str) else None
    if not m or m[2] in (".",".."):
        raise ValueError("Invalid public GitHub issue comment URL")
    return f"https://api.github.com/repos/{m[1]}/{m[2]}/issues/comments/{m[4]}"


def _summary(payload):
    return {key:payload[key] for key in SUMMARY_KEYS}


def audit_public(ledger, session=None, *, expected_issue=None, as_of=None):
    """Re-read registered hashes and forecast summaries from public, unedited comments."""
    client = session or requests.Session()
    headers = {"Accept":"application/vnd.github+json"}
    if session is None:
        client.trust_env = False  # Never silently send .netrc or proxy credentials.
        token = os.getenv("EPL_AUDIT_GITHUB_TOKEN")
        if token:
            headers["Authorization"] = "Bearer "+token
    cache, public_repos, errors, verified = {}, {}, [], {}
    inventory = None

    def read_inventory():
        if not _COMMENT.fullmatch(expected_issue+"#issuecomment-1"):
            raise ValueError("Invalid configured evidence issue")
        api = comment_api(expected_issue+"#issuecomment-1").split("/issues/comments/")[0]
        response = client.get(api,headers=headers,timeout=12,allow_redirects=False)
        if response.status_code != 200 or response.json().get("private") is not False:
            raise ValueError("Configured witness repository is not publicly readable")
        url = api+"/issues/"+expected_issue.rsplit("/",1)[1]+"/comments"
        docs = {}
        page = 1
        while True:
            resp = client.get(url,params={"per_page":100,"page":page},headers=headers,timeout=20,allow_redirects=False)
            if resp.status_code != 200:
                raise ValueError(f"Evidence inventory returned HTTP {resp.status_code}")
            items = resp.json()
            if not isinstance(items,list):
                raise ValueError("Invalid evidence inventory")
            for c in items:
                if c["html_url"].split("#")[0]!=expected_issue:
                    raise ValueError("Evidence from another issue")
                if as_of is None or instant(c["created_at"])<=instant(as_of):
                    docs[c["html_url"]] = c
            if len(items)<100:
                return docs
            page += 1

    def verify(url, at, header, text):
        api_url = comment_api(url)
        if url not in cache:
            try:
                if inventory is None and "Authorization" in headers:
                    repo_url = api_url.split("/issues/comments/")[0]
                    if repo_url not in public_repos:
                        visibility = client.get(repo_url,headers=headers,timeout=8,allow_redirects=False)
                        public_repos[repo_url] = visibility.status_code == 200 and visibility.json().get("private") is False
                    if not public_repos[repo_url]:
                        raise ValueError("Repository is private or no longer accessible")
                if inventory is not None:
                    if url not in inventory:
                        raise ValueError("Evidence missing from configured issue at snapshot time")
                    comment = inventory[url]
                else:
                    resp = client.get(api_url,headers=headers,timeout=8,allow_redirects=False)
                    if resp.status_code != 200:
                        raise ValueError(f"GitHub returned HTTP {resp.status_code}")
                    comment = resp.json()
                if (comment["html_url"] != url or instant(comment["created_at"]) != instant(comment["updated_at"])
                        or not isinstance(comment["body"], str)):
                    raise ValueError("Comment was changed or its URL does not match")
                lines = comment["body"].splitlines()
                if lines[:2] != ["EPL T-24h · SHA-256",""] or (len(lines)-2)%2:
                    raise ValueError("Unrecognized evidence comment structure")
                cache[url] = (instant(comment["created_at"]), set(zip(lines[2::2],lines[3::2])))
            except (requests.RequestException,KeyError,TypeError,ValueError) as exc:
                cache[url] = str(exc)
        result = cache[url]
        if isinstance(result,str):
            raise ValueError(result)
        if result[0] != instant(at) or (header,text) not in result[1]:
            raise ValueError("Timestamp, hash or accompanying public payload differs")

    reg = ledger.get("registration")
    reg_evidence = ledger.get("registration_evidence") or {}
    reg_digest = digest(reg)
    reg_ok = False
    try:
        try:
            if expected_issue:
                inventory = read_inventory()
                if not reg_evidence and not any(r.get("evidence") for r in ledger.get("records",[])) and not any(
                        c.get("body","").startswith("EPL T-24h · SHA-256") for c in inventory.values()):
                    return {"ok":True,"registration_ok":False,"forecasts":{},"verified":0,
                            "checked_at":datetime.now(timezone.utc).isoformat(),"errors":[]}
            verify(reg_evidence.get("url"),reg_evidence.get("at"),"protocol "+reg_digest,canonical(reg))
            if inventory is not None and inventory[reg_evidence["url"]].get("author_association") not in MAINTAINER:
                raise ValueError("Protocol witness must be published by a repository maintainer")
            if not reg:
                raise ValueError("Missing registration")
            reg_ok = True
        except (ValueError,TypeError,KeyError,requests.RequestException) as exc:
            errors.append({"kind":"registration","error":str(exc)})
            if expected_issue:
                return {"ok":False,"registration_ok":False,"forecasts":{},"verified":0,"errors":errors}
        for row in ledger.get("records",[]):
            if not row.get("payload") or not row.get("evidence"):
                continue
            p = row["payload"]
            try:
                if digest(p) != row["hash"] or p["protocol_hash"] != reg_digest:
                    raise ValueError("Forecast or registration digest mismatch")
                if row["evidence"].split("#")[0] != reg_evidence["url"].split("#")[0]:
                    raise ValueError("Forecast witness is in a different issue")
                verify(row["evidence"],row["witnessed"],"forecast "+row["hash"],canonical(_summary(p)))
                if inventory is not None and inventory[row["evidence"]]["user"]["id"]!=inventory[reg_evidence["url"]]["user"]["id"]:
                    raise ValueError("Forecast published by a different registration identity")
                verified[row["hash"]] = {"url":row["evidence"],"at":row["witnessed"]}
            except (ValueError,KeyError,TypeError,AttributeError) as exc:
                errors.append({"kind":"forecast","id":row.get("id"),"error":str(exc)})
        if inventory is not None and reg_ok:
            author = inventory[reg_evidence["url"]]["user"]["id"]
            announced = set()
            for c in inventory.values():
                if c["user"]["id"]!=author or not c["body"].startswith("EPL T-24h · SHA-256\n\n"):
                    continue
                announced.update(line[9:] for line in c["body"].splitlines()[2::2] if line.startswith("forecast "))
            if announced!=set(verified):
                errors.append({"kind":"inventory","error":"Published forecast hashes do not match the snapshot"})
    finally:
        if session is None:
            client.close()
    return {"ok":reg_ok and not errors,"checked_at":datetime.now(timezone.utc).isoformat(),
            "registration_ok":reg_ok,"registration_hash":reg_digest,"registration_evidence":reg_evidence,
            "verified":len(verified),"forecasts":verified,"errors":errors,"comments_fetched":len(cache)}


def store_collector_audit(root, ledger, report, as_of):
    """Apply the audit only to the collector registration and witness it examined."""
    registration = ledger.get("registration")
    witness = ledger.get("registration_evidence")
    registration_hash = digest(registration)
    if report.get("registration_ok") and (
            report.get("registration_hash")!=registration_hash or report.get("registration_evidence")!=witness):
        return False
    cutoff = instant(as_of)
    with database(root) as db:
        db.execute("BEGIN IMMEDIATE")
        if digest(read_value(db,"registration"))!=registration_hash or read_value(db,"registration_evidence")!=witness:
            return False
        previous = read_value(db,"public_audit") or {}
        if previous.get("registration_hash")==registration_hash and previous.get("registration_evidence")==witness:
            # v0.1 audit entries only recorded their completion time.
            previous_cutoff = previous.get("as_of") or previous.get("checked_at")
            if previous_cutoff and instant(previous_cutoff)>=cutoff:
                return False
        write_value(db,"public_audit",dict(report,as_of=cutoff.isoformat(),registration_hash=registration_hash,
                                          registration_evidence=witness))
    return True


def audit_and_store(root=None, session=None):
    from .evaluation import public_ledger
    from .snapshot import expected_issue
    ledger = public_ledger(root)
    cutoff = datetime.now(timezone.utc)
    report = audit_public(ledger,session=session,expected_issue=expected_issue(),as_of=cutoff)
    store_collector_audit(root,ledger,report,cutoff)
    return report


def _claim_witness(root, issue_url, now):
    # Keep the exact body across timeouts and restarts. Never hold the collector's
    # SQLite write lock while talking to GitHub.
    with database(root) as db:
        db.execute("BEGIN IMMEDIATE")
        reg = read_value(db, "registration")
        if not reg:
            raise ValueError("Register a model first.")
        witness = read_value(db, "registration_evidence")
        if witness and witness["url"].split("#")[0] != issue_url:
            raise ValueError("Configured issue differs from the registration witness")
        batch = read_value(db, "witness_outbox")
        recovery = bool(batch)
        if batch:
            if batch["issue"] != issue_url or batch["protocol"] != digest(reg):
                raise ValueError("Pending witness belongs to another issue or registration")
            if instant(batch["lease_until"]) > now:
                return None, False
        else:
            rows = db.execute("""SELECT hash,payload FROM captures
                WHERE hash IS NOT NULL AND evidence IS NULL
                ORDER BY CASE WHEN julianday(kickoff)-1 >= julianday(?) THEN 0 ELSE 1 END,
                         julianday(kickoff), fixture LIMIT 40""", (now.isoformat(),)).fetchall()
            if witness and not rows:
                return None, False
            lines = [] if witness else ["protocol "+digest(reg), canonical(reg)]
            for row in rows:
                lines.extend(["forecast "+row["hash"], canonical(_summary(json.loads(row["payload"])))])
            batch = {"issue":issue_url, "protocol":digest(reg), "registration":not witness,
                     "hashes":[row["hash"] for row in rows], "started":now.isoformat(),
                     "body":"EPL T-24h · SHA-256\n\n" + "\n".join(lines)}
        batch["claim"] = uuid4().hex
        batch["lease_until"] = (now+timedelta(seconds=60)).isoformat()
        write_value(db, "witness_outbox", batch)
        return batch, recovery


def _recover_witness(client, headers, comments_url, batch):
    # Only an unedited comment from this authenticated publisher may be adopted.
    identity = client.get("https://api.github.com/user", headers=headers, timeout=8, allow_redirects=False)
    identity.raise_for_status()
    author = identity.json()["id"]
    since = (instant(batch["started"])-timedelta(minutes=5)).isoformat()
    page = 1
    while True:
        response = client.get(comments_url, headers=headers, timeout=10, allow_redirects=False,
                              params={"since":since, "per_page":100, "page":page})
        response.raise_for_status()
        items = response.json()
        if not isinstance(items,list):
            raise ValueError("Unrecognized issue comment inventory")
        for c in items:
            if (c.get("user",{}).get("id")==author and c.get("body")==batch["body"]
                    and c.get("created_at")==c.get("updated_at")):
                return c
        if len(items)<100:
            return None
        page += 1


def publish(root=None, *, session=None, clock=lambda:datetime.now(timezone.utc)):
    repo = os.environ.get("EPL_EVIDENCE_REPO","")
    issue = os.environ.get("EPL_EVIDENCE_ISSUE","")
    token = os.environ.get("EPL_GITHUB_TOKEN")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repo) or not issue.isdecimal() or not token:
        raise ValueError("Set EPL_EVIDENCE_REPO, EPL_EVIDENCE_ISSUE and EPL_GITHUB_TOKEN for public witnessing.")
    issue_url = f"https://github.com/{repo}/issues/{issue}"
    batch, recovery = _claim_witness(root, issue_url, instant(clock()))
    if not batch:
        return {"published":0}
    client = session or requests.Session()
    if session is None:
        client.trust_env = False
    headers = {"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json"}
    base = "https://api.github.com/repos/"+repo
    comments_url = f"{base}/issues/{issue}/comments"
    try:
        check = client.get(base,timeout=10,headers=headers,allow_redirects=False)
        check.raise_for_status()
        if check.json().get("private") is not False:
            raise ValueError("Evidence must be publicly readable.")
        comment = _recover_witness(client, headers, comments_url, batch) if recovery else None
        if comment is None:
            # Recovery can take several pages. A second process may have taken
            # the lease while this one was comparing the public inventory.
            with database(root) as db:
                db.execute("BEGIN IMMEDIATE")
                active = read_value(db,"witness_outbox") or {}
                if active.get("claim") != batch["claim"]:
                    return {"published":0,"status":"claim_changed"}
                active["lease_until"] = (instant(clock())+timedelta(seconds=60)).isoformat()
                write_value(db,"witness_outbox",active)
            response = client.post(comments_url,headers=headers,json={"body":batch["body"]},timeout=15)
            response.raise_for_status()
            comment = response.json()
        if (comment["body"] != batch["body"] or comment["created_at"] != comment["updated_at"]
                or comment["html_url"].split("#")[0] != issue_url):
            raise ValueError("The public witness does not match the submitted hashes.")
        comment_api(comment["html_url"])
        stamp = instant(comment["created_at"]).isoformat()
        with database(root) as db:
            db.execute("BEGIN IMMEDIATE")
            claim = read_value(db,"witness_outbox") or {}
            if claim.get("claim") != batch["claim"]:
                return {"published":0,"status":"claim_changed"}
            if batch["registration"]:
                write_value(db,"registration_evidence",{"url":comment["html_url"],"at":stamp})
            db.executemany("UPDATE captures SET evidence=?,witnessed=? WHERE hash=? AND evidence IS NULL",
                           ((comment["html_url"],stamp,h) for h in batch["hashes"]))
            db.execute("DELETE FROM state WHERE key='witness_outbox'")
        return {"published":len(batch["hashes"]),"url":comment["html_url"],"at":stamp}
    except Exception:
        with database(root) as db:
            db.execute("BEGIN IMMEDIATE")
            claim = read_value(db,"witness_outbox") or {}
            if claim.get("claim") == batch["claim"]:
                claim["lease_until"] = instant(clock()).isoformat()
                write_value(db,"witness_outbox",claim)
        raise
    finally:
        if session is None:
            client.close()
