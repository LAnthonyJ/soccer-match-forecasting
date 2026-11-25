"""Shared snapshot adapter for the CLI, Flask UI, questions and static export."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import data, lifecycle, models, operations, schedule
from .evaluation import formal_report, public_ledger
from .market import instant, root_dir

UTC = timezone.utc
STATIC = Path(__file__).with_name("static")


def fixtures():
    db = root_dir() / "fixtures.sqlite3"
    now = datetime.now(UTC)
    return [schedule.public_fixture(m,now) for m in data.load_matches(db if db.exists() else None)]


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


HISTORY_PAGE_SIZE = 20


def history_pages(rows):
    ordered = sorted(rows,key=lambda m:(-date.fromisoformat(m["date"]).toordinal(),m["id"]))
    for i in range(0,len(ordered),HISTORY_PAGE_SIZE):
        yield ordered[i:i+HISTORY_PAGE_SIZE]


def _history_for(snapshot):
    # An independently verified export uses only repository-pinned historical artifacts.
    return cached_history(None, 0) if snapshot is not None else history()


def format_prediction(raw, source, market=None, b=None):
    return {"a": raw["probs"], "market": market, "b": b, "xg": raw["xg"],
            "top_scores": [[s["home"], s["away"], s["prob"]] for s in raw["top_scores"]],
            "samples": [raw["samples"][k] for k in ("home", "away")], "form": raw.get("form", {}),
            "model_version": raw["model_version"], "cutoff": raw["cutoff"], "trained_through":raw.get("trained_through"),
            "form_window":raw.get("form_window"), "source": source}


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
    "prediction_error":"单场模型计算失败","pending_freeze":"等待冻结","deadline_reserve":"已预留冻结时间，未取得同期报价",
    "pending_audit": "等待公开核验", "unknown_kickoff": "开球时间待核实",
    "missing_market": "缺少合格的同期市场报价", "no_event": "该场暂无市场报价",
    "unverified_observation": "报价接收时点与已确认赛程不符", "model_version_changed": "模型版本与注册记录不符",
    "frozen": "模型 A 已在赛前冻结",
    "schedule_conflict":"两家来源的开球时间不一致，等待核实", "schedule_unconfirmed":"计划开球时间尚待核实",
}


def explain(game, preference=None):
    p = game["prediction"]
    if not p:
        return game.get("freeze_note") or "这场比赛暂无可核实的预测。"
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


def item(match, raw=None, kind="replay", market=None, b=None, *, reason=None, freeze_reason=None, evidence=None):
    row = {k:match.get(k) for k in ("id","season","date","kickoff_utc","round","home","away","hg","ag","result_source","source_conflict")}
    row["home_zh"] = data.DISPLAY_ZH.get(match["home"], match["home"])
    row["away_zh"] = data.DISPLAY_ZH.get(match["away"], match["away"])
    row["prediction"] = format_prediction(raw,kind,market,b) if raw else None
    row["market_reason"] = reason
    row["market_note"] = REASONS.get(reason, reason or "")
    row["freeze_reason"] = freeze_reason
    row["freeze_note"] = REASONS.get(freeze_reason, freeze_reason or "")
    row["evidence"] = evidence
    for key in ("schedule_status","schedule_source","schedule_review"):
        if match.get(key) is not None:
            row[key] = match[key]
    if not match.get("kickoff_utc") and match.get("schedule_status") in ("conflict","unconfirmed"):
        row["freeze_reason"] = "schedule_"+match["schedule_status"]
        row["freeze_note"] = REASONS[row["freeze_reason"]]
    sources = match.get("source_observations") or {}
    row["score_sources"] = {provider:{"observed_at":obs.get("observed_at"),
                            "score":[obs["hg"],obs["ag"]] if obs.get("hg") is not None and obs.get("ag") is not None else None}
                            for provider,obs in sources.items() if isinstance(obs,dict)}
    if raw:
        row["explanation"] = explain(row)
        row["draw_explanation"] = explain(row,"draw")
        row["market_explanation"] = market_text(row["prediction"])
        row["independent_market_explanation"] = market_text(row["prediction"],True)
    return row


_LEGACY_MARKET_REASONS = {"no_api_key","missing_api_key","quota_reserve","provider_unavailable",
    "event_missing","recent_attempt","request_in_flight","insufficient_books","late_receipt",
    "unverified_observation","collecting_quotes","deadline_reserve","missing_market","no_event"}


def _capture_reasons(record, kickoff, due, *, replay=False, rescheduled=False):
    """Old ledgers had one reason. Only infer the axis that their value describes."""
    payload = record.get("payload") or {}
    legacy = record.get("reason")
    freeze = record.get("freeze_reason")
    market = record.get("market_reason")
    if not freeze and legacy and legacy not in _LEGACY_MARKET_REASONS and not payload:
        freeze = legacy
    if not market and legacy in _LEGACY_MARKET_REASONS:
        market = legacy
    if payload:
        freeze = "frozen"
    elif not freeze:
        freeze = ("unknown_kickoff" if not kickoff else "rescheduled" if rescheduled
                  else "no_matching_snapshot" if replay and not record
                  else "missed_window" if due else "not_due")
    if payload.get("market") is not None:
        market = None
    elif not market:
        market = ("unknown_kickoff" if not kickoff else "no_historical_market" if replay
                  else "missing_market" if due or payload else "not_due")
    return {"freeze_reason":freeze,"reason":market}


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
        due = bool(kickoff and now >= instant(kickoff) - timedelta(days=1))
        replay = replays.get(key)
        has_replay = bool(due and replay and models.replay_matches(replay,m))
        reasons[key] = _capture_reasons(record,kickoff,due,replay=has_replay and not frozen,
                                        rescheduled=key in previous and not record)
        if not kickoff:
            games[key] = item(m, **reasons[key])
            continue
        if frozen:
            games[key] = item(m, frozen["prediction"], "frozen", frozen["market"], frozen["b"],
                              evidence=record.get("evidence"), **reasons[key])
            continue
        if has_replay:
            games[key] = item(m,replay,**reasons[key])
            continue
        games[key] = item(m,**reasons[key])
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
                    games[m["id"]] = item(m,preds[m["id"]],kind,**reasons[m["id"]])
    return [games[m["id"]] for m in targets]


def _current_id(games, now):
    season = max((m["season"] for m in games), default=None)
    scheduled = [m for m in games if m["season"] == season]
    today = now.astimezone(data.LONDON).date().isoformat()
    future = [m for m in scheduled if m.get("hg") is None and
              (instant(m["kickoff_utc"])>=now if m.get("kickoff_utc") else m["date"]>=today)]
    key = lambda m:(m["date"],m.get("kickoff_utc") or "",m["id"])
    chosen = min(future,key=key) if future else max(scheduled,key=key,default=None)
    return chosen["id"] if chosen else None


def _week(m):
    if m.get("round") is not None:
        text = str(m["round"])
        n = re.fullmatch(r"(?:(?:round|matchday|week)\s*)?(\d+)",text,re.I)
        return "第 "+n[1]+" 轮" if n else text
    day = date.fromisoformat(m["date"])
    return (day-timedelta(days=day.weekday())).isoformat()+" 周"


def board_view(full):
    games = full["fixtures"]
    anchors = {m["id"]:m for m in games if m["id"] in (full["current_id"],full["default_id"])}
    weeks = {(m["season"],_week(m)) for m in anchors.values()}
    cards = []
    for m in games:
        if m["id"]==full["default_id"]:
            cards.append(m)
        elif (m["season"],_week(m)) in weeks:
            card = {k:m.get(k) for k in ("id","season","round","date","kickoff_utc","home","away","home_zh",
                                         "away_zh","hg","ag","source_conflict","schedule_status")}
            p = m["prediction"]
            card.update(partial=True,prediction={"source":p["source"],"a":p["a"]} if p else None)
            cards.append(card)
    result = dict(full, compact=True, fixtures=cards)
    r = full["report"]
    result["report"] = {"n_pairs":r["n_pairs"],"status":r["status"],"as_of":r.get("as_of"),"seasons":r["seasons"],
                        "pilot":{"seasons":r.get("pilot",{}).get("seasons",[])},
                        "history":{"n":r["history"]["n"],"seasons":r["history"]["seasons"]}}
    result["week_catalog"] = full.get("week_catalog") or {s:sorted({_week(m) for m in games if m["season"]==s}) for s in {m["season"] for m in games}}
    return result


def score_watermark(matches,season,as_of):
    """Last fully settled date across the verified schedule, plus the newest individual result."""
    days = {}
    confirmed_dates = []
    for m in matches:
        if m["season"]!=season:
            continue
        observed = m.get("result_observed_at")
        known = (m.get("hg") is not None and m.get("ag") is not None and not m.get("source_conflict")
                 and (not observed or instant(observed)<=as_of) and m["date"]<=as_of.astimezone(data.LONDON).date().isoformat())
        if known:
            confirmed_dates.append(m["date"])
        if m.get("schedule_status")=="verified" and m.get("kickoff_utc"):
            kickoff = instant(m["kickoff_utc"])
            day = kickoff.astimezone(data.LONDON).date().isoformat()
            days.setdefault(day,[]).append(known and kickoff + timedelta(days=1)<=as_of)
    through = None
    for day,complete in sorted(days.items()):
        if not all(complete):
            break
        through = day
    return {"settled_through":through,"latest_confirmed_score_date":max(confirmed_dates,default=None)}


def dashboard(season=None, *, static=False, all_seasons=False, snapshot=None, compact=False):
    now = instant(snapshot["as_of"]) if snapshot is not None else datetime.now(UTC)
    if snapshot is None:
        matches, ledger = fixtures(), public_ledger()
        scoring_fixtures = matches
    else:
        # Package older seasons with unchanged CC0 records. The public season list is authoritative.
        supplied = snapshot["fixtures"]
        seasons_in_snapshot = {m["season"] for m in supplied}
        scoring_fixtures = [m for m in data.load_matches() if m["season"] not in seasons_in_snapshot] + supplied
        matches = [schedule.public_fixture(m,now) for m in scoring_fixtures]
        ledger = snapshot["ledger"]
    years = sorted({m["season"] for m in matches})
    if season and season not in years:
        raise ValueError("未知赛季")
    selected = years if all_seasons else [season] if season else years[-(2 if static else 3):]
    targets = sorted((m for m in matches if m["season"] in selected),key=lambda m:(m["date"],m["kickoff_utc"] or "",m["id"]))
    current_id = _current_id(targets,now)
    if compact:
        week_catalog = {s:sorted({_week(m) for m in targets if m["season"]==s}) for s in selected}
        replays = _history_for(snapshot)[0]
        example = next((m for m in reversed(targets) if m.get("hg") is not None and m.get("kickoff_utc")
                        and m["id"] in replays and models.replay_matches(replays[m["id"]],m)),None)
        weeks = {(m["season"],_week(m)) for m in targets if m["id"]==current_id or m is example}
        targets = [m for m in targets if (m["season"],_week(m)) in weeks]
    output = _present(targets,matches,ledger,now,snapshot)
    example = next((r for r in reversed(output) if r["prediction"] and r["prediction"]["source"]=="replay" and r["hg"] is not None),None)
    report = formal_report(scoring_fixtures,ledger,now)
    _attach_trace(output,matches,ledger,report,now,snapshot)
    scores = _history_for(snapshot)[1]
    report["history"] = {k:v for k,v in scores.items() if k != "rows"}
    health_season = season or years[-1]
    if snapshot is None:
        health = data.season_health(root_dir()/"fixtures.sqlite3",health_season,matches=matches,now=now)
    else:
        health = snapshot["seasons_health"].get(health_season) or snapshot["health"]
    health = dict(health,**score_watermark(matches,health_season,now))
    complete = [s for s,count in Counter(m["season"] for m in matches if m["hg"] is not None and not m.get("source_conflict")).items() if count == 380]
    latest = max((r for r in matches if r.get("observed_at")),key=lambda r:r["observed_at"],default={})
    state = operations.public_state() if snapshot is None else snapshot.get("operations",{})
    status = operations.status(matches,ledger,health,now,state,snapshot_at=snapshot["as_of"] if snapshot else None)
    result = {"operations":status,"fixtures":output, "report":report, "default_id":example["id"] if example else current_id,
            "current_id":current_id, "available_seasons":selected if static else years,
            "updated_at":now.isoformat(), "health":health, "last_complete_season":max(complete,default=None), "archive_seasons":years,
            "data_version":latest.get("source_commit") or data.read_coverage()["source_commit"],
            "last_change_observed_at":latest.get("observed_at"), "last_scored_date":health.get("last_scored_date"),
            "team_aliases":data.ALIASES, "public_snapshot":snapshot is not None}
    return board_view(dict(result,week_catalog=week_catalog)) if compact else result


def _attach_trace(games,matches,ledger,report,now,snapshot=None):
    versions = data.load_versions(root_dir()/"fixtures.sqlite3") if snapshot is None else snapshot["versions"]
    deployments = operations.public_deployments() if snapshot is None else snapshot.get("deployments",[])
    lifecycle.attach(games,matches,versions,ledger,report,deployments,now)
    for game in games:
        if game.get("schedule_status") == "verified" and game.get("kickoff_utc"):
            game["schedule_window"] = schedule.protocol_window_text(game)


def export_history_assets(directory, dashboard_dict, *, snapshot=None):
    directory = Path(directory)
    def save(path,value):
        path.write_text(json.dumps(value,ensure_ascii=False,separators=(",",":")),encoding="utf8")
    archive = historical_rows(snapshot=snapshot)
    save(directory/"history-rows.json",archive)
    history_dir = directory/"history"
    history_dir.mkdir(exist_ok=True)
    by_year = {}
    for row in archive:
        by_year.setdefault(row["season"],[]).append(row)
    for year,rows in {"all":archive,**by_year}.items():
        if year!="all":
            save(history_dir/(year+".json"),rows)
        page_dir = history_dir/year
        page_dir.mkdir(exist_ok=True)
        for i,chunk in enumerate(history_pages(rows),1):
            save(page_dir/f"{i}.json",chunk)
    save(directory/"report.json",dashboard_dict["report"])
    save(directory/"board.json",board_view(dashboard_dict))
    # Older history remains addressable without putting every season in the initial download.
    included = set(dashboard_dict["available_seasons"])
    if snapshot is None:
        source, ledger = fixtures(), public_ledger()
    else:
        observed_seasons = {m["season"] for m in snapshot["fixtures"]}
        source = [m for m in data.load_matches() if m["season"] not in observed_seasons] + snapshot["fixtures"]
        ledger = snapshot["ledger"]
    older = [m for m in source if m["season"] not in included]
    captures = {(r["id"],r["kickoff"],r["revision"]):r for r in ledger["records"]}
    previous = {r["id"] for r in ledger["records"]}
    as_of = instant(dashboard_dict["updated_at"])
    replays = _history_for(snapshot)[0]
    season_dir = directory/"seasons"
    season_dir.mkdir(exist_ok=True)
    for year in included:
        save(season_dir/(year+".json"),[m for m in dashboard_dict["fixtures"] if m["season"]==year])
    for season in sorted({m["season"] for m in older}):
        rows = []
        for m in older:
            if m["season"] == season:
                kickoff = m.get("kickoff_utc")
                record = captures.get((m["id"],kickoff,m.get("schedule_revision",0)), {})
                frozen = record.get("payload")
                raw = replays.get(m["id"])
                has_replay = bool(raw and kickoff and models.replay_matches(raw,m))
                due = bool(kickoff and as_of >= instant(kickoff)-timedelta(days=1))
                reasons = _capture_reasons(record,kickoff,due,replay=has_replay and not frozen,
                                           rescheduled=m["id"] in previous and not record)
                if frozen and kickoff:
                    rows.append(item(m,frozen["prediction"],"frozen",frozen["market"],frozen["b"],
                                     evidence=record.get("evidence"),**reasons))
                    continue
                rows.append(item(m,raw if has_replay else None,**reasons))
        _attach_trace(rows,source,ledger,dashboard_dict["report"],as_of,snapshot)
        (season_dir/(season+".json")).write_text(json.dumps(rows,ensure_ascii=False,separators=(",",":")),encoding="utf8")


def full_report():
    result = formal_report(fixtures(), public_ledger())
    result["history"] = {k:v for k,v in history()[1].items() if k != "rows"}
    return result


def season_fixtures(season):
    now, matches, ledger = datetime.now(UTC), fixtures(), public_ledger()
    chosen = [m for m in matches if m["season"]==season]
    if not chosen:
        raise KeyError(season)
    games = _present(chosen,matches,ledger,now)
    ids = {m["id"] for m in chosen}
    report = formal_report(matches,ledger,now) if any(r["id"] in ids and r.get("payload") for r in ledger["records"]) else {}
    _attach_trace(games,matches,ledger,report,now)
    return games


def prediction(match_id):
    matches = fixtures()
    m = next((r for r in matches if r["id"] == match_id),None)
    if not m:
        raise KeyError(match_id)
    now,ledger = datetime.now(UTC),public_ledger()
    games = _present([m], matches, ledger, now)
    report = formal_report(matches,ledger,now) if any(r["id"]==match_id and r.get("payload") for r in ledger["records"]) else {}
    _attach_trace(games,matches,ledger,report,now)
    return games[0]


def ask(question, fixture_id=None, zone="Europe/London"):
    if not isinstance(question,str) or not 0<len(question.strip())<=500:
        raise ValueError("请输入 1–500 字的问题。")
    q = question.casefold().strip()
    def reply(answer,choices=()):
        return {"answer":answer,"choices":list(choices)}
    if any(x in q for x in ("伤","停赛","伤停","阵容","首发","injur","lineup")):
        return reply("目前缺少可核实的实时伤停和首发数据。模型使用赛前已知的历史赛果。")
    matches = fixtures()
    trace_question = any(word in q for word in ("这场得","这场评分","这场的评分","这场的成绩","结算","见证","时间线","这场比赛得","单场评分"))
    if not trace_question and any(x in q for x in ("过去","上赛季","成绩","表现比","领先市场","赛季模型","战绩","评测","试运行","历史回放","beat the market")):
        report = formal_report(matches,public_ledger())
        def score_reply(message):
            stamp = report.get("as_of")
            return reply(message + (f" 数据截至 {stamp}。" if stamp else ""))
        requested = re.search(r"20\d\d-\d\d",q)
        season = requested.group() if requested else None
        if not season and ("过去一季" in q or "上赛季" in q):
            season = max((s for s in {m["season"] for m in matches} if sum(
                m["season"]==s and m["hg"] is not None for m in matches)==380),default=None)
        if season:
            if "历史" in q or "回放" in q:
                row = next((s for s in history()[1]["seasons"] if s["season"]==season),None)
                if row:
                    return score_reply(f"{season} 历史回放有 {row['n']} 场，模型 A 平均对数损失 {row['log_loss']:.4f}，Brier 分数 {row['brier']:.4f}。")
                return score_reply(f"{season} 赛季尚无历史回放评分。")
            official = next((s for s in report["seasons"] if s["season"]==season),None)
            pilot = next((s for s in report.get("pilot",{}).get("seasons",[]) if s["season"]==season),None)
            summary = official if "正式" in q else pilot if "试运行" in q else official or pilot
            if not summary or not summary["paired"]:
                label = "正式评测" if "正式" in q else "试运行" if "试运行" in q else ""
                if not label and summary:
                    label = "正式评测" if summary is official else "试运行"
                return score_reply(f"{season} 赛季{label}尚无可核验的同场市场配对（0 场），无法判断该季是否领先。" + (summary or {}).get("sample_note",""))
            return score_reply(f"{season} 赛季{'正式评测' if summary is official else '试运行'}有 {summary['paired']} 场有效配对，融合模型和市场平均对数损失分别为 "
                         f"{summary['model_loss']:.4f}、{summary['market_loss']:.4f}。")
        status = {"accumulating":"正在积累样本","leading":"在预注册赛季和时点领先","unproven":"尚未证实领先"}
        return score_reply(f"正式评测有 {report['n_pairs']} 场可核验配对，目前{status[report['status']]}。")
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
        clock = datetime.now(tz)
        today = clock.date()
        sat = today+timedelta(days=(5-today.weekday())%7 if today.weekday()!=6 else -1)
        weekend = "周末" in q or "weekend" in q
        candidates = []
        for m in matches:
            if not schedule.confirmed_unplayed(m):
                continue
            kickoff = instant(m["kickoff_utc"]).astimezone(tz)
            if kickoff > clock and (not weekend or sat <= kickoff.date() <= sat+timedelta(days=1)):
                candidates.append(m)
    else:
        candidates = matches
    if teams:
        candidates = [m for m in candidates if teams <= {m["home"],m["away"]}]
    next_game = "下一场" in q or "下场" in q
    selected = None if next_game else next((m for m in candidates if m["id"]==fixture_id),None)
    if not selected:
        if len(candidates)==1 or (candidates and next_game):
            selected = min(candidates,key=lambda r:(instant(r["kickoff_utc"]),r["id"])) if next_game else candidates[0]
        else:
            ranked = sorted(candidates,key=lambda m:abs((date.fromisoformat(m["date"])-date.today()).days))
            return reply("请选择具体场次。" if ranked else "已收录的赛程中没有找到符合条件的比赛。",
                         [{"id":m["id"],"label":f"{m['date']} · {m['home']} – {m['away']}"} for m in ranked[:8]])
    if re.search(r"截止|采集窗口|冻结窗口|t\s*[-−－]?\s*24",q):
        return {**reply(schedule.protocol_window_text(selected)),"fixture_id":selected["id"]}
    if next_game or any(s in q for s in ("踢谁","对手","何时","时间","赛程","weekend")) and not trace_question:
        home,away = (data.DISPLAY_ZH.get(selected[k],selected[k]) for k in ("home","away"))
        choices = [{"id":selected["id"],"label":f"{home} 对 {away}"}] if next_game else []
        return {**reply(f"{selected['date']} {home} 对 {away}。开球时间（UTC）：{selected['kickoff_utc'] or '待核实'}。",choices),
                "fixture_id":selected["id"]}
    game = prediction(selected["id"])
    name = f"{game['date']} {game['home_zh']} 对 {game['away_zh']}"
    if trace_question:
        return reply(game["trace_answer"])
    p = game["prediction"]
    market_question = any(s in q for s in ("市场","分歧","赔率","market"))
    if not p:
        note = game.get("market_note" if market_question else "freeze_note")
        return reply(f"{name} 暂无已核实的赛前预测。" + (note+"。" if note else ""))
    if market_question:
        if not p.get("market") and game.get("market_note"):
            return reply(f"{name}。"+game["market_note"]+"。缺少合格的同期市场快照，无法计算市场分歧。")
        use_a = any(word in q for word in ("独立","模型a","模型 a","model a"))
        return reply(f"{name}。"+game["independent_market_explanation" if use_a else "market_explanation"])
    return reply(f"{name}。"+game["draw_explanation" if "平局" in q else "explanation"])
