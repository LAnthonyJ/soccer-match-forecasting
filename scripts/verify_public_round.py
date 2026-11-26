"""Independently check the public fan board and its provenance against a pinned EPL snapshot."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from epl_agent import fan, service, snapshot

DEFAULT_SITE = "https://lanthonyj.github.io/worldcup_prediction"
CARD_FIELDS = ("id", "season", "round", "date", "home", "away", "hg", "ag", "kickoff_utc", "schedule_status")


def equal(actual, expected, label):
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, found {actual!r}")


def read(session, url, limit=8_000_000):
    with session.get(url, timeout=(5, 30), allow_redirects=False, stream=True,
                     headers={"Cache-Control":"no-cache"}) as res:
        if res.status_code != 200:
            raise ValueError(f"GET {url}: HTTP {res.status_code}")
        buf = bytearray()
        for chunk in res.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > limit:
                raise ValueError(f"Response exceeds {limit} bytes: {url}")
        return bytes(buf)


def card(row):
    prediction = row.get("prediction")
    return {**{key:row.get(key) for key in CARD_FIELDS}, "source_conflict":bool(row.get("source_conflict")),
            "prediction":None if prediction is None else
            {key:prediction[key] for key in ("source","cutoff","a")}}


def verify_round(session, site, expected):
    year, week = expected["current_season"], expected["current_week"]
    board = json.loads(read(session, site+"/board.json"))
    for key in ("current_season", "current_week", "current_id", "updated_at", "batch_id", "public_snapshot"):
        equal(board.get(key), expected[key], "board."+key)
    games = {r["id"]:card(r) for r in expected["fixtures"]}
    if not games or len(games) != len(expected["fixtures"]):
        raise ValueError("Invalid fixture set in reference round")
    filename = f"/rounds/{year}/{fan.round_file(week)}.json"
    for name, rows in (("board",board["fixtures"]), ("round",json.loads(read(session,site+filename)))):
        equal(sorted(row["id"] for row in rows), sorted(games), name+" fixture IDs")
        for row in rows:
            equal(card(row), games[row["id"]], name+" fixture "+row["id"])
    for fid, local in games.items():
        detail = json.loads(read(session, site+"/fixtures/"+fid+".json"))
        # A finished, disputed or waiting card uses score state. Only compare
        # the compact projection the app actually renders in that state.
        equal(card(detail), local, "detail "+fid)
    return board


def verify_public(site, local, sha, repo, issue):
    parts = urlsplit(site)
    if parts.scheme != "https" and not (parts.scheme=="http" and parts.hostname in ("localhost","127.0.0.1","::1")):
        raise ValueError("Use HTTPS or a loopback test site.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Supply the site base URL without credentials, query or fragment.")
    if not re.fullmatch(r"[a-f0-9]{40}", sha) or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Supply a full pinned commit and owner/repository.")
    site = site.rstrip("/")
    raw = f"https://raw.githubusercontent.com/{repo}/{sha}/snapshot.json"
    os.environ.update(EPL_EVIDENCE_REPO=repo, EPL_EVIDENCE_ISSUE=str(issue))
    with requests.Session() as session:
        session.trust_env = False
        def confirm_marker():
            actual = read(session,site+"/public-data-commit.txt",128).decode().strip()
            equal(actual, sha, "public-data-commit.txt")
        confirm_marker()
        batch, original = snapshot.read_verified(local, session=session)
        file_hash = hashlib.sha256(original).hexdigest()
        for url in (raw, site+"/snapshot.json"):
            equal(hashlib.sha256(read(session,url,128_000_000)).hexdigest(), file_hash, url)
        dashboard = service.dashboard(static=True,snapshot=batch)
        dashboard["batch_id"] = file_hash
        expected = fan.export_board(dashboard,batch["fixtures"])
        board = verify_round(session,site,expected)
        equal(board["fixed_snapshot_url"],raw,"board fixed download")
        equal(json.loads(read(session,site+"/batch.json")), {"batch_id":file_hash}, "batch marker")
        online = json.loads(read(session,site+"/report.json",32_000_000))
        scores = json.loads(original)["scores"]
        equal(online["status"],scores["status"],"evaluation status")
        for group, cohort in (("formal",online), ("pilot",online["pilot"])):
            for key in ("n_pairs","scores","delta","ci","missing","independent"):
                if key in scores[group]:
                    equal(cohort[key],scores[group][key],group+"."+key)
        confirm_marker()
    return {"verified":True,"checked_at":datetime.now(timezone.utc).isoformat(),"site":site,
            "commit":sha,"snapshot_sha256":file_hash,"snapshot":raw,"as_of":batch["as_of"],
            "season":board["current_season"],"week":board["current_week"],"fixtures":board["fixtures"],
            "formal_pairs":scores["formal"]["n_pairs"],"pilot_pairs":scores["pilot"]["n_pairs"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot",type=Path,help="directory containing the fixed snapshot.json")
    parser.add_argument("--site",default=DEFAULT_SITE)
    parser.add_argument("--commit",default=(ROOT/".github/epl-public-data-sha").read_text().strip())
    parser.add_argument("--repo",default="LAnthonyJ/worldcup_prediction")
    parser.add_argument("--issue",type=int,default=1)
    args = parser.parse_args()
    try:
        receipt = verify_public(args.site,args.snapshot,args.commit,args.repo,args.issue)
    except (OSError,ValueError,KeyError,TypeError,requests.RequestException) as exc:
        parser.exit(1,f"Public round verification failed: {exc}\n")
    print(json.dumps(receipt,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
