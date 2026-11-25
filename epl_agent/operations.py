"""Durable collector telemetry. Only the allowlisted fields enter public snapshots."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3

import requests

from .market import instant, root_dir

PUBLIC_FIELDS = ("heartbeat_at", "upload_sha", "upload_at", "deployed_sha", "deployed_at",
                 "publication_status", "checked_at", "sync_status")


def stamp():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def store(root=None, *, write=False):
    path = Path(root or root_dir()) / "operations.sqlite3"
    if not write and not path.exists():
        yield None
        return
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path.resolve().as_uri() + ("?mode=rwc" if write else "?mode=ro"),
                                 uri=True, timeout=10)) as db, db:
        if write:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS progress(name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS uploaded(sha TEXT PRIMARY KEY, items TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS upload_times(sha TEXT PRIMARY KEY, uploaded_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deployments(
                    id TEXT, kickoff TEXT, revision INTEGER, hash TEXT, result_observed_at TEXT,
                    seen_at TEXT, sha TEXT, PRIMARY KEY(id,kickoff,revision,hash,result_observed_at));
            """)
        yield db


def set_values(root=None, **values):
    with store(root, write=True) as db:
        db.executemany("INSERT OR REPLACE INTO progress VALUES(?,?)",
                       [(k, json.dumps(v)) for k,v in values.items()])


def values(root=None):
    with store(root) as db:
        return {k:json.loads(v) for k,v in db.execute("SELECT name,value FROM progress")} if db else {}


def public_state(root=None):
    state = values(root)
    return {key:state[key] for key in PUBLIC_FIELDS if state.get(key) is not None}


def public_deployments(root=None):
    with store(root) as db:
        if not db:
            return []
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute("SELECT * FROM deployments ORDER BY id,revision,seen_at,sha")]
        for row in rows:
            row["result_observed_at"] = row["result_observed_at"] or None
        return rows


def record_upload(root, sha, batch):
    by_id = {m["id"]:m for m in batch["fixtures"]}
    entries = []
    for row in batch["ledger"]["records"]:
        fixture = by_id.get(row["id"])
        if (not row.get("hash") or not fixture or
                (row["kickoff"],row["revision"]) != (fixture["kickoff_utc"],fixture.get("schedule_revision",0))):
            continue
        entries.append([row["id"],row["kickoff"],row["revision"],row["hash"],fixture.get("result_observed_at") or ""])
    with store(root, write=True) as db:
        received = stamp()
        db.execute("""INSERT OR IGNORE INTO upload_times SELECT ?,?
                      WHERE NOT EXISTS(SELECT 1 FROM uploaded WHERE sha=?)""",(sha,received,sha))
        db.execute("INSERT OR REPLACE INTO uploaded VALUES (?,?)", (sha,json.dumps(entries)))
        for key,value in {"upload_sha":sha,"upload_at":received,"publication_status":"pending_deployment"}.items():
            db.execute("INSERT OR REPLACE INTO progress VALUES(?,?)",(key,json.dumps(value)))


def upload_times(root=None):
    """First collector receipts observed after this schema was installed."""
    through=stamp()
    rows=[]
    with store(root) as db:
        if db and db.execute("SELECT 1 FROM sqlite_master WHERE name='upload_times'").fetchone():
            rows=[{"sha":sha,"uploaded_at":at} for sha,at in db.execute(
                "SELECT sha,uploaded_at FROM upload_times WHERE uploaded_at<=? ORDER BY uploaded_at,sha",(through,))]
    return {"schema":"epl-upload-times/v1","from_utc":rows[0]["uploaded_at"] if rows else None,
            "through_utc":through,"uploads":rows}


def check_deployment(root, http=requests.get):
    repo = os.getenv("EPL_EVIDENCE_REPO","")
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo):
        return
    owner, name = repo.split("/")
    response = http(f"https://{owner}.github.io/{name}/public-data-commit.txt",
                    timeout=8, headers={"Cache-Control":"no-cache"}, allow_redirects=False)
    response.raise_for_status()
    sha = response.text.strip()
    if not re.fullmatch("[0-9a-f]{40}", sha):
        raise ValueError("Invalid deployed commit")
    state = values(root)
    at = stamp()
    with store(root, write=True) as db:
        entry = db.execute("SELECT items FROM uploaded WHERE sha=?", (sha,)).fetchone()
        if entry:
            db.executemany("INSERT OR IGNORE INTO deployments VALUES(?,?,?,?,?,?,?)",
                           [tuple(item)+(at,sha) for item in json.loads(entry[0])])
    updates = {"deployed_sha":sha,"checked_at":at}
    if sha != state.get("deployed_sha"):
        updates["deployed_at"] = at
    if sha == state.get("upload_sha"):
        updates["publication_status"] = "deployed"
    set_values(root, **updates)


def status(matches, ledger, health, now, state, *, snapshot_at=None):
    cutoffs = sorted(instant(m["kickoff_utc"])-timedelta(days=1) for m in matches
                     if m.get("kickoff_utc") and m.get("hg") is None and not m.get("withdrawn")
                     and (m.get("schedule_status") == "verified" or
                          (snapshot_at is not None and "schedule_status" not in m)))
    next_cutoff = next((dt for dt in cutoffs if dt >= now), None)
    pulse = state.get("heartbeat_at")
    return {**state, "worker_status": ("unknown" if not pulse else "offline"
                if (now-instant(pulse)).total_seconds()>180 else "online"),
            "as_of":now.isoformat(), "snapshot_at":snapshot_at,
            "next_window":(next_cutoff-timedelta(minutes=15)).isoformat() if next_cutoff else None,
            "next_cutoff":next_cutoff.isoformat() if next_cutoff else None,
            "pending_witness":sum(bool(r.get("hash") and not r.get("evidence")) for r in ledger["records"]),
            "overdue":health["overdue_count"], "scores_through":health["last_scored_date"],
            "last_source_success":health["last_sync_at"]}


def local_status():
    from . import data
    root = root_dir()
    path = root/"fixtures.sqlite3"
    now = datetime.now(timezone.utc)
    matches = data.load_matches(path)
    health = data.season_health(path,max(m["season"] for m in matches),matches,now)
    state = values(root)
    market = root/"market.sqlite3"
    waiting = 0
    if market.is_file():
        with closing(sqlite3.connect(market.resolve().as_uri()+"?mode=ro",uri=True)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='captures'").fetchone():
                waiting = db.execute("SELECT COUNT(*) FROM captures WHERE hash IS NOT NULL AND evidence IS NULL").fetchone()[0]
    result = status(matches,{"records":[]},health,now,state)
    result["pending_witness"] = waiting
    return result
