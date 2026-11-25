"""Paired prospective scoring, using only externally witnessed forecasts."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math

import numpy as np

from .market import PROTOCOL, database, digest, instant, read_value
from .evidence import comment_api


def metrics(rows, key):
    if not rows:
        return {"log_loss":None,"brier":None,"accuracy":None,"calibration":[]}
    probs = np.asarray([r[key] for r in rows])
    y = np.asarray([r["y"] for r in rows])
    truth = np.eye(3)[y]
    bins = []
    bucket = np.minimum((probs*10).astype(int),9)
    for i in range(10):
        mask = bucket == i
        if mask.any():
            bins.append({"p":float(probs[mask].mean()),"observed":float(truth[mask].mean()),"n":int(mask.sum())})
    return {"log_loss":float(-np.log(np.maximum(probs[np.arange(len(y)),y],1e-15)).mean()),
            "brier":float(np.square(probs-truth).sum(axis=1).mean()),
            "accuracy":float((probs.argmax(axis=1)==y).mean()),"calibration":bins}


def interval(rows):
    if not rows:
        return None, None
    weeks = defaultdict(list)
    for r in rows:
        iso = instant(r["kickoff"]).isocalendar()
        weeks[iso[:2]].append(math.log(max(r["b"][r["y"]],1e-15)/max(r["market"][r["y"]],1e-15)))
    blocks = list(weeks.values())
    sums = np.array([sum(x) for x in blocks])
    sizes = np.array([len(x) for x in blocks])
    mean = float(sums.sum()/sizes.sum())
    if len(blocks)<2:
        return mean,None
    rng = np.random.default_rng(PROTOCOL["bootstrap_seed"])
    draw = rng.integers(len(blocks),size=(PROTOCOL["bootstrap_weeks"],len(blocks)))
    dist = sums[draw].sum(axis=1)/sizes[draw].sum(axis=1)
    return mean, np.quantile(dist,[.025,.975]).tolist()


def public_ledger(root=None):
    with database(root) as db:
        reg = read_value(db,"registration")
        records = []
        for row in db.execute("SELECT * FROM captures ORDER BY received,fixture"):
            p = json.loads(row["payload"]) if row["payload"] else None
            records.append({"id":row["fixture"],"kickoff":row["kickoff"],"revision":row["revision"],"observed":row["received"],
                            "reason":row["reason"],"hash":row["hash"],"payload":p,
                            "evidence":row["evidence"],"witnessed":row["witnessed"]})
        return {"registration":reg,"registration_evidence":read_value(db,"registration_evidence"),
                "public_audit":read_value(db,"public_audit"),
                "health":read_value(db,"last_capture"),"records":records}


def _registered(reg, witness):
    if not reg or not witness:
        return False
    try:
        comment_api(witness["url"])
        created = instant(reg["created"])
        start = created.year + int(created.month >= 7)
        return bool(reg["model"] and instant(witness["at"]) >= created.replace(microsecond=0)
                    and reg["seasons"] == [f"{y}-{str(y+1)[-2:]}" for y in (start,start+1)]
                    and digest(reg["protocol"]) == digest(PROTOCOL))
    except (TypeError,ValueError,KeyError):
        return False


def _valid_vector(value):
    return (isinstance(value,(tuple,list)) and len(value)==3
            and all(type(x) in (float,int) and math.isfinite(x) and 0 < x < 1 for x in value)
            and math.isclose(sum(value),1.0,abs_tol=1e-8))


def _valid_capture(m, row, reg, witness, now):
    try:
        p = row["payload"]
        comment_api(row["evidence"])
        cutoff, kickoff = instant(p["cutoff"]),instant(m["kickoff_utc"])
        received, frozen = instant(p["observed"]),instant(p["frozen"])
        result_seen = m.get("result_observed_at")
        prediction_cutoff = p["prediction"].get("cutoff")
        trained_through = p["prediction"].get("trained_through")
        return (p["id"] == row["id"] == m["id"] and p["season"] == m["season"]
                and instant(row["kickoff"]) == instant(p["kickoff"]) == kickoff
                and p["schedule_revision"] == row["revision"] == m.get("schedule_revision",0)
                and cutoff == kickoff-timedelta(days=1) and kickoff <= now
                and (not result_seen or kickoff < instant(result_seen) <= now)
                and (not prediction_cutoff or instant(prediction_cutoff) <= received)
                and (not trained_through or instant(trained_through) <= received)
                and row["reason"] == "paired" and instant(row["observed"]) == received
                and instant(p["schedule_observed_at"]) <= received
                and instant(witness["at"]) <= received
                and row["evidence"].split("#")[0] == witness["url"].split("#")[0]
                and cutoff-timedelta(minutes=15) <= received <= frozen <= cutoff
                and frozen.replace(microsecond=0) <= instant(row["witnessed"]) <= cutoff
                and p["model_version"] == reg["model"] == p["prediction"]["model_version"]
                and p["prediction"]["probs"] == p["a"] and p["protocol_hash"] == digest(reg)
                and digest(p) == row["hash"] and all(_valid_vector(p.get(k)) for k in ("a","b","market"))
                and all(abs(.8*market+.2*a-b) <= 1e-9 for a,market,b in zip(p["a"],p["market"],p["b"])))
    except (KeyError,TypeError,ValueError,OverflowError):
        return False


def _cohort_report(fixtures, ledger, now, years, *, official):
    now = now or datetime.now(timezone.utc)
    reg = ledger["registration"]
    all_records = ledger["records"]
    records = {(r["id"],r["kickoff"],r["revision"]):r for r in all_records}
    record_counts = Counter((r["id"],r["kickoff"],r["revision"]) for r in all_records)
    fixtures = list(fixtures)
    fixture_counts = Counter(m["id"] for m in fixtures)
    witness = ledger.get("registration_evidence")
    reg_ok = _registered(reg,witness)
    audit = ledger.get("public_audit") or {}
    audit_bound = bool(reg_ok and audit.get("registration_ok") and audit.get("registration_hash")==digest(reg)
                       and audit.get("registration_evidence")==witness)
    externally_checked = 0
    local_pairs = 0
    details = []
    totals = Counter()
    finished = Counter()
    eligible = defaultdict(list)
    missing = Counter()
    all_rows = []
    for m in fixtures:
        if m["season"] not in years:
            continue
        season = m["season"]
        totals[season] += 1
        detail = {"id":m["id"],"home":m["home"],"away":m["away"],"season":season,
                  "kickoff":m.get("kickoff_utc"),"actual":None,"status":"pending_result",
                  "reason":"pending_result","a":None,"b":None,"market":None,"evidence":None}
        cutoff = instant(m["kickoff_utc"])-timedelta(days=1) if m.get("kickoff_utc") else None
        detail.update(cutoff=cutoff.isoformat() if cutoff else None,model_version=None)
        details.append(detail)
        def exclude(reason):
            missing[reason] += 1
            detail.update(status="excluded",reason=reason)
        if not official and reg and cutoff and cutoff < instant(reg["created"]):
            detail.update(status="outside_period",reason="before_registration")
            continue
        if m.get("source_conflict"):
            exclude("source_conflict")
            continue
        if m["hg"] is None or m["ag"] is None:
            continue
        if fixture_counts[m["id"]]!=1:
            exclude("duplicate_fixture")
            continue
        if m.get("result_observed_at") and instant(m["result_observed_at"]) > now:
            continue
        finished[season] += 1
        y = 0 if m["hg"]>m["ag"] else 2 if m["hg"]<m["ag"] else 1
        detail["actual"] = "HDA"[y]
        key = (m["id"],m["kickoff_utc"],m.get("schedule_revision",0))
        row = records.get(key)
        if record_counts[key] > 1:
            exclude("duplicate_capture")
            continue
        if not row or not row["payload"]:
            old_revision = any(r["id"]==m["id"] for r in all_records) if not row else False
            exclude(row["reason"] if row else "rescheduled" if old_revision else "no_matching_snapshot")
            continue
        p = row["payload"]
        if not p.get("market") or not p.get("b"):
            exclude(row["reason"])
            continue
        if not reg_ok or not _valid_capture(m,row,reg,witness,now):
            exclude("unverified_forecast")
            continue
        local_pairs += 1
        if not audit_bound or audit.get("forecasts",{}).get(row["hash"])!={"url":row["evidence"],"at":row["witnessed"]}:
            exclude("pending_audit")
            continue
        externally_checked += 1
        detail.update(status="paired",reason="paired",a=p["a"],b=p["b"],market=p["market"],evidence=row["evidence"],
                      model_version=p["model_version"])
        scored = {**p,"y":y,"home":m["home"],"away":m["away"]}
        eligible[season].append(scored)
        all_rows.append(scored)
    seasons = []
    for s in years:
        rows = eligible[s]
        seasons.append({"season":s,"total":totals[s],"finished":finished[s],"paired":len(rows),
                        "coverage":len(rows)/max(finished[s],1),"model_loss":metrics(rows,"b")["log_loss"],
                        "market_loss":metrics(rows,"market")["log_loss"]})
    delta,ci = interval(all_rows)
    complete = reg_ok and len(years)==2 and all(finished[s]==totals[s]==380 for s in years)
    season_cutoffs = [instant(m["kickoff_utc"])-timedelta(days=1) for m in fixtures
                      if m["season"] in years and m.get("kickoff_utc")]
    preregistered = bool(reg_ok and season_cutoffs and instant(witness["at"]) <= min(season_cutoffs))
    verification = {"registration_local":reg_ok,"registration_public":audit_bound,
                    "before_first_fixture":preregistered,"local_pairs":local_pairs,"public_pairs":externally_checked,
                    "pending_pairs":local_pairs-externally_checked,
                    "audited_at":audit.get("checked_at")}
    leading = (official and complete and preregistered and audit_bound and audit.get("ok") is True
               and externally_checked==local_pairs and all(s["coverage"]>=.9 for s in seasons)
               and delta is not None and delta>=.005 and ci and ci[0]>0)
    return {"status":"leading" if leading else "unproven" if complete else "accumulating",
            "n_pairs":len(all_rows),"seasons":seasons,"delta":delta,"ci":ci,"rows":details,
            "missing":dict(missing),"scores":{k:metrics(all_rows,k) for k in ("a","market","b")},
            "health":ledger["health"],"as_of":now.isoformat(),"verification":verification,
            "collection":{"fixtures":len(records),"paired":sum(bool(r["payload"] and r["payload"].get("b")) for r in records.values())},
            "worst":sorted(all_rows,key=lambda r:r["b"][r["y"]])[:10]}


def formal_report(fixtures, ledger, now=None):
    now = now or datetime.now(timezone.utc)
    fixtures = list(fixtures)
    reg = ledger.get("registration")
    years = reg["seasons"] if reg else []
    report = _cohort_report(fixtures,ledger,now,years,official=True)
    first = int(years[0][:4])-1 if years else None
    pilot_years = [f"{first}-{str(first+1)[-2:]}"] if first else []
    pilot = _cohort_report(fixtures,ledger,now,pilot_years,official=False)
    report["pilot"] = {key:pilot[key] for key in ("n_pairs","rows","seasons","delta","ci","scores","missing","verification")}
    return report
