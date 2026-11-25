"""CC0 English Premier League match data and versioned schedule observations."""
from __future__ import annotations

import calendar
import csv
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ASSETS = Path(__file__).parent / "assets" / "data"
PIN = "9ee7a0c351805c99cc334478a9bf04b7d0432fec"
UPSTREAM = "https://raw.githubusercontent.com/openfootball/england"
HISTORY = tuple(f"{year}-{str(year + 1)[-2:]}" for year in range(2000, 2026))
LONDON = ZoneInfo("Europe/London")
FIELDS = ["id", "season", "round", "date", "kickoff_utc", "home", "away", "hg", "ag"]
ALIASES = {
    "Arsenal": ("阿森纳", "兵工厂"), "Aston Villa": ("阿斯顿维拉", "维拉"),
    "Bournemouth": ("伯恩茅斯", "AFC Bournemouth"), "Brentford": ("布伦特福德",),
    "Brighton & Hove Albion": ("布莱顿", "Brighton", "Brighton and Hove Albion"),
    "Chelsea": ("切尔西",), "Crystal Palace": ("水晶宫",), "Everton": ("埃弗顿",),
    "Fulham": ("富勒姆",), "Ipswich Town": ("伊普斯维奇", "Ipswich"),
    "Leeds United": ("利兹联", "Leeds"), "Liverpool": ("利物浦",),
    "Manchester City": ("曼城", "曼彻斯特城", "Man City"),
    "Manchester United": ("曼联", "曼彻斯特联", "Man Utd", "Man United"),
    "Newcastle United": ("纽卡斯尔联", "纽卡斯尔", "纽卡", "Newcastle"),
    "Nottingham Forest": ("诺丁汉森林", "Nott'm Forest", "Nottm Forest"),
    "Sunderland": ("桑德兰",), "Tottenham Hotspur": ("托特纳姆热刺", "热刺", "Tottenham", "Spurs"),
    "West Ham United": ("西汉姆联", "西汉姆", "West Ham"),
    "Wolverhampton Wanderers": ("狼队", "伍尔弗汉普顿", "Wolves"),
    "Hull City": ("赫尔城",), "Coventry City": ("考文垂",), "Burnley": ("伯恩利",),
    "Southampton": ("南安普顿",), "Leicester City": ("莱斯特城", "Leicester"),
    "Sheffield United": ("谢菲尔德联", "Sheffield Utd"), "Luton Town": ("卢顿", "Luton"),
    "Watford": ("沃特福德",), "Norwich City": ("诺维奇", "Norwich"),
    "West Bromwich Albion": ("西布朗", "West Brom"), "Cardiff City": ("卡迪夫城",),
    "Huddersfield Town": ("哈德斯菲尔德",), "Stoke City": ("斯托克城", "Stoke"),
    "Swansea City": ("斯旺西",), "Middlesbrough": ("米德尔斯堡",),
    "Queens Park Rangers": ("女王公园巡游者", "QPR"), "Blackburn Rovers": ("布莱克本",),
    "Bolton Wanderers": ("博尔顿",), "Wigan Athletic": ("维冈竞技",),
    "Birmingham City": ("伯明翰",), "Blackpool": ("布莱克浦",), "Portsmouth": ("朴茨茅斯",),
    "Reading": ("雷丁",), "Derby County": ("德比郡",), "Charlton Athletic": ("查尔顿",),
    "Bradford City": ("布拉德福德",),
}
DISPLAY_ZH = {name: values[0] for name, values in ALIASES.items()}


def _normal(name):
    return " ".join(re.sub(r"\s+(?:FC|AFC)$", "", name, flags=re.I).strip().casefold().split())


_NAMES = {_normal(label): name for name, variants in ALIASES.items() for label in (name, *variants)}


def alias(name: str) -> str:
    return _NAMES.get(_normal(name), re.sub(r"\s+(?:FC|AFC)$", "", name.strip(), flags=re.I))


def fixture_id(season, home, away):
    def slug(name):
        return re.sub(r"[^a-z0-9]+", "-", alias(name).lower()).strip("-")
    return f"{season}_{slug(home)}_{slug(away)}"


def _utc(moment) -> str:
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("Observation times require an explicit timezone")
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


_MONTH = {name: i for i, name in enumerate(" Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" "))}
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_DATE = re.compile(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\w{3}) (\d{1,2})(?: (\d{4}))?")
_SCORE = r"(\d+)-(\d+)(?:\s+\(\d+-\d+\))?"
_OLD = re.compile(r"(.+?)\s+" + _SCORE + r"\s+(.+?)")
_NEW = re.compile(r"(.+?)\s+v\s+(.+?)(?:\s+" + _SCORE + r")?")


def parse_season(content: str, season: str):
    """Parse the two Football.TXT fixture layouts. A heading resets the grouped kickoff."""
    if not re.fullmatch(r"20\d\d-\d\d", season):
        raise ValueError("Invalid EPL season")
    year = int(season[:4])
    rows = []
    matchday = day = time = None
    for number, line in enumerate(content.splitlines(), start=1):
        line = line.strip().lstrip("\ufeff")
        heading = re.fullmatch(r"▪ (?:Matchday|Regular Season -) (\d+)", line)
        if heading:
            matchday, day, time = int(heading[1]), None, None
            continue
        heading = _DATE.fullmatch(line)
        if heading:
            dow, month, dom, explicit = heading.groups()
            years = (int(explicit),) if explicit else (year, year + 1)
            possible = [date(y, _MONTH[month], int(dom)) for y in years
                        if int(dom) <= calendar.monthrange(y, _MONTH[month])[1]]
            day = next((d for d in possible if _DAYS[d.weekday()] == dow), None)
            if day is None:
                raise ValueError(f"{season}:{number} date/weekday mismatch")
            time = None
            continue
        if not day or not line or line.startswith(("(", "#")):
            continue
        postponed = re.search(r"\s+\[(?:postponed|ppd|abandoned|time tbd)\]\s*$", line, re.I)
        if postponed:
            line = line[:postponed.start()]
        clock = re.match(r"^(\d\d:\d\d)\s+", line)
        if clock:
            time, line = clock[1], line[clock.end():]
        fixture = _NEW.fullmatch(line)
        if fixture:
            home, away, hg, ag = fixture.groups()
        else:
            fixture = _OLD.fullmatch(line)
            if not fixture:
                continue  # multi-line goal scorer notes
            home, hg, ag, away = fixture.groups()
        home, away = alias(home), alias(away)
        if not matchday or home == away:
            raise ValueError(f"{season}:{number} invalid fixture")
        kickoff = None
        if time and not postponed:
            kickoff = datetime.fromisoformat(f"{day}T{time}").replace(tzinfo=LONDON)
        rows.append({"id": fixture_id(season, home, away), "season": season,
                     "date": day.isoformat(), "round": matchday, "kickoff_utc": _utc(kickoff) if kickoff else None,
                     "home": home, "away": away, "hg": int(hg) if hg else None, "ag": int(ag) if ag else None})
    return rows


def coverage(rows, season):
    from collections import Counter
    counts = Counter(row["id"] for row in rows)
    teams = sorted({row[key] for row in rows for key in ("home", "away")})
    missing = [fixture_id(season, h, a) for h in teams for a in teams
               if h != a and fixture_id(season, h, a) not in counts]
    return {"season": season, "expected": 380, "fixtures": len(counts), "rows": len(rows),
            "teams": len(teams), "completed": sum(r["hg"] is not None for r in rows),
            "duplicates": [k for k, n in counts.items() if n > 1], "missing": missing}


def apply_explicit_offsets(rows, full_text):
    """2025/26 short file mixes clocks; join its companion's explicit UTC offsets by fixture."""
    by_pair = {(r["home"], r["away"]): r for r in rows}
    seen = set()
    lines = full_text.splitlines()
    for i, line in enumerate(lines):
        event = re.match(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\w{3}) (\d+) (\d\d:\d\d) UTC([+-]\d+) ", line)
        if not event:
            continue
        fixture = _NEW.fullmatch(lines[i + 1].strip())
        pair = (alias(fixture[1]), alias(fixture[2])) if fixture else None
        if pair not in by_pair or pair in seen:
            raise ValueError(f"Invalid explicit offset record at line {i + 1}")
        row = by_pair[pair]
        day = date.fromisoformat(row["date"])
        if (day.month, day.day) != (_MONTH[event[1]], int(event[2])):
            raise ValueError(f"Date disagreement for {pair}")
        local = datetime.fromisoformat(f"{day}T{event[3]}")
        row["kickoff_utc"] = _utc(local.replace(tzinfo=timezone(timedelta(hours=int(event[4])))))
        seen.add(pair)
    if len(seen) != len(rows):
        raise ValueError("Incomplete explicit kickoff offsets")


def rebuild(checkout, destination=ASSETS):
    """Rebuild from a checkout matching the pinned raw file hashes (or bootstrap them)."""
    checkout, destination = Path(checkout), Path(destination)
    expected = read_coverage() if (ASSETS / "coverage.json").exists() else {}
    all_rows, reports = [], []
    for season in (*HISTORY, "2026-27"):
        relative = f"{season}/1-premierleague.txt"
        raw = (checkout / relative).read_bytes().replace(b"\r\n", b"\n")
        digest = hashlib.sha256(raw).hexdigest()
        old = next((s for s in expected.get("seasons", []) if s["season"] == season), None)
        if old and digest != old["source_sha256"]:
            raise ValueError(f"Source differs from pin: {relative}")
        rows = parse_season(raw.decode("utf-8-sig"), season)
        report = coverage(rows, season)
        if season == "2025-26":
            full_path = f"{season}/1-premierleague-full.txt"
            full = (checkout / full_path).read_bytes().replace(b"\r\n", b"\n")
            full_hash = hashlib.sha256(full).hexdigest()
            if old and old.get("offsets_sha256", full_hash) != full_hash:
                raise ValueError("Explicit offset source differs from pin")
            apply_explicit_offsets(rows, full.decode("utf-8-sig"))
            report.update(offsets_file=full_path, offsets_sha256=full_hash)
        if (report["teams"] != 20 or report["duplicates"] or report["missing"] or
                (season in HISTORY and report["completed"] != 380)):
            raise ValueError(f"Season validation failed: {report}")
        reports.append(dict(report, file=relative, source_sha256=digest))
        all_rows.extend(rows)
    destination.mkdir(exist_ok=True, parents=True)
    with (destination / "matches.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(all_rows, key=lambda r: (r["date"], r["kickoff_utc"] or "", r["id"])))
    (destination / "coverage.json").write_text(json.dumps({
        "upstream": "https://github.com/openfootball/england", "license": "CC0-1.0",
        "source_commit": PIN, "seasons": reports,
        "csv_sha256": hashlib.sha256((destination / "matches.csv").read_bytes()).hexdigest(),
    }, indent=2) + "\n", encoding="utf-8")
    return reports


def read_coverage():
    return json.loads((ASSETS / "coverage.json").read_text(encoding="utf-8"))


def _bundled():
    with (ASSETS / "matches.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            for field in ("round", "hg", "ag"):
                row[field] = int(row[field]) if row[field] != "" else None
            row["kickoff_utc"] = row["kickoff_utc"] or None
            yield row


@lru_cache(maxsize=1)
def _base():
    return {row["id"]: row for row in _bundled()}


def _versions(db, as_of):
    return db.execute("""
        SELECT v.payload FROM fixture_versions v JOIN (
            SELECT id, MAX(observed) AS observed FROM fixture_versions
            WHERE observed <= ? GROUP BY id
        ) cut ON v.id=cut.id AND v.observed=cut.observed
    """, (as_of,))


def load_matches(db_path=None, *, as_of=None):
    """Return bundled reconstruction plus latest observed local revisions at the cutoff.

    Rows without schedule_observed_at are reconstruction only. Scores in the bundled
    archive are final labels; prediction code must first apply its result-time filter.
    """
    rows = {key: dict(value) for key, value in _base().items()}
    if db_path and Path(db_path).is_file():
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='fixture_versions'").fetchone()
            if exists:
                cutoff = _utc(as_of) if as_of is not None else "9999"
                for (raw,) in _versions(db, cutoff):
                    row = json.loads(raw)
                    if row.get("withdrawn"):
                        rows.pop(row["id"], None)
                    else:
                        rows[row["id"]] = row
    return sorted(rows.values(), key=lambda r: (r["date"], r["kickoff_utc"] or "", r["id"]))


def _get(url):
    req = Request(url, headers={"User-Agent": "epl-prediction-agent/0.1"})
    with urlopen(req, timeout=20) as response:
        return response.read(5_000_001)


def record_observation(db_path, season, rows, *, source_commit, observed):
    """Store one complete upstream view atomically; keep historical revisions readable."""
    received = _utc(observed)
    report = coverage(rows, season)
    if report["duplicates"] or report["teams"] != 20 or report["fixtures"] != 380:
        raise ValueError("Incomplete or duplicate-fixture upstream season; previous schedule retained")
    if not all(r["season"] == season for r in rows):
        raise ValueError("Mixed seasons in observation")
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=10)) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS fixture_versions (
                id TEXT NOT NULL, observed TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (id, observed)
            );
            CREATE TABLE IF NOT EXISTS season_syncs (
                season TEXT NOT NULL, observed TEXT NOT NULL, source_commit TEXT NOT NULL,
                changed INTEGER NOT NULL, PRIMARY KEY(season, observed)
            );
        """)
        db.execute("BEGIN IMMEDIATE")
        latest = db.execute("SELECT MAX(observed) FROM season_syncs WHERE season=?", (season,)).fetchone()[0]
        if latest and latest > received:
            raise ValueError("A more recent schedule observation is already committed")
        previous = {}
        for (raw,) in _versions(db, received):
            item = json.loads(raw)
            if item["season"] == season:
                previous[item["id"]] = item
        changed = 0
        known = {k: v for k, v in _base().items() if v["season"] == season}
        known.update(previous)
        published_ids = {r["id"] for r in rows}
        for key in known.keys() - published_ids:
            old = known[key]
            if old.get("withdrawn"):
                continue
            if latest == received:
                raise ValueError("Conflicting observations at the same timestamp")
            withdrawn = dict(old, withdrawn=True, observed_at=received, schedule_observed_at=received,
                             schedule_revision=old.get("schedule_revision", 0) + 1,
                             source_commit=source_commit, revised_fields=["withdrawn"])
            db.execute("INSERT INTO fixture_versions VALUES(?,?,?)",
                       (key, received, json.dumps(withdrawn, ensure_ascii=False)))
            changed += 1
        for source in rows:
            row = dict(source)
            old = previous.get(row["id"])
            if old and not old.get("withdrawn") and all(row[k] == old.get(k) for k in FIELDS):
                continue
            if latest == received:
                raise ValueError("Conflicting observations at the same timestamp")
            schedule_changed = (old is None or old.get("withdrawn") or
                                any(row[k] != old[k] for k in ("date", "kickoff_utc", "round")))
            result_changed = old is None or (row["hg"], row["ag"]) != (old["hg"], old["ag"])
            row.update(observed_at=received, source_commit=source_commit,
                       schedule_revision=(old["schedule_revision"] if old else 0) + int(schedule_changed),
                       schedule_observed_at=received if schedule_changed else old["schedule_observed_at"],
                       result_observed_at=(received if result_changed or old.get("withdrawn") else old["result_observed_at"])
                       if row["hg"] is not None else None)
            row["revised_fields"] = [k for k in FIELDS if old and old[k] != row[k]]
            db.execute("INSERT INTO fixture_versions VALUES(?,?,?)",
                       (row["id"], received, json.dumps(row, ensure_ascii=False)))
            changed += 1
        if changed == 0 and latest == received:
            db.rollback()
        else:
            db.execute("INSERT INTO season_syncs VALUES(?,?,?,?)", (season, received, source_commit, changed))
            db.commit()
    return {"season": season, "upstream": source_commit, "observed_at": received,
            "fixtures": report["fixtures"], "completed": report["completed"], "changed": changed}


def sync(db_path, *, season=None):
    """Fetch the latest free openfootball EPL file(s) under one immutable Git commit."""
    today = datetime.now(LONDON).date()
    first_year = today.year if today.month >= 7 else today.year - 1
    season = season or f"{first_year}-{str(first_year + 1)[-2:]}"
    if not re.fullmatch(r"20\d\d-\d\d", season):
        raise ValueError("Use a season like 2026-27")
    source = json.loads(_get("https://api.github.com/repos/openfootball/england/commits/master"))["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Invalid upstream revision")
    url = f"{UPSTREAM}/{source}/{season}"
    body = _get(f"{url}/1-premierleague.txt")
    if len(body) > 5_000_000:
        raise ValueError("Unexpected season size")
    rows = parse_season(body.decode("utf-8-sig"), season)
    if season == "2025-26":
        apply_explicit_offsets(rows, _get(f"{url}/1-premierleague-full.txt").decode("utf-8-sig"))
    return record_observation(db_path, season, rows, source_commit=source, observed=datetime.now(timezone.utc))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Rebuild pinned CC0 match data")
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--output", type=Path, default=ASSETS)
    args = parser.parse_args()
    for item in rebuild(args.checkout, args.output):
        print(item["season"], item["fixtures"], item["completed"])
