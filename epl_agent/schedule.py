"""Bind a kickoff decision to the two open-data candidates for this exact fixture."""
from datetime import datetime
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from . import data

PROVIDERS = ("openfootball", "openligadb")
REVIEW_FILE = Path(__file__).with_name("assets") / "schedule" / "reviews.json"


@lru_cache(maxsize=1)
def reviews():
    return json.loads(REVIEW_FILE.read_text(encoding="utf8"))


def fingerprint(fid, refs):
    values = [fid, *[(refs.get(p) or {}).get("kickoff_utc") for p in PROVIDERS]]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def select(row, when):
    refs = row["source_observations"]
    candidates = [refs.get(p) or {} for p in PROVIDERS]
    clocks = [r.get("kickoff_utc") for r in candidates]
    observed = all(r.get("observed_at") for r in candidates)
    signature = fingerprint(row["id"], refs)
    reviewed = reviews()["decisions"].get(signature)
    row.pop("schedule_review",None)
    if reviewed and (not observed or reviewed["fixture"] != row["id"] or reviewed["observed_at"] > when):
        reviewed = None
    source = None
    if reviewed:
        source = reviewed["provider"]
        evidence = reviews()["evidence"][reviewed["evidence"]]
        row["schedule_review"] = {**evidence, "provider":source, "fingerprint":signature,
                                  "observed_at":reviewed["observed_at"]}
    elif observed and clocks[0] and clocks[0] == clocks[1]:
        source = PROVIDERS[0]
    if source:
        chosen = refs[source]["kickoff_utc"]
        row.update(kickoff_utc=chosen, date=datetime.fromisoformat(chosen).astimezone(data.LONDON).date().isoformat(),
                   schedule_status="verified", schedule_source=source, schedule_observed_at=when)
    else:
        status = "conflict" if observed and all(clocks) and clocks[0] != clocks[1] else "unconfirmed"
        row.update(kickoff_utc=None, schedule_status=status, schedule_source=None, schedule_observed_at=None)
    return row


def public_fixture(match, now):
    """A bare historical dataset remains replayable; future primary-only dates are provisional."""
    if ("schedule_status" in match or match.get("hg") is not None
            or match["date"] < now.astimezone(data.LONDON).date().isoformat()):
        return match
    return dict(match, kickoff_utc=None, schedule_status="unconfirmed", schedule_source=None)
