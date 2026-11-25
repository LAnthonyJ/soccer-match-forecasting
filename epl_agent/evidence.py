"""Opt-in public timestamp witness using a dedicated issue in a public GitHub repo."""
import json
import os
import re
from datetime import datetime, timezone

import requests

from .market import canonical, database, digest, instant, read_value, write_value

_COMMENT = re.compile(r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)/issues/([1-9]\d*)#issuecomment-([1-9]\d*)\Z")
SUMMARY_KEYS = ("id","season","kickoff","cutoff","frozen","a","market","b","model_version")


def comment_api(url):
    """Build only the GitHub REST issue-comment endpoint; never follow caller-supplied URLs."""
    m = _COMMENT.fullmatch(url) if isinstance(url,str) else None
    if not m or m[2] in (".",".."):
        raise ValueError("Invalid public GitHub issue comment URL")
    return f"https://api.github.com/repos/{m[1]}/{m[2]}/issues/comments/{m[4]}"


def _summary(payload):
    return {key:payload[key] for key in SUMMARY_KEYS}


def audit_public(ledger, session=None):
    """Re-read registered hashes and forecast summaries from public, unedited comments."""
    client = session or requests.Session()
    headers = {"Accept":"application/vnd.github+json"}
    if session is None:
        client.trust_env = False  # Never silently send .netrc or proxy credentials.
        token = os.getenv("EPL_AUDIT_GITHUB_TOKEN")
        if token:
            headers["Authorization"] = "Bearer "+token
    cache, public_repos, errors, verified = {}, {}, [], {}

    def verify(url, at, header, text):
        api_url = comment_api(url)
        if url not in cache:
            try:
                if "Authorization" in headers:
                    repo_url = api_url.split("/issues/comments/")[0]
                    if repo_url not in public_repos:
                        visibility = client.get(repo_url,headers=headers,timeout=8,allow_redirects=False)
                        public_repos[repo_url] = visibility.status_code == 200 and visibility.json().get("private") is False
                    if not public_repos[repo_url]:
                        raise ValueError("Repository is private or no longer accessible")
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
            verify(reg_evidence.get("url"),reg_evidence.get("at"),"protocol "+reg_digest,canonical(reg))
            if not reg:
                raise ValueError("Missing registration")
            reg_ok = True
        except (ValueError,TypeError) as exc:
            errors.append({"kind":"registration","error":str(exc)})
        for row in ledger.get("records",[]):
            if not row.get("payload"):
                continue
            p = row["payload"]
            try:
                if digest(p) != row["hash"] or p["protocol_hash"] != reg_digest:
                    raise ValueError("Forecast or registration digest mismatch")
                if row["evidence"].split("#")[0] != reg_evidence["url"].split("#")[0]:
                    raise ValueError("Forecast witness is in a different issue")
                verify(row["evidence"],row["witnessed"],"forecast "+row["hash"],canonical(_summary(p)))
                verified[row["hash"]] = {"url":row["evidence"],"at":row["witnessed"]}
            except (ValueError,KeyError,TypeError,AttributeError) as exc:
                errors.append({"kind":"forecast","id":row.get("id"),"error":str(exc)})
    finally:
        if session is None:
            client.close()
    return {"ok":reg_ok and not errors,"checked_at":datetime.now(timezone.utc).isoformat(),
            "registration_ok":reg_ok,"registration_hash":reg_digest,"registration_evidence":reg_evidence,
            "verified":len(verified),"forecasts":verified,"errors":errors,"comments_fetched":len(cache)}


def audit_and_store(root=None, session=None):
    from .evaluation import public_ledger
    report = audit_public(public_ledger(root),session=session)
    with database(root) as db:
        write_value(db,"public_audit",report)
    return report


def publish(root=None, *, session=None):
    repo = os.environ.get("EPL_EVIDENCE_REPO","")
    issue = os.environ.get("EPL_EVIDENCE_ISSUE","")
    token = os.environ.get("EPL_GITHUB_TOKEN")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repo) or not issue.isdecimal() or not token:
        raise ValueError("Set EPL_EVIDENCE_REPO, EPL_EVIDENCE_ISSUE and EPL_GITHUB_TOKEN for public witnessing.")
    client = session or requests.Session()
    headers = {"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json"}
    base = "https://api.github.com/repos/"+repo
    check = client.get(base,timeout=10,headers=headers)
    check.raise_for_status()
    if check.json().get("private") is not False:
        raise ValueError("Evidence must be publicly readable.")
    with database(root) as db:
        db.execute("BEGIN IMMEDIATE")
        reg = read_value(db,"registration")
        if not reg:
            raise ValueError("Register a model first.")
        rows = list(db.execute("SELECT hash,payload FROM captures WHERE hash IS NOT NULL AND evidence IS NULL ORDER BY hash LIMIT 40"))
        registration = not read_value(db,"registration_evidence")
        if not rows and not registration:
            return {"published":0}
        lines = ["protocol "+digest(reg), canonical(reg)] if registration else []
        for row in rows:
            p = json.loads(row["payload"])
            lines.extend(["forecast "+row["hash"],canonical(_summary(p))])
        body = "EPL T-24h · SHA-256\n\n" + "\n".join(lines)
        response = client.post(f"{base}/issues/{issue}/comments",headers=headers,json={"body":body},timeout=15)
        response.raise_for_status()
        comment = response.json()
        stamp = instant(comment["created_at"]).isoformat()
        if comment["body"] != body or comment["created_at"] != comment["updated_at"]:
            raise ValueError("The public witness does not match the submitted hashes.")
        if registration:
            write_value(db,"registration_evidence",{"url":comment["html_url"],"at":stamp})
        db.executemany("UPDATE captures SET evidence=?,witnessed=? WHERE hash=?",
                       ((comment["html_url"],stamp,r[0]) for r in rows))
        return {"published":len(rows),"url":comment["html_url"],"at":stamp}
