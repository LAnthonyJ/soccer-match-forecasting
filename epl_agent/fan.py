"""Small matchday views shared by the live service and the static site."""
from collections import defaultdict
from datetime import datetime, timezone
import json
import re

from . import data, service
from .evaluation import public_ledger
from .market import instant


def round_file(week):
    number = re.fullmatch(r"第 (\d+) 轮",week)
    return "r"+number[1] if number else "w"+week.encode().hex()


def week_catalog(matches):
    weeks = defaultdict(set)
    for match in matches:
        weeks[match["season"]].add(service._week(match))
    def order(week):
        number = re.fullmatch(r"第 (\d+) 轮",week)
        return (0,int(number[1])) if number else (1,week)
    return {year:sorted(names,key=order) for year,names in sorted(weeks.items())}


def can_preview(game, now):
    return (game.get("hg") is None and game.get("ag") is None and not game.get("source_conflict")
            and not game.get("withdrawn") and game.get("schedule_status")=="verified"
            and game.get("kickoff_utc") and instant(game["kickoff_utc"])>now)


def match_card(game, now):
    card = {k:game.get(k) for k in ("id","season","round","date","kickoff_utc","home","away",
                                      "hg","ag","schedule_status")}
    for side in ("home","away"):
        card[side+"_zh"] = game.get(side+"_zh") or data.DISPLAY_ZH.get(game[side],game[side])
    if game.get("source_conflict"):
        card.update(source_conflict=True, hg=None, ag=None)
    if card["hg"] is None and card["schedule_status"]!="verified":
        card["kickoff_utc"] = None
    p = game.get("prediction")
    card["prediction"] = ({k:p.get(k) for k in ("source","a","cutoff")}
                          if p and can_preview(game,now) else None)
    card["partial"] = True
    return card


def board_payload(games, season, current, catalog, now, finished, public=False, fixed_url=None):
    week = service._week(current)
    return {"schema":"fan-board/v1","current_season":season,"current_week":week,"current_id":current["id"],
            "updated_at":now.isoformat(),"season_finished":finished,"week_catalog":catalog,
            "team_aliases":data.ALIASES,"public_snapshot":public,"fixed_snapshot_url":fixed_url,
            "fixtures":[match_card(m,now) for m in games if m["season"]==season and service._week(m)==week]}


def finished(matches):
    return bool(matches) and all(m.get("hg") is not None and m.get("ag") is not None
                                 and not m.get("source_conflict") for m in matches)


def _live_round(matches, season, week, now):
    rows = [m for m in matches if m["season"]==season and service._week(m)==week]
    if not rows:
        raise KeyError((season,week))
    upcoming = [m for m in rows if can_preview(m,now)]
    details = ({m["id"]:m for m in service._present(upcoming,matches,public_ledger(),now)}
               if upcoming else {})
    return [details.get(m["id"],m) for m in rows]


def live_board():
    now = datetime.now(timezone.utc)
    matches = service.fixtures()
    season = max(m["season"] for m in matches)
    active = [m for m in matches if m["season"]==season]
    fid = service._current_id(active,now)
    current = next(m for m in active if m["id"]==fid)
    games = _live_round(matches,season,service._week(current),now)
    return board_payload(games,season,current,week_catalog(matches),now,finished(active))


def live_round(season, week):
    now = datetime.now(timezone.utc)
    games = _live_round(service.fixtures(),season,week,now)
    return [match_card(m,now) for m in games]


def live_fixture(fid):
    matches = service.fixtures()
    match = next((m for m in matches if m["id"]==fid),None)
    if match is None:
        raise KeyError(fid)
    now = datetime.now(timezone.utc)
    if can_preview(match,now):
        return service.prediction(fid,matches=matches,now=now)
    return {**match_card(match,now),"partial":False}


def write_season(directory, season, games, now):
    round_dir = directory/"rounds"/season
    round_dir.mkdir(parents=True,exist_ok=True)
    fixture_dir = directory/"fixtures"
    fixture_dir.mkdir(exist_ok=True)
    groups = defaultdict(list)
    for game in games:
        card = match_card(game,now)
        groups[service._week(game)].append(card)
        detail = game if can_preview(game,now) else dict(card,partial=False)
        (fixture_dir/(game["id"]+".json")).write_text(json.dumps(detail,ensure_ascii=False,separators=(",",":")),encoding="utf8")
    for week,cards in groups.items():
        (round_dir/(round_file(week)+".json")).write_text(json.dumps(cards,ensure_ascii=False,separators=(",",":")),encoding="utf8")


def export_board(full, matches):
    now = instant(full["updated_at"])
    games = full["fixtures"]
    season = max(m["season"] for m in games)
    active = [m for m in games if m["season"]==season]
    current = next(m for m in active if m["id"]==full["current_id"])
    return board_payload(active,season,current,week_catalog(matches),now,finished(active),
                         full["public_snapshot"],full.get("fixed_snapshot_url"))
