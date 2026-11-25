"""Portable, independently audited public inputs for the EPL scorecard."""
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import requests

from . import data, schedule
from .evidence import audit_public, store_collector_audit
from .evaluation import formal_report, public_ledger
from .market import PROTOCOL, canonical, digest, instant, root_dir

LEGACY_FORMAT = "public-snapshot/v1"
OPERATIONS_FORMAT = "public-snapshot/v2"
FORMAT = "public-snapshot/v3"
NAME = "snapshot.json"
BRANCH = "public-data"
UTC = timezone.utc
FIXTURE_KEYS = set(data.FIELDS) | set(
    "source_commit observed_at schedule_observed_at result_observed_at schedule_revision revised_fields "
    "withdrawn source_conflict result_source schedule_source schedule_status source_observations resolution".split())
ROW_KEYS = "id kickoff revision observed reason hash payload evidence witnessed"
PAYLOAD_KEYS = "id season kickoff cutoff frozen model_version protocol_hash schedule_observed_at schedule_revision a market b prediction observed"
PRED_KEYS = ("probs elo_probs dc_probs tree_probs xg top_scores samples form form_window model_version "
             "cutoff trained_through historical_reconstruction source_fixture_digest")
PRIVATE_TEXT = re.compile(r"(?:github_pat_[\w]{20,}|gh[pousr]_[\w]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|[?&](?:apiKey|token)=[^&\s]+)",re.I)


def _only(value, fields):
    allowed = set(fields.split()) if isinstance(fields,str) else fields
    if not isinstance(value,dict) or value.keys()-allowed:
        raise ValueError("Unexpected fields in public snapshot")


def _scalars(value):
    if isinstance(value,dict) or (isinstance(value,list) and any(isinstance(x,(list,dict)) for x in value)):
        raise ValueError("Unexpected nested data in public snapshot")


def _flat(value, fields):
    _only(value,fields)
    for v in value.values():
        _scalars(v)


def _fixture_shape(row, version):
    _only(row,FIXTURE_KEYS | ({"schedule_review"} if version == FORMAT else set()))
    for key,value in row.items():
        if key=="source_observations":
            _only(value,"openfootball openligadb")
            for ref in value.values():
                _flat(ref,"fixture_id observed_at revision date kickoff_utc hg ag")
        elif key=="schedule_review":
            _flat(value,"provider observed_at evidence reference fingerprint")
        elif key=="resolution":
            _flat(value,"provider observed_at evidence note")
        else:
            _scalars(value)
    if version == FORMAT and "schedule_status" in row and not row.get("withdrawn"):
        refs = row.get("source_observations") or {}
        if any(ref.get("observed_at") and instant(ref["observed_at"]) > instant(row["observed_at"])
               for ref in refs.values()):
            raise ValueError("A kickoff candidate arrived after its fixture revision")
        candidate = {"id":row["id"], "source_observations":refs}
        expected = schedule.select(candidate, data._utc(row["observed_at"]))
        fields = ["schedule_status", "schedule_source", "kickoff_utc", "schedule_review"]
        if expected.get("kickoff_utc"):
            fields.append("date")
            if not row.get("schedule_observed_at") or (row.get("schedule_review") and
                    instant(row["schedule_observed_at"]) < instant(row["schedule_review"]["observed_at"])):
                raise ValueError("Invalid verified kickoff receipt")
        if any(row.get(k) != expected.get(k) for k in fields):
            raise ValueError("Schedule decision disagrees with the exact source candidates")


def _record_shape(row, version):
    fields = ROW_KEYS if version == LEGACY_FORMAT else ROW_KEYS + " freeze_reason market_reason"
    _only(row,fields)
    for key,value in row.items():
        if key!="payload":
            _scalars(value)
    p = row["payload"]
    if p is None:
        if row.get("hash") or row.get("evidence"):
            raise ValueError("Missing witnessed payload")
        return
    _only(p,PAYLOAD_KEYS)
    for key,value in p.items():
        if key!="prediction":
            _scalars(value)
    pred = p["prediction"]
    _only(pred,PRED_KEYS)
    for key,value in pred.items():
        if key=="form":
            _only(value,"home away")
            for side in value.values():
                _flat(side,"gf ga points")
        elif key=="samples":
            _flat(value,"home away")
        elif key=="form_window":
            _flat(value,"max_games prior_games home_games away_games")
        elif key=="top_scores":
            for score in value:
                _flat(score,"home away prob")
        else:
            _scalars(value)
    if digest(p)!=row["hash"]:
        raise ValueError("Frozen prediction digest mismatch")


def _operations_shape(batch, as_of):
    ops = batch.get("operations", {})
    _only(ops,"heartbeat_at upload_sha upload_at deployed_sha deployed_at publication_status checked_at sync_status")
    for key,value in ops.items():
        if value is None:
            continue
        if key.endswith("_at"):
            if instant(value)>as_of:
                raise ValueError("Collector status is newer than the snapshot")
        elif key.endswith("_sha"):
            if not isinstance(value,str) or not re.fullmatch(r"[a-f0-9]{40}",value):
                raise ValueError("Invalid public commit SHA")
        elif not isinstance(value,str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}",value):
            raise ValueError("Invalid collector status")
    deployments = batch.get("deployments", [])
    if not isinstance(deployments,list):
        raise ValueError("Invalid Pages deployment receipts")
    seen = set()
    for receipt in deployments:
        _only(receipt,"id kickoff revision hash result_observed_at seen_at sha")
        key = (receipt["id"],receipt["kickoff"],receipt["revision"],receipt.get("hash"),receipt.get("result_observed_at"))
        if (not isinstance(receipt["id"],str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,180}",receipt["id"])
                or type(receipt["revision"]) is not int or receipt["revision"]<0
                or not isinstance(receipt["sha"],str) or not re.fullmatch(r"[a-f0-9]{40}",receipt["sha"])
                or (receipt.get("hash") is not None and not re.fullmatch(r"[a-f0-9]{64}",receipt["hash"]))
                or instant(receipt["seen_at"])>as_of
                or (receipt.get("result_observed_at") and instant(receipt["result_observed_at"])>instant(receipt["seen_at"]))
                or key in seen):
            raise ValueError("Invalid Pages deployment receipt")
        instant(receipt["kickoff"])
        seen.add(key)


def _validate(batch, version=FORMAT):
    extra = "" if version == LEGACY_FORMAT else " operations deployments"
    _only(batch,"as_of fixtures versions overlay ledger seasons_health"+extra)
    as_of = instant(batch["as_of"])
    if as_of>datetime.now(UTC):
        raise ValueError("Snapshot time is in the future")
    if version != LEGACY_FORMAT:
        _operations_shape(batch,as_of)
    ledger = batch["ledger"]
    _only(ledger,"registration registration_evidence health records")
    reg = ledger["registration"]
    if reg is not None:
        _only(reg,"protocol model created seasons")
        if digest(reg["protocol"])!=digest(PROTOCOL):
            raise ValueError("Unknown registered protocol")
        for key in ("model","created","seasons"):
            _scalars(reg[key])
        created = instant(reg["created"])
        year = created.year + int(created.month>=7)
        if created>as_of or reg["seasons"]!=[f"{y}-{str(y+1)[-2:]}" for y in (year,year+1)]:
            raise ValueError("Invalid frozen evaluation seasons or registration time")
    if ledger["health"]:
        _flat(ledger["health"],"at status fixtures")
    if ledger["registration_evidence"]:
        _flat(ledger["registration_evidence"],"at url")
    for r in ledger["records"]:
        _record_shape(r,version)
        if any(instant(stamp)>as_of for stamp in (r["observed"],r.get("witnessed"),(r.get("payload") or {}).get("frozen")) if stamp):
            raise ValueError("A ledger observation is newer than its snapshot")
    groups = defaultdict(list)
    latest = dict(data._base())
    previous_version = {}
    for key in ("versions","fixtures"):
        for m in batch[key]:
            _fixture_shape(m,version)
            if m["id"]!=data.fixture_id(m["season"],m["home"],m["away"]):
                raise ValueError("Fixture identity mismatch")
            if any(instant(m[t])>as_of for t in ("observed_at","result_observed_at","schedule_observed_at") if m.get(t)):
                raise ValueError("A fixture observation is newer than its snapshot")
            if key=="versions":
                seen = instant(m["observed_at"])
                prior = latest.get(m["id"]) or {}
                if version == FORMAT and m.get("schedule_status") == "verified" and not m.get("withdrawn"):
                    changed = any(prior.get(k) != m.get(k) for k in (
                        "schedule_status","date","kickoff_utc","round","schedule_source","schedule_review","withdrawn"))
                    expected = m["observed_at"] if changed else prior.get("schedule_observed_at")
                    if not expected or instant(m["schedule_observed_at"]) != instant(expected):
                        raise ValueError("Verified kickoff was backdated or detached from its revision")
                if m["id"] in previous_version and seen<=previous_version[m["id"]]:
                    raise ValueError("Duplicate or out-of-order public fixture revision")
                previous_version[m["id"]] = seen
                if m.get("withdrawn"):
                    latest.pop(m["id"],None)
                else:
                    latest[m["id"]] = m
            else:
                if latest.get(m["id"])!=m:
                    raise ValueError("Public fixture differs from its archived or observed version")
                groups[m["season"]].append(m)
    if not groups:
        raise ValueError("Empty EPL schedule")
    for s,rows in groups.items():
        d = data.coverage(rows,s)
        if d["teams"]!=20 or d["fixtures"]!=380 or d["missing"] or d["duplicates"]:
            raise ValueError("Incomplete official fixture denominator: "+s)
    if reg:
        for season in reg["seasons"]:
            if as_of>=datetime(int(season[:4]),9,1,tzinfo=UTC) and season not in groups:
                raise ValueError("Registered season omitted from fixture denominator: "+season)
    for season,health in batch["seasons_health"].items():
        if season not in groups or health.get("season")!=season:
            raise ValueError("Data health season mismatch")
        timing = " schedule_verified_count schedule_conflict_count schedule_unconfirmed_count schedule_pending_ids" if version == FORMAT else ""
        _only(health,"season last_scored_date last_sync_at completed total overdue_count overdue_ids conflict_count conflict_ids sources"+timing)
        if version == FORMAT and any("schedule_status" in m for m in groups[season]):
            expected = data.season_health(None,season,groups[season],as_of)
            for k in ("total","completed","last_scored_date","overdue_ids","overdue_count","conflict_ids",
                      "conflict_count","schedule_verified_count","schedule_conflict_count",
                      "schedule_unconfirmed_count","schedule_pending_ids"):
                if expected.get(k) != health.get(k):
                    raise ValueError("Published fixture health differs from the versioned schedule")
        for k,v in health.items():
            if k!="sources":
                _scalars(v)
        for src in health["sources"]:
            _flat(src,"provider last_success_at last_attempt_at status revision license")
    overlay = batch["overlay"]
    _only(overlay,"provider license source_url license_url records")
    for key in ("provider","license","source_url","license_url"):
        _scalars(overlay[key])
    for row in overlay["records"]:
        _flat(row,"id season round fixture_id observed_at revision home away date kickoff_utc hg ag")
    serialized = canonical(batch)
    secrets = [os.getenv(k,"") for k in ("ODDS_API_KEY","EPL_GITHUB_TOKEN","EPL_AUDIT_GITHUB_TOKEN","EPL_SNAPSHOT_GITHUB_TOKEN")]
    if PRIVATE_TEXT.search(serialized) or any(len(secret)>=16 and secret in serialized for secret in secrets):
        raise ValueError("Public snapshot contains credential-like text")
    return as_of


def expected_issue():
    repo = os.getenv("EPL_EVIDENCE_REPO","")
    number = os.getenv("EPL_EVIDENCE_ISSUE","")
    if not repo and not number:
        return None
    if not re.fullmatch(r"[a-zA-Z0-9-]+/[a-zA-Z0-9_.-]+",repo) or not re.fullmatch(r"[1-9][0-9]*",number):
        raise ValueError("Configure the canonical EPL_EVIDENCE_REPO and EPL_EVIDENCE_ISSUE")
    return f"https://github.com/{repo}/issues/{number}"


def _check(batch, issue, session=None, *, version=FORMAT):
    as_of = _validate(batch,version)
    ledger = dict(batch["ledger"])
    ledger["require_schedule_proof"] = version == FORMAT
    if version == LEGACY_FORMAT:
        # Derived only after schema validation. Reproduce v1's published paired scores on replay.
        ledger["legacy_pair_window"] = True
    if issue:
        audit = audit_public(ledger,session,expected_issue=issue,as_of=as_of)
        if not audit["ok"]:
            raise ValueError("Independent GitHub witness audit failed: "+str(audit["errors"][:1]))
        ledger["public_audit"] = audit
    elif ledger["registration_evidence"] or any(r["evidence"] for r in ledger["records"]):
        raise ValueError("Set the independent expected issue before verifying a witnessed snapshot")
    report = formal_report(batch["fixtures"],ledger,as_of,include_independent=version!=LEGACY_FORMAT)
    # Audit execution time can differ on a second machine. Scores use the batch's fixed cutoff.
    def scores(cohort):
        summary = {k:cohort[k] for k in ("n_pairs","seasons","scores","delta","ci","missing")}
        if version != LEGACY_FORMAT:
            summary["independent"] = cohort["independent"]
        return summary
    benchmark = {"formal":scores(report),"pilot":scores(report["pilot"]),"status":report["status"]}
    latest = max(batch["seasons_health"])
    return dict(batch,ledger=ledger,health=batch["seasons_health"][latest]),benchmark


def _write_atomic(path, content):
    path.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile(dir=path.parent,delete=False) as f:
        try:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        finally:
            tmp = Path(f.name)
    try:
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def export_bundle(directory, *, root=None, session=None):
    from . import operations
    root = Path(root or root_dir())
    db = root/"fixtures.sqlite3"
    ledger = public_ledger(root)
    worker_state = operations.public_state(root)
    deployments = operations.public_deployments(root)
    # Network reads below use this instant, including when the issue receives another forecast later.
    stamp = datetime.now(UTC)
    matches = data.load_matches(db,as_of=stamp)
    versions = data.load_versions(db,as_of=stamp)
    years = set(sorted({r["season"] for r in matches})[-2:])
    years.update(r["season"] for r in versions)
    years.update((ledger["registration"] or {}).get("seasons",[]))
    rows = [r for r in matches if r["season"] in years]
    batch = {"as_of":stamp.isoformat(),"fixtures":rows,"versions":versions,
              "overlay":data.openligadb_overlay(db,as_of=stamp),"operations":worker_state,"deployments":deployments,
             "ledger":{k:ledger[k] for k in ("registration","registration_evidence","records","health")},
             "seasons_health":{s:data.season_health(db,s,matches=rows,now=stamp) for s in sorted({r["season"] for r in rows})}}
    verified,scores = _check(batch,expected_issue(),session)
    envelope = {"format":FORMAT,"sha256":digest(batch),"data":batch,"scores":scores}
    _write_atomic(Path(directory)/NAME,(canonical(envelope)+"\n").encode("utf8"))
    audited = verified["ledger"].get("public_audit")
    if audited and audited.get("registration_ok"):
        store_collector_audit(root,ledger,audited,stamp)
    return {"format":FORMAT,"sha256":envelope["sha256"],"as_of":batch["as_of"],"scores":scores}


def _verify_bytes(contents, session=None):
    if len(contents)>128_000_000:
        raise ValueError("Snapshot exceeds supported size")
    envelope = json.loads(contents)
    _only(envelope,"format sha256 data scores")
    if envelope["format"] not in (FORMAT,OPERATIONS_FORMAT,LEGACY_FORMAT) or digest(envelope["data"])!=envelope["sha256"]:
        raise ValueError("Invalid public snapshot format or SHA-256")
    batch,scores = _check(envelope["data"],expected_issue(),session,version=envelope["format"])
    if canonical(envelope["scores"])!=canonical(scores):
        raise ValueError("Published and independently recomputed scores differ")
    return batch


def read_verified(directory, *, session=None):
    source = Path(directory)/NAME
    with source.open("rb") as f:
        contents = f.read(128_000_001)
    return _verify_bytes(contents,session),contents


def verify(directory, *, session=None):
    return read_verified(directory,session=session)[0]


def publish_bundle(directory, *, session=None):
    """Use a separate branch and an explicit opt-in token. A non-fast-forward loses the race safely."""
    issue = expected_issue()
    token = os.getenv("EPL_SNAPSHOT_GITHUB_TOKEN")
    if not issue or not token:
        raise ValueError("Configure the public evidence issue and EPL_SNAPSHOT_GITHUB_TOKEN to publish")
    verified,contents = read_verified(directory,session=session)
    if len(contents)>95_000_000:
        raise ValueError("Snapshot exceeds GitHub blob size")
    repo = os.environ["EPL_EVIDENCE_REPO"]
    api = f"https://api.github.com/repos/{repo}"
    headers = {"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json"}
    http = session or requests.Session()
    def call(method, endpoint, payload=None, missing=False):
        resp = http.request(method,api+endpoint,headers=headers,json=payload,timeout=45,allow_redirects=False)
        if missing and resp.status_code==404:
            return None
        if not 200<=resp.status_code<300:
            raise ValueError(f"Snapshot publication failed (GitHub HTTP {resp.status_code})")
        return resp.json()
    import base64
    def notify_pages(sha):
        branch = os.getenv("EPL_PAGES_CODE_BRANCH","")
        if not branch:
            return
        if branch!="epl-v1":
            raise ValueError("EPL_PAGES_CODE_BRANCH supports only the dedicated epl-v1 branch")
        marker = "/contents/.github/epl-public-data-sha"
        previous = call("GET",marker+"?ref=epl-v1",missing=True)
        content = base64.b64encode((sha+"\n").encode()).decode("ascii")
        if previous and base64.b64decode(previous["content"]).decode().strip()==sha:
            return
        # Do not roll the code-branch marker back if another collector just advanced public-data.
        if call("GET","/git/ref/heads/"+BRANCH)["object"]["sha"]!=sha:
            raise ValueError("A newer public-data commit is already live")
        payload = {"message":"Refresh verified EPL scorecard","content":content,"branch":branch}
        if previous:
            payload["sha"] = previous["sha"]
        call("PUT",marker,payload)
    try:
        if call("GET","").get("private") is not False:
            raise ValueError("Expected a public repository")
        head = call("GET","/git/ref/heads/"+BRANCH,missing=True)
        blob = call("POST","/git/blobs",{"content":base64.b64encode(contents).decode("ascii"),"encoding":"base64"})
        tree = call("POST","/git/trees",{"tree":[{"path":NAME,"mode":"100644","type":"blob","sha":blob["sha"]}]})
        parents = [head["object"]["sha"]] if head else []
        if parents:
            parent = call("GET","/git/commits/"+parents[0])
            if parent["tree"]["sha"]==tree["sha"]:
                notify_pages(parents[0])
                return parents[0]
            previous = re.fullmatch(r"EPL public snapshot (\S+)",parent["message"])
            if not previous or instant(previous[1])>=instant(verified["as_of"]):
                raise ValueError("New snapshot must follow the previous public-data commit")
        stamp = verified["as_of"]
        commit = call("POST","/git/commits",{"message":"EPL public snapshot "+stamp,"tree":tree["sha"],"parents":parents})
        if parents:
            call("PATCH","/git/refs/heads/"+BRANCH,{"sha":commit["sha"],"force":False})
        else:
            call("POST","/git/refs",{"ref":"refs/heads/"+BRANCH,"sha":commit["sha"]})
        notify_pages(commit["sha"])
        return commit["sha"]
    finally:
        if session is None:
            http.close()
