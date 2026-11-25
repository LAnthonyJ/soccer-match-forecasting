"""Merge locally observed EPL results from openfootball and OpenLigaDB."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from . import data

PRIMARY = "openfootball"
SECONDARY = "openligadb"
LICENSES = {PRIMARY: "CC0-1.0", SECONDARY: "ODbL-1.0"}
OVERLAY_LICENSE = "https://opendatacommons.org/licenses/odbl/1-0/"
URL = "https://api.openligadb.de/getmatchdata/pl/"
TEAM_NAMES = {"FC Arsenal": "Arsenal", "FC Liverpool": "Liverpool", "Brentford F.C.": "Brentford"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def parse_secondary(raw, season):
    """Keep scored regulation-time results; exclude halftime and other result types."""
    records = json.loads(raw)
    if not isinstance(records, list):
        raise ValueError("Invalid OpenLigaDB EPL response")
    rows = {}
    provider_ids = set()
    for m in records:
        if m.get("leagueShortcut") != "pl" or str(m.get("leagueSeason")) != season[:4]:
            raise ValueError("OpenLigaDB competition or season mismatch")
        home, away = [data.alias(TEAM_NAMES.get(n, n)) for n in
                      (m["team1"]["teamName"], m["team2"]["teamName"])]
        fid = data.fixture_id(season, home, away)
        source_id = m["matchID"]
        if type(source_id) is not int or source_id in provider_ids or fid in rows or home == away:
            raise ValueError("Duplicate or invalid OpenLigaDB fixture")
        provider_ids.add(source_id)
        kickoff = data._utc(m["matchDateTimeUTC"]) if m.get("matchDateTimeUTC") else None
        day = datetime.fromisoformat(kickoff).astimezone(data.LONDON).date().isoformat() if kickoff else None
        finals = [r for r in m.get("matchResults", []) if r.get("resultTypeKind") == "After90Minutes"]
        hg = ag = None
        if m.get("matchIsFinished") is True and len(finals) == 1:
            h, a = finals[0].get("pointsTeam1"), finals[0].get("pointsTeam2")
            if all(type(g) is int and g >= 0 for g in (h, a)):
                hg, ag = h, a
        rows[fid] = dict(id=fid, season=season, fixture_id=str(source_id), home=home, away=away,
                         date=day, kickoff_utc=kickoff, hg=hg, ag=ag)
    return list(rows.values())


def _setup(db):
    data._prepare_versions(db)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS provider_fixtures (
            season TEXT, provider TEXT, id TEXT, observed TEXT, revision TEXT, payload TEXT,
            PRIMARY KEY (season,provider,id,observed));
        CREATE TABLE IF NOT EXISTS provider_attempts (
            season TEXT, provider TEXT, attempted TEXT, status TEXT, revision TEXT,
            PRIMARY KEY (season,provider,attempted));
        CREATE TABLE IF NOT EXISTS score_resolutions (
            id TEXT, observed TEXT, fingerprint TEXT, provider TEXT, evidence TEXT, note TEXT,
            PRIMARY KEY (id,observed));
        CREATE TRIGGER IF NOT EXISTS prevent_legacy_fixture_overwrite
        BEFORE INSERT ON fixture_versions
        WHEN json_type(NEW.payload,'$.source_observations') IS NULL
          AND EXISTS (SELECT 1 FROM fixture_versions prior WHERE prior.id=NEW.id
                      AND json_type(prior.payload,'$.source_observations')='object')
        BEGIN
          SELECT RAISE(ABORT, 'A v1 fixture requires a multi-source writer. Restart the old worker.');
        END;
    """)


def _source_rows(db, season, provider, cutoff="9999"):
    rows = {}
    # Only read the last revision of each provider fixture.
    sql = """SELECT p.id,p.payload,p.observed,p.revision FROM provider_fixtures p JOIN
        (SELECT id,MAX(observed) t FROM provider_fixtures WHERE season=? AND provider=?
            AND observed<=? GROUP BY id) s ON p.id=s.id AND p.observed=s.t
        WHERE p.season=? AND p.provider=?"""
    for fid, payload, observed, revision in db.execute(sql, (season,provider,cutoff,season,provider)):
        if payload is not None:
            rows[fid] = (json.loads(payload), observed, revision)
    return rows


def _store(db, season, provider, payload, observed, revision):
    previous = _source_rows(db, season, provider)
    by_id = {row["id"]: row for row in payload}
    for fid, row in by_id.items():
        if fid not in previous or row != previous[fid][0]:
            db.execute("INSERT INTO provider_fixtures VALUES (?,?,?,?,?,?)",
                       (season, provider, fid, observed, revision, json.dumps(row, ensure_ascii=False)))
    if provider == PRIMARY:  # Validated complete season. A partial secondary response cannot withdraw fixtures.
        for fid in previous.keys() - by_id.keys():
            db.execute("INSERT INTO provider_fixtures VALUES (?,?,?,?,?,NULL)",
                       (season, provider, fid, observed, revision))


def _reference(row, timestamp, revision):
    return dict(fixture_id=row.get("fixture_id",row["id"]), observed_at=timestamp, revision=revision,
                date=row["date"], kickoff_utc=row["kickoff_utc"], hg=row["hg"], ag=row["ag"])


def _dispute_id(refs):
    """Bind a decision to the two observations, including a correction that later reverts."""
    return _digest([[refs[p][key] for key in ("hg","ag","observed_at")] for p in (PRIMARY,SECONDARY)])


def _previous(db, season):
    rows = (json.loads(raw) for (raw,) in data._versions(db, "9999"))
    return {r["id"]:r for r in rows if r["season"] == season and not r.get("withdrawn")}


def _merged(db, season, observed):
    primaries = _source_rows(db, season, PRIMARY)
    secondaries = _source_rows(db, season, SECONDARY)
    previous = _previous(db, season)
    if not primaries:
        # A v0.1 SQLite store may already contain newer observed primary schedules.
        if not previous:
            primaries = {fid:(r,None,data.PIN) for fid,r in data._base().items() if r["season"] == season}
        for fid, row in previous.items():
            primary_ref = row.get("source_observations",{}).get(PRIMARY)
            if primary_ref:
                primary = {k:row[k] for k in data.FIELDS}
                primary.update({k:primary_ref[k] for k in ("hg","ag","date","kickoff_utc")})
                primaries[fid] = primary, primary_ref["observed_at"], primary_ref["revision"]
            elif "source_observations" not in row:
                primaries[fid] = row, row.get("observed_at"), row.get("source_commit",data.PIN)
    choices = {fid:(fingerprint,provider,at,evidence,note) for fid,fingerprint,provider,at,evidence,note
               in db.execute("SELECT id,fingerprint,provider,observed,evidence,note FROM score_resolutions ORDER BY observed")}
    result = []
    for fid,(primary,pt,pr) in primaries.items():
        row = {k:primary[k] for k in data.FIELDS}
        row.update(schedule_source=PRIMARY, result_source=PRIMARY if row["hg"] is not None else None,
                   schedule_observed_at=pt, result_observed_at=pt, source_conflict=None,
                   source_observations={PRIMARY:_reference(primary,pt,pr)})
        alt = secondaries.get(fid)
        if alt:
            secondary, st, sr = alt
            if (secondary["home"],secondary["away"]) != (primary["home"],primary["away"]):
                raise ValueError("Non-exact source team identity")
            row["source_observations"][SECONDARY] = _reference(secondary,st,sr)
            p, s = (primary["hg"],primary["ag"]), (secondary["hg"],secondary["ag"])
            prior = previous.get(fid,{})
            prior_conflict = prior.get("source_conflict") or prior.get("resolution")
            if p != s and ((None not in p and None not in s) or prior_conflict):
                fingerprint = _dispute_id(row["source_observations"])
                choice = choices.get(fid)
                if choice and choice[0] == fingerprint:
                    _, chosen, at, evidence, note = choice
                    selected = primary if chosen == PRIMARY else secondary
                    row.update(hg=selected["hg"],ag=selected["ag"],result_observed_at=at,result_source=chosen,
                               resolution={"provider":chosen, "observed_at":at, "evidence":evidence, "note":note})
                else:
                    row.update(source_conflict="score_conflict",hg=None,ag=None,result_source=None)
            elif None in p and None not in s:
                row.update(hg=s[0],ag=s[1],result_source=SECONDARY,result_observed_at=st)
            elif p == s == (None,None) and prior_conflict:
                row["source_conflict"] = "score_conflict"
        result.append(row)
    return result


def _secondary(season):
    raw = data._get(URL + season[:4])
    rows = parse_secondary(raw,season)
    return rows,_digest(rows)


def synchronize(db_path, season):
    batches = {}
    errors = {}
    for name, loader in ((PRIMARY,data._primary),(SECONDARY,_secondary)):
        try:
            rows, rev = loader(season)
            if name == PRIMARY:
                data._validate_season(rows, season)
            batches[name] = rows, data._utc(datetime.now(timezone.utc)), rev
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            errors[name] = exc
    if not batches and any(isinstance(e,ValueError) for e in errors.values()) and not Path(db_path).exists():
        raise next(e for e in errors.values() if isinstance(e, ValueError))
    observed = data._utc(datetime.now(timezone.utc))
    path = Path(db_path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path,timeout=15)) as db,db:
        _setup(db)
        db.execute("BEGIN IMMEDIATE")
        for provider in LICENSES:
            batch = batches.get(provider)
            at = batch[1] if batch else observed
            latest = db.execute("SELECT MAX(attempted) FROM provider_attempts WHERE season=? AND provider=?",
                                (season,provider)).fetchone()[0]
            if latest and latest >= at:
                raise ValueError("A newer or simultaneous source observation was already committed")
            if batch:
                rows,received,revision = batch
                _store(db,season,provider,rows,received,revision)
            db.execute("INSERT INTO provider_attempts VALUES (?,?,?,?,?)",
                       (season,provider,at,"ok" if batch else type(errors[provider]).__name__,batch[2] if batch else None))
        if batches:
            rows = _merged(db,season,observed)
            if rows:
                versions = {p: db.execute("SELECT revision FROM provider_attempts WHERE season=? AND provider=? AND status='ok' ORDER BY attempted DESC LIMIT 1", (season,p)).fetchone() for p in LICENSES}
                view = data._record_view(db,season,rows,source_commit=_digest(versions),observed=observed)
            else:
                view = {"season":season,"changed":0,"fixtures":0,"completed":0}
        else:
            view = {"season":season,"changed":0}
    return {**view,"status":"ok" if not errors else "partial" if batches else "offline",
            "health":health(path,season,now=observed)}


def _readable(path):
    if not path or not Path(path).is_file():
        return None
    return sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True)


def health(db_path, season, matches=None, now=None):
    instant = datetime.fromisoformat(data._utc(now or datetime.now(timezone.utc)))
    rows = [m for m in (matches if matches is not None else data.load_matches(db_path,as_of=instant)) if m["season"] == season]
    settled = [m for m in rows if m.get("hg") is not None and m.get("ag") is not None]
    overdue = [m["id"] for m in rows if m["hg"] is None and m.get("kickoff_utc") and
               datetime.fromisoformat(m["kickoff_utc"].replace("Z","+00:00")) < instant - timedelta(days=1)]
    conflicts = [m["id"] for m in rows if m.get("source_conflict")]
    sources = {p:dict(provider=p,last_success_at=None,last_attempt_at=None,status="never",revision=None,license=license_id)
               for p,license_id in LICENSES.items()}
    db = _readable(db_path)
    if db is not None:
        with closing(db):
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "provider_attempts" in tables:
                for p,at,status,revision in db.execute("SELECT provider,attempted,status,revision FROM provider_attempts WHERE season=? AND attempted<=? ORDER BY attempted", (season,data._utc(instant))):
                    sources[p].update(last_attempt_at=at,status=status)
                    if status == "ok":
                        sources[p].update(last_success_at=at,revision=revision)
            if not sources[PRIMARY]["last_success_at"] and "season_syncs" in tables:
                # Merged v1 sync records include secondary-only fetches; only v0.1 syncs count here.
                first = None
                if "provider_attempts" in tables:
                    first = db.execute("SELECT MIN(attempted) FROM provider_attempts WHERE season=?",
                                       (season,)).fetchone()[0]
                legacy=db.execute("""SELECT observed,source_commit FROM season_syncs
                    WHERE season=? AND observed<=? AND observed<? ORDER BY observed DESC LIMIT 1""",
                    (season,data._utc(instant),first or "9999")).fetchone()
                if legacy:
                    entry = sources[PRIMARY]
                    entry.update(last_success_at=legacy[0],revision=legacy[1])
                    if not entry["last_attempt_at"]:
                        entry.update(last_attempt_at=legacy[0],status="ok")
    return dict(season=season,last_scored_date=max((m["date"] for m in settled),default=None),
                last_sync_at=max((s["last_success_at"] for s in sources.values() if s["last_success_at"]),default=None),
                completed=len(settled),total=len(rows),overdue_count=len(overdue),overdue_ids=sorted(overdue),
                conflict_count=len(conflicts),conflict_ids=sorted(conflicts),sources=list(sources.values()))


def overlay(db_path, season=None, as_of=None):
    records = []
    db = _readable(db_path)
    if db is not None:
        with closing(db):
            available = db.execute("SELECT 1 FROM sqlite_master WHERE name='provider_fixtures'").fetchone()
            if available:
                seasons = [season] if season else [s for (s,) in db.execute(
                    "SELECT DISTINCT season FROM provider_fixtures WHERE provider=? ORDER BY season",(SECONDARY,))]
                cutoff = data._utc(as_of) if as_of else "9999"
                for s in seasons:
                    for row,at,revision in _source_rows(db,s,SECONDARY,cutoff).values():
                        records.append(dict(row, observed_at=at, revision=revision))
    return dict(provider=SECONDARY,license=LICENSES[SECONDARY],source_url="https://www.openligadb.de/",
                license_url=OVERLAY_LICENSE,records=sorted(records,key=lambda r:r["id"]))


def resolve(db_path, season, fixture_id, provider, evidence, note):
    if provider not in LICENSES or not note.strip() or urlsplit(evidence).scheme != "https" or not urlsplit(evidence).hostname:
        raise ValueError("Specify a provider, a public HTTPS evidence URL and a reason")
    now = data._utc(datetime.now(timezone.utc))
    with closing(sqlite3.connect(db_path,timeout=15)) as db,db:
        _setup(db)
        db.execute("BEGIN IMMEDIATE")
        row = _previous(db,season).get(fixture_id)
        if not row or not row.get("source_conflict"):
            raise ValueError("Fixture has no active score conflict")
        refs = row["source_observations"]
        scores = [[refs[p]["hg"], refs[p]["ag"]] for p in (PRIMARY,SECONDARY)]
        if None in scores[0 if provider==PRIMARY else 1]:
            raise ValueError("Preferred source has no final result")
        db.execute("INSERT INTO score_resolutions VALUES(?,?,?,?,?,?)",
                   (fixture_id,now,_dispute_id(refs),provider,evidence,note.strip()))
        rows = _merged(db,season,now)
        return data._record_view(db,season,rows,source_commit=row["source_commit"],observed=now)


if __name__ == "__main__":
    from .market import root_dir
    parser = argparse.ArgumentParser(description="Resolve a disputed result against public evidence")
    cmd = parser.add_subparsers(dest="command",required=True).add_parser("resolve")
    cmd.add_argument("season")
    cmd.add_argument("fixture_id")
    cmd.add_argument("--prefer",required=True,choices=list(LICENSES))
    cmd.add_argument("--evidence",required=True)
    cmd.add_argument("--note",required=True)
    args = parser.parse_args()
    print(json.dumps(resolve(root_dir()/"fixtures.sqlite3",args.season,args.fixture_id,args.prefer,args.evidence,args.note),ensure_ascii=False))
