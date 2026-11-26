"""Version-aware English Premier League results for supporter queries."""
from bisect import bisect_right
from collections import defaultdict
from datetime import timedelta, timezone
import json
from pathlib import Path
import re

from . import data, models


def _name(team):
    return data.DISPLAY_ZH.get(team, team)


class ResultIndex:
    """Use the model's observation/availability rules, with indexes ordered by playing time."""
    def __init__(self, archive, versions=()):
        self.source = models._Versions(archive, versions, archive)
        self.by_team = defaultdict(set)
        self.by_pair = defaultdict(set)
        self.by_season = defaultdict(set)
        self.available = {}
        self.received = {}
        self.order = {}
        self.tables = {}
        for row in [*archive, *versions]:
            key, home, away = row["id"], row["home"], row["away"]
            at = models._kickoff(row)
            self.available[id(row)] = models._available(row)
            self.received[id(row)] = (max(at,models._instant(row["result_observed_at"]))
                                      if row.get("result_observed_at") else self.available[id(row)])
            self.order[id(row)] = at
            self.by_season[row["season"]].add(key)
            for team in (home, away):
                self.by_team[team].add((at, key))
            self.by_pair[tuple(sorted((home, away)))].add((at, key))
        self.by_team = {k: sorted(rows) for k, rows in self.by_team.items()}
        self.by_pair = {k: sorted(rows) for k, rows in self.by_pair.items()}

    def previous(self, entries, cutoff, count, excluded, availability):
        before = bisect_right(entries, (cutoff, "\uffff"))
        results, missing = [], []
        for i in range(before - 1, -1, -1):
            at, key = entries[i]
            if key == excluded:
                continue
            row = self.source.at(key, cutoff)
            if row is None or self.order[id(row)] != at:
                continue  # withdrawn fixture or an earlier date for the same fixture
            if models._scored(row) and availability[id(row)] <= cutoff:
                results.append(row)
                if len(results) == count:
                    break
            elif at < cutoff:
                missing.append(key)
        return results, missing

    def table(self, season, cutoff, scope):
        cache_key = season, cutoff, scope
        availability = self.received if scope == "current" else self.available
        if cache_key in self.tables:
            return self.tables[cache_key]
        clubs, missing, confirmed = {}, [], 0
        for key in sorted(self.by_season.get(season, ())):
            row = self.source.at(key, cutoff)
            if not row:
                continue
            for team in (row["home"], row["away"]):
                if team not in clubs:
                    clubs[team] = dict(team=team, team_zh=_name(team), played=0, wins=0, draws=0,
                                       losses=0, gf=0, ga=0, gd=0, points=0)
            if not models._scored(row) or availability[id(row)] > cutoff:
                if self.order[id(row)] < cutoff:
                    missing.append(key)
                continue
            confirmed += 1
            for team, gf, ga in ((row["home"], row["hg"], row["ag"]), (row["away"], row["ag"], row["hg"])):
                club = clubs[team]
                club["played"] += 1
                club["gf"] += gf
                club["ga"] += ga
                club["gd"] += gf - ga
                outcome, points = ("wins", 3) if gf > ga else ("draws", 1) if gf == ga else ("losses", 0)
                club[outcome] += 1
                club["points"] += points
        def rank_key(club):
            return (-club["points"], -club["gd"], -club["gf"])
        rows = sorted(clubs.values(), key=lambda club: (*rank_key(club), club["team"]))
        previous = None
        for index, club in enumerate(rows, 1):
            order = rank_key(club)
            if order != previous:
                rank, previous = index, order
            club["rank"] = rank
        result = dict(season=season, confirmed=confirmed, missing=len(missing), missing_ids=missing, rows=rows)
        self.tables[cache_key] = result
        return result

    def context(self, game, stamp, scope):
        if scope not in {"prematch", "current"}:
            raise ValueError("Unknown history scope")
        batch = models._instant(stamp)
        if scope == "current":
            cutoff = batch
            local = cutoff.astimezone(data.LONDON)
            active_year = local.year - int(local.month < 7)
            seasons = sorted((s for s in self.by_season if int(s[:4]) <= active_year), reverse=True)
            season = next((s for s in seasons if any(self.source.at(k, cutoff) is not None
                                                    for k in self.by_season[s])), game["season"])
            excluded = None
        else:
            kickoff = models._kickoff(game)
            supplied = (game.get("prediction") or {}).get("cutoff")
            at = models._instant(supplied) if supplied else kickoff - timedelta(days=1)
            cutoff = min(at, batch, kickoff)
            season, excluded = game["season"], game["id"]
        table = self.table(season, cutoff, scope)
        availability = self.received if scope == "current" else self.available
        facts, recent, gaps = [], {}, {}
        def add(key, text):
            facts.append(dict(id=key, text=text, tool="fixture",
                              stage="prematch" if scope == "prematch" else "postmatch"))
        coverage = f"已确认 {table['confirmed']} 场，已过比赛时间待补或待核实 {table['missing']} 场。"
        for side in ("home", "away"):
            team = game[side]
            matches, pending = self.previous(self.by_team.get(team, ()), cutoff, 5, excluded, availability)
            recent[side] = [_result(row, team) for row in matches]
            gaps[side] = pending
            notes = [f"{r['date']}（{r['season']}）{'主' if r['venue']=='home' else '客'}对{r['opponent_zh']} "
                     f"{r['gf']}–{r['ga']} {r['outcome']}" for r in recent[side]]
            items = "；".join(notes) + "。" if notes else ""
            add(side+"_recent", f"{_name(team)}最近可核验的 {len(matches)} 场英超（比分本队在前）：{items}"
                + (f"期间另有 {len(pending)} 场待补或待核实。" if pending else ""))
            club = next((c for c in table["rows"] if c["team"] == team), None)
            if club:
                tied = sum(c["rank"] == club["rank"] for c in table["rows"]) > 1
                add(side+"_standing", f"{season} 按已确认比分计算，{_name(team)}{'并列' if tied else ''}第 {club['rank']}，"
                    f"{club['played']} 场 {club['wins']} 胜 {club['draws']} 平 {club['losses']} 负，"
                    f"进 {club['gf']} 球、失 {club['ga']} 球，比赛积分 {club['points']}。{coverage}未核对官方扣分与上诉。")
            else:
                add(side+"_standing", f"{season} 已收录的英超赛程中没有{_name(team)}的积分记录。{coverage}")
        names = sorted((game["home"], game["away"]))
        matches, pending = self.previous(self.by_pair.get(tuple(names), ()), cutoff, 1, excluded, availability)
        h2h = _result(matches[0], game["home"]) if matches else None
        if h2h:
            text = (f"最近一次已确认英超交锋：{h2h['date']}（{h2h['season']}），"
                    f"{_name(h2h['home'])} {h2h['hg']}–{h2h['ag']} {_name(h2h['away'])}。")
        else:
            text = "截至此时没有两队已确认的英超交锋比分。"
        add("h2h", text + (f"另有 {len(pending)} 场较近交锋待补或待核实。" if pending else ""))
        summary = "、".join(f"{c['rank']} {_name(c['team'])} {c['points']}分" for c in table["rows"])
        add("standings", f"{season} 按已确认比分计算。{coverage}未核对官方扣分与上诉。{summary}")
        return dict(fixture_id=game["id"], season=game["season"], scope=scope, as_of=cutoff.isoformat(),
                    home=game["home"], away=game["away"], home_zh=_name(game["home"]), away_zh=_name(game["away"]),
                    recent=recent, missing_recent=gaps, h2h=h2h, missing_h2h=pending, standings=table, facts=facts)


def _result(row, team):
    home = row["home"] == team
    opponent = row["away"] if home else row["home"]
    gf, ga = (row["hg"], row["ag"]) if home else (row["ag"], row["hg"])
    record = {key: row.get(key) for key in ("id", "season", "date", "kickoff_utc", "home", "away", "hg", "ag")}
    return dict(record, opponent=opponent, opponent_zh=_name(opponent), venue="home" if home else "away",
                gf=gf, ga=ga, outcome="胜" if gf > ga else "平" if gf == ga else "负",
                source=row.get("result_source") or "openfootball", observed_at=row.get("result_observed_at"))


def live_context(fid, scope="prematch", *, as_of=None):
    from . import service
    now = service.datetime.now(timezone.utc)
    game = service.prediction(fid, now=now)
    stamp = min(now, models._instant(as_of)) if as_of is not None else now
    return ResultIndex(**service.model_inputs()).context(game, stamp, scope)


def export_season(dest, season, games, index, stamp):
    if not re.fullmatch(r"\d{4}-\d{2}", season):
        raise ValueError("Invalid history season")
    by_home = defaultdict(list)
    for game in games:
        if not re.fullmatch(season+r"_[a-z0-9-]+_[a-z0-9-]+",game["id"]):
            raise ValueError("Invalid history fixture ID")
        by_home[game["id"].split("_")[1]].append(game)
    for scope in ("current", "prematch"):
        folder = Path(dest)/"team-history"/season/scope
        folder.mkdir(parents=True, exist_ok=True)
        for slug, fixtures in by_home.items():
            result = {g["id"]:index.context(g, stamp, scope) for g in fixtures}
            (folder/(slug+".json")).write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                                              encoding="utf8")
