"""Research-only residual candidate. The registered B0 comparator is never overwritten."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

import numpy as np

from . import data, models
from .evaluation import metrics
from .market import database, digest, instant, market_consensus, read_value, root_dir, write_value

KEY = "residual_candidate_v1"
PUBLIC_COMMENT = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/\d+#issuecomment-\d+\Z")


def _verified(row, fixture, reg, registration_evidence):
    """Recompute market probabilities from private receipts; never export the raw quotes."""
    try:
        p = json.loads(row["payload"])
        event = json.loads(row["raw"])
        received, cutoff, kickoff = instant(row["received"]), instant(p["cutoff"]), instant(p["kickoff"])
        frozen, witnessed = instant(p["frozen"]), instant(row["witnessed"])
        witness_at = instant(registration_evidence["at"])
        if not (PUBLIC_COMMENT.fullmatch(registration_evidence["url"]) and PUBLIC_COMMENT.fullmatch(row["evidence"])):
            return None
        if (p["id"] != row["fixture"] or p["id"] != fixture["id"] or p["season"] != fixture["season"]
                or kickoff != instant(fixture["kickoff_utc"]) or kickoff != instant(row["kickoff"])
                or p["schedule_revision"] != row["revision"] or row["revision"] != fixture.get("schedule_revision",0)
                or p["model_version"] != reg["model"] or p["protocol_hash"] != digest(reg)
                or p["prediction"]["model_version"] != reg["model"] or p["prediction"]["probs"] != p["a"]
                or digest(p) != row["hash"] or row["reason"] != "paired" or instant(p["observed"]) != received
                or not instant(p["schedule_observed_at"]) <= received
                or cutoff != kickoff - timedelta(hours=24)
                or not cutoff-timedelta(minutes=15) <= received <= frozen <= cutoff
                or witness_at > received or not frozen.replace(microsecond=0) <= witnessed <= cutoff
                or event["sport_key"] != "soccer_epl" or instant(event["commence_time"]) != kickoff
                or data.alias(event["home_team"]) != data.alias(fixture["home"])
                or data.alias(event["away_team"]) != data.alias(fixture["away"])):
            return None
        market = market_consensus(event,received)
        if market is None or not np.allclose(market,p["market"],rtol=0,atol=1e-10):
            return None
        if not np.allclose(models.blend(p["a"],market),p["b"],rtol=0,atol=1e-10):
            return None
        return {"id":fixture["id"], "probs":p["a"], "market":market,"cutoff":p["cutoff"],
                "timing_verified":True,"capture_hash":row["hash"]}
    except (ValueError, KeyError, TypeError, OverflowError):
        return None


def _examples(db, fixtures, now):
    reg = read_value(db,"registration")
    witness = read_value(db,"registration_evidence")
    if not reg or not witness:
        return []
    by_id = {m["id"]:m for m in fixtures}
    rows = []
    for row in db.execute("SELECT * FROM captures WHERE witnessed IS NOT NULL AND raw IS NOT NULL"):
        fixture = by_id.get(row["fixture"])
        if not fixture:
            continue
        sample = _verified(row,fixture,reg,witness)
        if sample is None or instant(row["witnessed"]) > now:
            continue
        if fixture.get("hg") is not None and fixture.get("ag") is not None and fixture.get("result_observed_at"):
            observed = instant(fixture["result_observed_at"])
            if instant(fixture["kickoff_utc"]) < observed <= now:
                sample["actual"] = models.OUTCOMES[models._outcome(fixture["hg"], fixture["ag"])]
                sample["result_observed_at"] = observed.isoformat()
        rows.append(sample)
    return rows


def _fixtures(root):
    store = Path(root or root_dir())/"fixtures.sqlite3"
    return data.load_matches(store if store.is_file() else None)


def train_candidate(root=None):
    """Freeze the first 380 eligible settled fixtures and persist a new research candidate once."""
    fixtures = _fixtures(root)
    with database(root) as db:
        db.execute("BEGIN IMMEDIATE")
        artifact = read_value(db, KEY)
        if artifact is None:
            now = datetime.now(timezone.utc)
            sample = _examples(db,fixtures,now)
            artifact = models.fit_challenger(sample)
            artifact["trained_at"] = now.isoformat()
            artifact["base_version"] = read_value(db,"registration")["model"]
            artifact["protocol_hash"] = digest(read_value(db,"registration"))
            write_value(db, KEY, artifact)
        return {"version":artifact["version"],"trained_at":artifact["trained_at"],"base_version":artifact["base_version"],
                "development_count":len(artifact["development_ids"]),"scope":"separate_research_candidate"}


def _fresh(fixture_id, root):
    fixtures = _fixtures(root)
    with database(root) as db:
        candidate = read_value(db,KEY)
        if candidate is None:
            raise ValueError("Train a candidate from 380 verified development fixtures first")
        if candidate["protocol_hash"] != digest(read_value(db,"registration")):
            raise ValueError("Base registration changed; use a separate candidate cohort")
        dev = set(candidate["development_ids"])
        examples = _examples(db,fixtures,datetime.now(timezone.utc))
    forecasts = []
    for row in examples:
        if (fixture_id and row["id"] != fixture_id) or row["id"] in dev or instant(row["cutoff"]) <= instant(candidate["trained_at"]):
            continue
        p = models.challenger_forecast(candidate,row["probs"],row["market"])
        forecasts.append({**row,"candidate":p, "model_version":candidate["version"]})
    return candidate, forecasts


def forecast_candidate(fixture_id, root=None):
    """Apply frozen coefficients to a witnessed post-training A + market snapshot."""
    _, rows = _fresh(fixture_id,root)
    if len(rows) != 1:
        raise ValueError("No eligible, unique post-training forecast for this fixture")
    row = rows[0]
    return {"id":row["id"],"cutoff":row["cutoff"],"model_version":row["model_version"],
            "probs":row["candidate"],"base_capture_hash":row["capture_hash"],"scope":"candidate_reconstruction"}


def score_candidate(root=None):
    candidate, rows = _fresh(None,root)
    settled = [dict(r,y=models.OUTCOMES.index(r["actual"])) for r in rows if r.get("actual")]
    return {"model_version":candidate["version"],"trained_at":candidate["trained_at"],"n":len(settled),
            "scope":"separate_candidate_reconstruction","scores":{k:metrics(settled,k) for k in ("candidate","market","probs")}}
