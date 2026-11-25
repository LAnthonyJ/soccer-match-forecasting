"""Shared snapshot adapter for the CLI, Flask UI, questions and static export."""
from collections import Counter
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


def model_inputs():
    db = root_dir() / "fixtures.sqlite3"
    return {"archive": data.load_matches(), "versions": data.load_versions(db) if db.is_file() else []}


def historical_rows(*, snapshot=None):
    scores = _history_for(snapshot)[1]
    return scores.get("rows", [])


def _history_for(snapshot):
    # An independently verified export uses only repository-pinned historical artifacts.
    return cached_history(None, 0) if snapshot is not None else history()


def format_prediction(raw, source, market=None, b=None):
    return {"a": raw["probs"], "market": market, "b": b, "xg": raw["xg"],
            "top_scores": [[s["home"], s["away"], s["prob"]] for s in raw["top_scores"]],
            "samples": [raw["samples"][k] for k in ("home", "away")], "form": raw.get("form", {}),
            "model_version": raw["model_version"], "cutoff": raw["cutoff"], "source": source}


REASONS = {
    "missed_window": "错过赛前 24 小时采集窗口", "no_api_key": "采集机未配置赔率密钥",
    "missing_api_key": "采集机未配置赔率密钥", "quota_reserve": "本月额度已到预留线",
    "provider_unavailable": "报价供应商暂不可用", "event_missing": "该场暂无市场报价",
    "recent_attempt": "本轮轮询尚无合格报价", "request_in_flight": "正在请求本轮报价",
    "insufficient_books": "完整三项报价的合格书商不足三家",
    "schedule_unverified": "开球时间缺少赛前观测证据", "late_receipt": "报价收到时已超过截止时间",
    "late_generation": "预测生成时已超过截止时间", "rescheduled": "赛程已改期",
    "source_conflict": "两源赛果冲突，暂停结算", "no_witness": "缺少公开时间见证",
    "unverified_forecast": "赛前记录未通过核验", "no_matching_snapshot": "未找到同赛程版本的快照",
    "no_historical_market": "历史回放缺少同场、同时间的市场快照",
    "not_due": "尚未到达赛前采集窗口", "collecting_quotes": "正在采集合格报价",
    "pending_audit": "等待公开核验", "unknown_kickoff": "开球时间待核实",
    "missing_market": "缺少合格的同期市场报价", "no_event": "该场暂无市场报价",
}


def explain(game, preference=None):
    p = game["prediction"]
    if not p:
        return game.get("market_note") or "这场比赛暂无可核实的预测。"
    h, d, a = p["a"]
    heading = (f"独立模型 A 给出主胜 {h:.1%}、平局 {d:.1%}、客胜 {a:.1%}。"
               f"客胜比主胜{'高' if a >= h else '低'} {abs(a-h)*100:.1f} 个百分点。")
    if preference == "draw":
        heading = f"独立模型 A 的平局概率为 {d:.1%}，主胜和客胜分别为 {h:.1%}、{a:.1%}。"
    form = p["form"]
    recent = ""
    if form.get("home") and form.get("away"):
        descriptions = []
        for side in ("home", "away"):
            f = form[side]
            descriptions.append(f"{game[side+'_zh']}场均进 {f['gf']:.2f} 球、失 {f['ga']:.2f} 球、得 {f['points']:.2f} 分")
        recent = "近况按最近最多 8 场英超和 4 场联赛先验平滑，" + "；".join(descriptions) + "。"
    return (heading + recent + f"预计进球为 {p['xg'][0]:.2f} 比 {p['xg'][1]:.2f}。"
            f"球队累计样本为 {p['samples'][0]} 和 {p['samples'][1]} 场。数据截止 {p['cutoff']}。")


def market_text(p, independent=False):
    if not p or not p.get("market"):
        return "缺少合格的同期市场快照，无法计算市场分歧。"
    use_a = independent or p["b"] is None
    probs = p["a"] if use_a else p["b"]
    gaps = [v-market for v, market in zip(probs, p["market"])]
    k = max(range(3), key=lambda i: abs(gaps[i]))
    return (f"{'独立模型 A' if use_a else '融合模型 B'} 与市场的最大分歧在"
            f"{('主胜','平局','客胜')[k]}，相对市场 {gaps[k]*100:+.1f} 个百分点。")


def item(match, raw=None, kind="replay", market=None, b=None, *, reason=None, evidence=None):
    row = {k:match.get(k) for k in ("id","season","date","kickoff_utc","round","home","away","hg","ag","result_source","source_conflict")}
    row["home_zh"] = data.DISPLAY_ZH.get(match["home"], match["home"])
    row["away_zh"] = data.DISPLAY_ZH.get(match["away"], match["away"])
    row["prediction"] = format_prediction(raw,kind,market,b) if raw else None
    row["market_reason"] = reason
    row["market_note"] = REASONS.get(reason, reason or "")
    row["evidence"] = evidence
    if raw:
        row["explanation"] = explain(row)
        row["draw_explanation"] = explain(row,"draw")
        row["market_explanation"] = market_text(row["prediction"])
        row["independent_market_explanation"] = market_text(row["prediction"],True)
    return row


def _present(targets, matches, ledger, now, snapshot=None):
    replays = _history_for(snapshot)[0]
    captures = {(r["id"], r["kickoff"], r["revision"]):r for r in ledger["records"]}
    previous = {r["id"] for r in ledger["records"]}
    games, previews, rebuild = {}, [], []
    reasons = {}
    for m in targets:
        key, kickoff = m["id"], m.get("kickoff_utc")
        record = captures.get((key, kickoff, m.get("schedule_revision",0)), {})
        frozen = record.get("payload")
        if not kickoff:
            games[key] = item(m, reason="unknown_kickoff")
            continue
        if frozen:
            games[key] = item(m, frozen["prediction"], "frozen", frozen["market"], frozen["b"],
                              reason=record.get("reason") if not frozen["market"] else None, evidence=record.get("evidence"))
            continue
        cutoff = instant(kickoff) - timedelta(days=1)
        replay = replays.get(key)
        due = now >= cutoff
        reason = record.get("reason") or ("rescheduled" if key in previous else None)
        if due and replay and models.replay_matches(replay,m):
            games[key] = item(m,replay,reason=reason or "no_historical_market")
            continue
        reasons[key] = reason or ("missed_window" if due else "not_due")
        games[key] = item(m,reason=reasons[key])
        if m["hg"] is None or not replay or m.get("observed_at"):
            (rebuild if due else previews).append(m)
    if previews or rebuild:
        inputs = model_inputs() if snapshot is None else {"archive":data.load_matches(), "versions":snapshot["versions"]}
        directory = model_dir() if snapshot is None else None
        for batch, clock, kind in ((previews, now, "preview"), (rebuild, None, "reconstruction")):
            if not batch:
                continue
            preds = models.forecast_many(batch, matches, clock, model_dir=directory, **inputs)
            for m in batch:
                if m["id"] in preds:
                    games[m["id"]] = item(m,preds[m["id"]],kind,reason=reasons[m["id"]])
    return [games[m["id"]] for m in targets]


def _current_id(games, now):
    season = max((m["season"] for m in games), default=None)
    scheduled = [m for m in games if m["season"] == season and m["kickoff_utc"]]
    future = [m for m in scheduled if instant(m["kickoff_utc"]) >= now]
    chosen = min(future,key=lambda m:m["kickoff_utc"]) if future else max(scheduled,key=lambda m:m["kickoff_utc"],default=None)
    return chosen["id"] if chosen else None


def dashboard(season=None, *, static=False, all_seasons=False, snapshot=None):
    now = instant(snapshot["as_of"]) if snapshot is not None else datetime.now(UTC)
    if snapshot is None:
        matches, ledger = fixtures(), public_ledger()
    else:
        # Package older seasons with unchanged CC0 records. The public season list is authoritative.
        supplied = snapshot["fixtures"]
        seasons_in_snapshot = {m["season"] for m in supplied}
        matches = [m for m in data.load_matches() if m["season"] not in seasons_in_snapshot] + supplied
        ledger = snapshot["ledger"]
    years = sorted({m["season"] for m in matches})
    if season and season not in years:
        raise ValueError("未知赛季")
    selected = years if all_seasons else [season] if season else years[-(2 if static else 3):]
    targets = sorted((m for m in matches if m["season"] in selected),key=lambda m:(m["date"],m["kickoff_utc"] or "",m["id"]))
    output = _present(targets,matches,ledger,now,snapshot)
    example = next((r for r in reversed(output) if r["prediction"] and r["prediction"]["source"]=="replay" and r["hg"] is not None),None)
    report = formal_report(matches,ledger,now)
    scores = _history_for(snapshot)[1]
    report["history"] = {k:v for k,v in scores.items() if k != "rows"}
    health_season = season or years[-1]
    if snapshot is None:
        health = data.season_health(root_dir()/"fixtures.sqlite3",health_season,matches=matches,now=now)
    else:
        health = snapshot["seasons_health"].get(health_season) or snapshot["health"]
    complete = [s for s,count in Counter(m["season"] for m in matches if m["hg"] is not None and not m.get("source_conflict")).items() if count == 380]
    latest = max((r for r in matches if r.get("observed_at")),key=lambda r:r["observed_at"],default={})
    return {"fixtures":output, "report":report, "default_id":example["id"] if example else _current_id(output,now),
            "current_id":_current_id(output,now), "available_seasons":selected if static else years,
            "updated_at":now.isoformat(), "health":health, "last_complete_season":max(complete,default=None), "archive_seasons":years,
            "data_version":latest.get("source_commit") or data.read_coverage()["source_commit"],
            "last_change_observed_at":latest.get("observed_at"), "last_scored_date":health.get("last_scored_date"),
            "team_aliases":data.ALIASES, "public_snapshot":snapshot is not None}


def export_history_assets(directory, dashboard_dict, *, snapshot=None):
    directory = Path(directory)
    (directory/"history-rows.json").write_text(json.dumps(historical_rows(snapshot=snapshot),ensure_ascii=False,separators=(",",":")),encoding="utf8")
    # Older history remains addressable without putting every season in the initial download.
    included = set(dashboard_dict["available_seasons"])
    older = [m for m in data.load_matches() if m["season"] not in included]
    replays = _history_for(snapshot)[0]
    season_dir = directory/"seasons"
    season_dir.mkdir(exist_ok=True)
    for season in sorted({m["season"] for m in older}):
        rows = []
        for m in older:
            if m["season"] == season:
                raw = replays.get(m["id"])
                rows.append(item(m,raw if raw and m["kickoff_utc"] and models.replay_matches(raw,m) else None,reason="no_historical_market"))
        (season_dir/(season+".json")).write_text(json.dumps(rows,ensure_ascii=False,separators=(",",":")),encoding="utf8")


def prediction(match_id):
    matches = fixtures()
    m = next((r for r in matches if r["id"] == match_id),None)
    if not m:
        raise KeyError(match_id)
    return _present([m], matches, public_ledger(), datetime.now(UTC))[0]


def ask(question, fixture_id=None, zone="Europe/London"):
    if not isinstance(question,str) or not 0<len(question.strip())<=500:
        raise ValueError("请输入 1–500 字的问题。")
    q = question.casefold().strip()
    def reply(answer,choices=()):
        return {"answer":answer,"choices":list(choices)}
    if any(x in q for x in ("伤","停赛","伤停","阵容","首发","injur","lineup")):
        return reply("目前缺少可核实的实时伤停和首发数据。模型使用赛前已知的历史赛果。")
    matches = fixtures()
    if any(x in q for x in ("过去","上赛季","成绩","表现比","领先市场","赛季模型","战绩","beat the market")):
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
        return reply(f"{name} 暂无已核实的赛前预测。{game['market_note']}。")
    if any(s in q for s in ("市场","分歧","赔率","market")):
        use_a = any(word in q for word in ("独立","模型a","模型 a","model a"))
        return reply(f"{name}。"+game["independent_market_explanation" if use_a else "market_explanation"])
    return reply(f"{name}。"+game["draw_explanation" if "平局" in q else "explanation"])
