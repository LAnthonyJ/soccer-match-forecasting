"""Shared snapshot adapter for the CLI, Flask UI, questions and static export."""
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import data, models
from .evaluation import formal_report, public_ledger
from .market import instant, root_dir

UTC = timezone.utc
STATIC = Path(__file__).with_name("static")


def fixtures():
    db = root_dir() / "fixtures.sqlite3"
    return data.load_matches(db if db.exists() else None)


def model_dir():
    custom = root_dir() / "models"
    pointer = custom / "ACTIVE"
    if pointer.is_file():
        key = pointer.read_text("ascii").strip()
        if not re.fullmatch(r"[a-f0-9]{64}",key):
            raise ValueError("Invalid local model build identifier")
        return custom / key
    return custom if (custom / ".complete").is_file() else None


@lru_cache(maxsize=3)
def cached_history(directory, stamp):
    return models.load_replays(directory), models.load_report(directory)


def history():
    directory = model_dir()
    stamp = (directory/".complete").stat().st_mtime_ns if directory else 0
    return cached_history(directory, stamp)


def format_prediction(raw, source, market=None, b=None):
    return {"a":raw["probs"],"market":market,"b":b,"xg":raw["xg"],
            "top_scores":[[s["home"],s["away"],s["prob"]] for s in raw["top_scores"]],
            "samples":[raw["samples"][k] for k in ("home","away")],"form":raw.get("form",{}),
            "model_version":raw["model_version"],"cutoff":raw["cutoff"],"source":source}


def item(match, raw=None, kind="replay", market=None, b=None):
    return {**{k:match.get(k) for k in ("id","season","date","kickoff_utc","round","home","away","hg","ag")},
            "home_zh":data.DISPLAY_ZH.get(match["home"],match["home"]),
            "away_zh":data.DISPLAY_ZH.get(match["away"],match["away"]),
            "prediction":format_prediction(raw,kind,market,b) if raw else None}


def dashboard(season=None, *, static=False, all_seasons=False):
    now = datetime.now(UTC)
    matches = fixtures()
    years = sorted({m["season"] for m in matches})
    if season and season not in years:
        raise ValueError("未知赛季")
    selected = years if all_seasons else [season] if season else years[-(2 if static else 3):]
    ledger = public_ledger()
    index = {(r["id"],r["kickoff"],r["revision"]):r["payload"] for r in ledger["records"] if r["payload"]}
    replays, scores = history()
    output = []
    for m in matches:
        if m["season"] not in selected:
            continue
        frozen = index.get((m["id"],m["kickoff_utc"],m.get("schedule_revision",0)))
        if frozen:
            output.append(item(m,frozen["prediction"],"frozen",frozen["market"],frozen["b"]))
        else:
            replay = replays.get(m["id"])
            output.append(item(m,replay if m["hg"] is not None and replay and models.replay_matches(replay,m) else None))
    # Choose a replay with actual model output; the upcoming week stays browsable.
    example = next((r for r in reversed(output) if r["prediction"] and r["hg"] is not None),None)
    report = formal_report(matches,ledger,now)
    report["history"] = scores
    latest = max((r for r in matches if r.get("observed_at")),key=lambda r:r["observed_at"],default=None)
    return {"fixtures":output,"report":report,"default_id":example["id"] if example else None,
            "available_seasons":selected if static else years,"updated_at":now.isoformat(),
            "data_version":latest["source_commit"] if latest else data.read_coverage()["source_commit"],
            "last_change_observed_at":latest["observed_at"] if latest else None,
            "last_scored_date":max((r["date"] for r in matches if r["hg"] is not None),default=None)}


def prediction(match_id):
    matches = fixtures()
    m = next((r for r in matches if r["id"]==match_id),None)
    if not m:
        raise KeyError(match_id)
    ledger = public_ledger()
    frozen = next((r["payload"] for r in ledger["records"] if r["id"]==match_id and
                   r["kickoff"]==m["kickoff_utc"] and r["revision"]==m.get("schedule_revision",0) and r["payload"]),None)
    if frozen:
        return item(m,frozen["prediction"],"frozen",frozen["market"],frozen["b"])
    if m["hg"] is not None:
        replay = history()[0].get(match_id)
        return item(m,replay if replay and models.replay_matches(replay,m) else None)
    now = datetime.now(UTC)
    if m["kickoff_utc"] and now < instant(m["kickoff_utc"])-timedelta(hours=24):
        return item(m,models.forecast(m,matches,now,model_dir=model_dir()),"preview")
    return item(m)


def ask(question, fixture_id=None, zone="Europe/London"):
    if not isinstance(question,str) or not 0<len(question.strip())<=500:
        raise ValueError("请输入 1–500 字的问题。")
    q = question.casefold().strip()
    def reply(answer,choices=()):
        return {"answer":answer,"choices":list(choices)}
    if any(x in q for x in ("伤","停赛","伤停","阵容","首发","injur","lineup")):
        return reply("目前缺少可核实的实时伤停和首发数据。模型使用赛前已知的历史赛果。")
    matches = fixtures()
    if any(x in q for x in ("过去","成绩","表现比","领先市场","赛季模型","战绩","beat the market")):
        report = formal_report(matches,public_ledger())
        requested = re.search(r"20\d\d-\d\d",q)
        season = requested.group() if requested else None
        if not season and ("过去一季" in q or "上赛季" in q):
            season = max((s for s in {m["season"] for m in matches} if sum(
                m["season"]==s and m["hg"] is not None for m in matches)==380),default=None)
        if season:
            summary = next((s for s in report["seasons"] if s["season"]==season),None)
            if not summary or not summary["paired"]:
                return reply(f"{season} 赛季尚无可核验的同场市场配对，无法判断该季是否领先。")
            return reply(f"{season} 赛季有 {summary['paired']} 场有效配对，融合模型和市场平均对数损失分别为 "
                         f"{summary['model_loss']:.4f}、{summary['market_loss']:.4f}。")
        status = {"accumulating":"正在积累样本","leading":"在预注册赛季和时点领先","unproven":"尚未证实领先"}
        return reply(f"正式评测有 {report['n_pairs']} 场可核验配对，目前{status[report['status']]}。")
    teams = set()
    for team,names in data.ALIASES.items():
        for word in (team,*names):
            if len(word)>1 and (re.search(r"(?<![a-z])"+re.escape(word.casefold())+r"(?![a-z])",q)):
                teams.add(team)
                break
    if "周末" in q or "weekend" in q or "下场" in q or "下一场" in q:
        try:
            tz = ZoneInfo(zone)
        except (ZoneInfoNotFoundError,ValueError,TypeError):
            tz = ZoneInfo("Europe/London")
        today = datetime.now(tz).date()
        sat = today+timedelta(days=(5-today.weekday())%7 if today.weekday()!=6 else -1)
        weekend = "周末" in q or "weekend" in q
        candidates = []
        for m in matches:
            md = instant(m["kickoff_utc"]).astimezone(tz).date() if m["kickoff_utc"] else date.fromisoformat(m["date"])
            if m["hg"] is None and (sat<=md<=sat+timedelta(days=1) if weekend else md>=today):
                candidates.append(m)
    else:
        candidates = matches
    if teams:
        candidates = [m for m in candidates if teams <= {m["home"],m["away"]}]
    selected = next((m for m in candidates if m["id"]==fixture_id),None)
    if not selected:
        if len(candidates)==1 or (candidates and ("下一场" in q or "下场" in q)):
            selected = min(candidates,key=lambda r:r["date"])
        else:
            ranked = sorted(candidates,key=lambda m:abs((date.fromisoformat(m["date"])-date.today()).days))
            return reply("请选择具体场次。" if ranked else "已收录的赛程中没有找到符合条件的比赛。",
                         [{"id":m["id"],"label":f"{m['date']} · {m['home']} – {m['away']}"} for m in ranked[:8]])
    game = prediction(selected["id"])
    name = f"{game['date']} {game['home_zh']} 对 {game['away_zh']}"
    if any(s in q for s in ("踢谁","对手","何时","时间","赛程","weekend")):
        return reply(f"{name}。开球时间（UTC）：{game['kickoff_utc'] or '待核实'}。")
    p = game["prediction"]
    if not p:
        return reply(f"{name} 暂无已核实的赛前预测记录。")
    if any(s in q for s in ("市场","分歧","赔率","market")):
        if not p["market"]:
            return reply(f"{name} 缺少合格的同期市场快照，无法计算市场分歧。")
        delta = [100*(a-b) for a,b in zip(p["b"],p["market"])]
        k = max(range(3),key=lambda i:abs(delta[i]))
        return reply(f"{name} 的最大分歧在{('主胜','平局','客胜')[k]}，融合模型相对市场 {delta[k]:+.1f} 个百分点。")
    a = p["a"]
    return reply(f"{name} 的独立模型给出主胜 {a[0]:.1%}、平局 {a[1]:.1%}、客胜 {a[2]:.1%}。"
                 f"比分模型估计进球为 {p['xg'][0]:.2f} 比 {p['xg'][1]:.2f}，"
                 f"主客队样本数为 {p['samples'][0]}、{p['samples'][1]}。数据截止 {p['cutoff']}。")
