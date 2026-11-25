"""Free-tier odds collection. The SQLite database is private to its operator."""
import hashlib
import json
import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

UTC = timezone.utc
BOOKS = ("williamhill", "betfair_sb_uk", "ladbrokes_uk", "unibet_uk", "betvictor", "paddypower",
         "skybet", "coral", "betway")
PROTOCOL = {"version": "epl-t24-v1", "books": BOOKS, "region": "uk", "market": "h2h",
            "min_books": 3, "window_minutes": 15, "max_book_age_hours": 24,
            "freeze_lead_minutes": 2, "poll_minutes": 3,
            "market_weight": .8, "min_coverage": .9, "min_delta": .005,
            "bootstrap_weeks": 10000, "bootstrap_seed": 20261125}


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode("utf8")).hexdigest()


def instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or not value.tzinfo:
        raise ValueError("Expected timezone-aware timestamp")
    return value.astimezone(UTC)


def root_dir():
    return Path(os.environ.get("EPL_AGENT_HOME", Path.home() / ".epl-prediction-agent"))


@contextmanager
def database(root=None):
    folder = Path(root or root_dir())
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "market.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS captures (
                fixture TEXT, kickoff TEXT, received TEXT, raw TEXT, reason TEXT, payload TEXT,
                hash TEXT UNIQUE, evidence TEXT, witnessed TEXT, revision INTEGER NOT NULL,
                PRIMARY KEY(fixture,kickoff,revision));
            CREATE TABLE IF NOT EXISTS observations (
                received TEXT NOT NULL,event TEXT NOT NULL,raw TEXT NOT NULL,PRIMARY KEY(received,event));
            CREATE TABLE IF NOT EXISTS quote_candidates (
                fixture TEXT NOT NULL, kickoff TEXT NOT NULL, revision INTEGER NOT NULL,
                received TEXT NOT NULL, event TEXT NOT NULL, consensus TEXT NOT NULL,
                PRIMARY KEY(fixture,kickoff,revision));
        """)
        yield db
        db.commit()
    finally:
        db.close()


def read_value(db, key, default=None):
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def write_value(db, key, value):
    db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, canonical(value)))


def freeze_protocol(db, model_version, now):
    reg = read_value(db, "registration")
    if reg:
        return reg
    # A season already in progress cannot be one of the two formal evaluation seasons.
    start = now.year + (now.month >= 7)
    reg = {"protocol": PROTOCOL, "model": model_version, "created": now.isoformat(),
           "seasons": [f"{y}-{str(y+1)[-2:]}" for y in (start, start+1)]}
    write_value(db, "registration", reg)
    return reg


def market_consensus(event, receipt):
    from .data import alias
    if not isinstance(event,dict) or not all(isinstance(event.get(k),str) and event[k].strip()
                                             for k in ("home_team", "away_team")):
        return None
    h, a = (alias(event[k]) for k in ("home_team", "away_team"))
    if h == a or not isinstance(event.get("bookmakers"),list):
        return None
    books = {}
    for book in event["bookmakers"]:
        if not isinstance(book,dict) or book.get("key") not in BOOKS or not isinstance(book.get("markets"),list):
            continue
        for line in book["markets"]:
            if not isinstance(line,dict) or line.get("key") != "h2h":
                continue
            try:
                updated = instant(line.get("last_update") or book.get("last_update"))
                if not timedelta(0) <= receipt-updated <= timedelta(hours=24):
                    continue
                outcomes = line["outcomes"]
                if not isinstance(outcomes,list) or len(outcomes) != 3 or not all(
                    isinstance(x,dict) and isinstance(x.get("name"),str)
                    and type(x.get("price")) in (float,int) for x in outcomes):
                    continue
                entries = {alias(x["name"]): float(x["price"]) for x in outcomes}
                prices = [entries[h], entries[alias("Draw")], entries[a]]
                if len(entries) != 3 or not all(math.isfinite(p) and p > 1 for p in prices):
                    continue
                inv = [1/p for p in prices]
                if book["key"] not in books or updated >= books[book["key"]][0]:
                    books[book["key"]] = updated, [p/sum(inv) for p in inv]
            except (ValueError, KeyError, TypeError, OverflowError):
                continue
    if len(books) < PROTOCOL["min_books"]:
        return None
    return [sum(x)/len(books) for x in zip(*(values for _,values in books.values()))]


def scheduled(matches, now):
    due = []
    for match in matches:
        if match.get("hg") is not None or not match.get("kickoff_utc"):
            continue
        cutoff = instant(match["kickoff_utc"])-timedelta(hours=24)
        if cutoff-timedelta(minutes=15) <= now <= cutoff:
            due.append((cutoff, match))
    return sorted(due, key=lambda item: item[0])


def _fixture_key(match):
    return match["id"], match["kickoff_utc"], match.get("schedule_revision", 0)


def _record_missed(db, matches, now, registered):
    # Keep gaps even when the operator's worker was offline for the entire window.
    missed = []
    for m in matches:
        if not m.get("kickoff_utc"):
            continue
        cutoff = instant(m["kickoff_utc"]) - timedelta(days=1)
        if registered <= cutoff < now:
            missed.append((*_fixture_key(m), now.isoformat()))
    db.executemany("""INSERT INTO captures(fixture,kickoff,revision,received,reason)
        VALUES(?,?,?,?,'missed_window') ON CONFLICT(fixture,kickoff,revision) DO NOTHING""", missed)
    db.execute("""UPDATE captures SET reason='missed_window'
        WHERE payload IS NULL AND reason='collecting_quotes' AND julianday(kickoff)-1 < julianday(?)""",
               (now.isoformat(),))


def _index_events(events):
    from .data import alias
    indexed = {}
    for e in events:
        if not isinstance(e, dict) or e.get("sport_key") != "soccer_epl" or not all(
            isinstance(e.get(k), str) and e[k].strip() for k in ("id","home_team","away_team","commence_time")):
            continue
        try:
            key = alias(e["home_team"]), alias(e["away_team"]), instant(e["commence_time"])
        except (ValueError, OverflowError):
            continue
        # Multiple provider fixture IDs for one kickoff cannot be resolved safely.
        indexed[key] = None if key in indexed else e
    return indexed


def _poll(db, now, finalizing, clock, get):
    token = os.getenv("ODDS_API_KEY")
    month = "quota:" + now.strftime("%Y-%m")
    remaining = read_value(db, month, 500)
    if not token or remaining <= 50:
        return {}, now, "missing_api_key" if not token else "quota_reserve"
    active = read_value(db,"active_request")
    if active and now-instant(active) < timedelta(seconds=45):
        return {}, now, "request_in_flight"
    last = read_value(db,"last_attempt")
    spacing = timedelta(minutes=1 if finalizing else PROTOCOL["poll_minutes"])
    if last and now-instant(last) < spacing:
        return {}, now, "recent_attempt"
    write_value(db,"active_request",now.isoformat())
    write_value(db,"last_attempt",now.isoformat())
    write_value(db,month,remaining-1)
    db.commit()  # reserve quota even if the process dies during the HTTP request
    events, reason, reported = [], "provider_unavailable", None
    try:
        res = get("https://api.the-odds-api.com/v4/sports/soccer_epl/odds",
                  params={"apiKey":token,"regions":"uk","markets":"h2h","oddsFormat":"decimal"},timeout=10)
        quota_header = res.headers.get("x-requests-remaining")
        if quota_header is not None and str(quota_header).isdigit():
            reported = max(0,int(quota_header))
        res.raise_for_status()
        events = res.json()
        if isinstance(events,list):
            reason = "event_missing"
        else:
            events = []
    except (requests.RequestException,ValueError):
        pass
    received = clock()
    db.execute("BEGIN IMMEDIATE")
    if read_value(db,"active_request") == now.isoformat():
        write_value(db,"active_request",None)
    if reported is not None:
        write_value(db,month,min(reported,read_value(db,month)))
    return _index_events(events), received, reason


def _remember_quote(db, key, event, received):
    if event is None:
        return False
    try:
        raw = canonical(event)
    except (TypeError,ValueError,OverflowError):
        return False
    db.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?)",
               (received.isoformat(),event["id"],raw))
    consensus = market_consensus(event,received)
    if consensus is None:
        return False
    db.execute("""INSERT INTO quote_candidates VALUES(?,?,?,?,?,?)
                  ON CONFLICT(fixture,kickoff,revision) DO UPDATE SET
                  received=excluded.received,event=excluded.event,consensus=excluded.consensus
                  WHERE julianday(excluded.received)>=julianday(quote_candidates.received)""",
               (*key,received.isoformat(),event["id"],canonical(consensus)))
    return True


def _reason(db, key, received, reason):
    db.execute("""INSERT INTO captures(fixture,kickoff,revision,received,reason) VALUES(?,?,?,?,?)
                  ON CONFLICT(fixture,kickoff,revision) DO UPDATE SET
                  reason=excluded.reason, received=excluded.received WHERE captures.payload IS NULL""",
               (*key,received.isoformat(),reason))


def collect(matches, predict, root=None, *, clock=lambda: datetime.now(UTC), get=requests.get):
    """Sample a shared league feed, then freeze once with a two-minute witness budget."""
    from .data import alias
    now = clock()
    with database(root) as db:
        db.execute("BEGIN IMMEDIATE")
        reg = read_value(db, "registration")
        if not reg:
            return {"status":"register_model_first","captured":0}
        if digest(reg["protocol"]) != digest(PROTOCOL):
            return {"status":"protocol_changed","captured":0}
        _record_missed(db,matches,now,instant(reg["created"]))
        due = [(t,m) for t,m in scheduled(matches,now) if not db.execute(
            "SELECT 1 FROM captures WHERE fixture=? AND kickoff=? AND revision=? AND payload IS NOT NULL",
            _fixture_key(m)).fetchone()]
        if not due:
            return {"status":"no_due_fixtures","captured":0}
        lead = timedelta(minutes=PROTOCOL["freeze_lead_minutes"])
        finalizing = any(now >= t-lead for t,_ in due)
        events, received, reason = _poll(db,now,finalizing,clock,get)
        if reason == "request_in_flight":
            return {"status":reason,"captured":0}
        count, why = 0, reason
        for cutoff, match in due:
            key = _fixture_key(match)
            if db.execute("SELECT 1 FROM captures WHERE fixture=? AND kickoff=? AND revision=? AND payload IS NOT NULL",
                          key).fetchone():
                continue  # another process finished while the HTTP request was in flight
            schedule_seen = match.get("schedule_observed_at")
            if not schedule_seen or instant(schedule_seen) > received:
                why = "schedule_unverified"
                _reason(db,key,received,why)
                continue
            if received > cutoff:
                why = "late_receipt"
                _reason(db,key,received,why)
                continue
            event = events.get((alias(match["home"]),alias(match["away"]),instant(key[1])))
            good = _remember_quote(db,key,event,received)
            why = "collecting_quotes" if good else "insufficient_books" if event else reason
            if received < cutoff-lead:
                _reason(db,key,received,why)
                continue
            candidate = db.execute("""SELECT q.received,q.consensus,o.raw FROM quote_candidates q
                JOIN observations o ON o.received=q.received AND o.event=q.event
                WHERE q.fixture=? AND q.kickoff=? AND q.revision=?""",key).fetchone()
            observed = instant(candidate["received"]) if candidate else received
            market = json.loads(candidate["consensus"]) if candidate else None
            raw = candidate["raw"] if candidate else None
            if observed < instant(schedule_seen) or not cutoff-timedelta(minutes=15) <= observed <= received:
                why = "unverified_observation"
                _reason(db,key,received,why)
                continue
            result = predict(match,observed,reg["model"])
            frozen = clock()
            if frozen > cutoff or frozen < observed:
                why = "late_generation"
                _reason(db,key,received,why)
                continue
            if not result or result["model_version"] != reg["model"]:
                why = "model_version_changed"
                _reason(db,key,received,why)
                continue
            probs = result["probs"]
            payload = {"id":key[0],"season":match["season"],"kickoff":key[1],
                       "cutoff":cutoff.isoformat(),"observed":observed.isoformat(),
                       "frozen":frozen.isoformat(),"model_version":result["model_version"],
                       "protocol_hash":digest(reg),"schedule_observed_at":schedule_seen,
                       "schedule_revision":key[2],"a":probs,"market":market,
                       "b":[.8*m+.2*p for m,p in zip(market,probs)] if market else None,
                       "prediction":result}
            why = "paired" if market else why
            db.execute("""INSERT INTO captures(fixture,kickoff,revision,received,reason,payload,hash,raw)
                          VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(fixture,kickoff,revision) DO UPDATE SET
                          received=excluded.received,reason=excluded.reason,payload=excluded.payload,
                          hash=excluded.hash,raw=excluded.raw
                          WHERE captures.payload IS NULL""",
                       (*key,observed.isoformat(),why,canonical(payload),digest(payload),raw))
            count += 1
        write_value(db,"last_capture",{"at":received.isoformat(),"status":why,"fixtures":len(due)})
        return {"status":why,"captured":count}
