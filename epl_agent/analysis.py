"""Build the public evidence a pre-match analysis may cite, independently of settlement."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import re

from . import data, schedule, team_history
from .market import canonical, digest

FIXTURE_ID = re.compile(r"[a-zA-Z0-9_-]{1,180}")
OUTCOMES = ("主胜", "平局", "客胜")
ORIGINS = {"preview":"赛前预览", "frozen":"赛前冻结", "replay":"历史回放",
           "reconstruction":"按历史时点重建", "schedule":"仅有赛程"}
GROUPS = (("赛前判断", ("probabilities", "favourite", "top_scores")), ("球队近况", ("home_form", "away_form", "expected_goals")),
          ("市场对照", ("market", "market_gap")), ("资料边界", ("injuries",)))


def fact(key, tool, text, stage="prematch"):
    return {"id":key, "tool":tool, "text":text, "stage":stage}


def displayed_percent(value):
    # Match the browser's (value * 100).toFixed(1), including exact halfway cases.
    return Decimal.from_float(value * 100.).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def packet(game, as_of):
    """Construct from an allowlist. hg, ag, later score revisions and questions are never model inputs."""
    fid = game["id"]
    if not FIXTURE_ID.fullmatch(fid):
        raise ValueError("Invalid fixture identifier")
    home, away = (game.get(k+"_zh") or game[k] for k in ("home", "away"))
    p, trace = game.get("prediction"), game.get("trace") or {}
    source = p["source"] if p else "schedule"
    kickoff = game.get("kickoff_utc")
    facts = [fact("fixture","fixture",f'{game["season"]} 英超，{home}主场对{away}。'),
             fact("kickoff","fixture",f"已记录开球时刻为 {kickoff}。" if kickoff else "开球时间待核实。")]
    if kickoff and game.get("schedule_status")=="verified":
        facts.append(fact("protocol_cutoff","fixture",schedule.protocol_window_text(game)))
    if p:
        a = p["a"]
        percents = [displayed_percent(value) for value in a]
        facts.append(fact("model","prediction",f'{ORIGINS[source]}，独立模型版本为 {p["model_version"]}。'))
        if p.get("trained_through"):
            facts.append(fact("training_cutoff","prediction","训练标签截止 "+p["trained_through"]+"。"))
        if source=="frozen" and trace.get("hash"):
            facts.append(fact("freeze_hash","prediction","赛前预测 SHA-256 为 "+trace["hash"]+"。"))
        facts.append(fact("probabilities","prediction","独立模型 A 给出"+ "、".join(
            f"{label} {value:.1f}%" for label,value in zip(OUTCOMES,percents))+"。"))
        ranked = sorted(range(3),key=percents.__getitem__,reverse=True)
        first, second = ranked[:2]
        difference = percents[first]-percents[second]
        tied = [OUTCOMES[i] for i in ranked if percents[i]==percents[first]]
        leaning = (f"{OUTCOMES[first]}概率最高，比{OUTCOMES[second]}高 {difference:.1f} 个百分点。"
                   if difference else "、".join(tied)+"的概率在显示精度内并列居首。")
        facts.append(fact("favourite","prediction",leaning))
        scores = sorted(p.get("top_scores") or [], key=lambda item: -item[2])[:3]
        if scores:
            values = [(f"{hg}–{ag}", displayed_percent(prob)) for hg,ag,prob in scores]
            tied_scores = [label for label,percent in values if percent == values[0][1]]
            leader = ("、".join(tied_scores)+" 在显示精度内并列最高。") if len(tied_scores)>1 else ""
            text = "最可能的单一比分（主队在前）："+"、".join(f"{label} 约 {percent:.1f}%" for label,percent in values)+"。"
            facts.append(fact("top_scores","prediction",text+leader))
        facts.append(fact("draw_margin","prediction",
            f"平局概率为 {percents[1]:.1f}%，与概率最高的一项相差 {percents[first]-percents[1]:.1f} 个百分点。"))
        if p.get("xg"):
            facts.append(fact("expected_goals","prediction",
                f'按比分分布，模型预计{home}进 {p["xg"][0]:.2f} 球，{away}进 {p["xg"][1]:.2f} 球。'))
            delta=p["xg"][0]-p["xg"][1]
            outlook=(f"{home if delta>0 else away}的模型预计进球多 {abs(delta):.2f} 球。"
                     if abs(delta)>=.005 else "双方的模型预计进球在显示精度内持平。")
            facts.append(fact("goals_comparison","prediction",outlook))
        for i,(side,team) in enumerate((("home",home),("away",away))):
            form = (p.get("form") or {}).get(side)
            if form:
                total = p["samples"][i]
                facts.append(fact(side+"_form","prediction",
                    f'{team}累计 {total} 场英超样本，近况取最近 {min(total,8)} 场并用 4 场先验平滑，'
                    f'场均进 {form["gf"]:.2f} 球、失 {form["ga"]:.2f} 球、得 {form["points"]:.2f} 分。'))
        form=p.get("form") or {}
        if form.get("home") and form.get("away") and min(p["samples"])>0:
            delta=form["home"]["points"]-form["away"]["points"]
            description=(f"按平滑后的近期场均积分，{home if delta>0 else away}高 {abs(delta):.2f} 分。"
                         if abs(delta)>=.005 else "两队平滑后的近期场均积分在显示精度内持平。")
            facts.append(fact("form_comparison","prediction",description))
    else:
        facts.append(fact("probabilities","prediction","暂无可核验的胜平负预测。"))
    if p and p.get("market"):
        quotes = p["market"]
        market_percents = [displayed_percent(value) for value in quotes]
        facts.append(fact("market","market","同期去水市场概率为"+
            "、".join(f"{label} {v:.1f}%" for label,v in zip(OUTCOMES,market_percents))+"。"))
        market_picks=[i for i,v in enumerate(market_percents) if v==max(market_percents)]
        model_picks=[i for i,v in enumerate(percents) if v==max(percents)]
        if len(market_picks)==len(model_picks)==1:
            facts.append(fact("market_leader","market",f'独立模型最看好{OUTCOMES[model_picks[0]]}，'
                              f'同期市场最看好{OUTCOMES[market_picks[0]]}。'))
        for key, values, name in (("market_gap",p["a"],"独立模型 A"),("blended_gap",p.get("b"),"融合模型 B")):
            if values:
                gaps = [(v-q)*100 for v,q in zip(values,quotes)]
                k = max(range(3),key=lambda idx:abs(gaps[idx]))
                facts.append(fact(key,"market",f"{name}与市场在{OUTCOMES[k]}上的分歧最大，相差 {gaps[k]:+.1f} 个百分点。"))
    else:
        facts.append(fact("market","market","缺少可核验的同期市场报价，无法计算市场分歧。"))
    facts.append(fact("injuries","fixture","缺少可核实的实时伤停和首发资料。"))
    body = {"schema":"epl-analysis-facts/v1","fixture_id":fid,"revision":trace.get("revision",0),
            "source":source,"facts":facts}
    unavailable = {"injuries"}
    if not p:
        unavailable.add("probabilities")
    if not p or not p.get("market"):
        unavailable.add("market")
    if not kickoff:
        unavailable.add("kickoff")
    available = [f["id"] for f in facts if f["id"] not in unavailable]
    return {**body,"fact_hash":digest(body),"as_of":p["cutoff"] if p else as_of,"available_topics":available}


def template_report(bundle, reason="provider_unconfigured"):
    by_key = {f["id"]:f["text"] for f in bundle["facts"]}
    sections = []
    for title,keys in GROUPS:
        present = [key for key in keys if key in by_key]
        if present:
            sections.append({"title":title,"fact_ids":present,"text":"".join(by_key[k] for k in present)})
    return {"fixture_id":bundle["fixture_id"],"fact_hash":bundle["fact_hash"],"as_of":bundle["as_of"],
            "source":"template","model":None,"generated_at":None,"fallback_reason":reason,
            "answer":"\n".join(s["text"] for s in sections),"sections":sections,
            "available_topics":bundle.get("available_topics",[])}


def _review(game, stamp):
    trace = game.get("trace") or {}
    score = trace.get("score")
    facts = []
    if game.get("source_conflict"):
        facts.append(fact("result","evaluation","两源赛果冲突，暂停结算。","postmatch"))
    elif game.get("hg") is not None and game.get("ag") is not None:
        facts.append(fact("result","evaluation",f'已收录赛果为主队 {game["hg"]} 比 {game["ag"]} 客队。',"postmatch"))
    if score:
        facts.append(fact("score","evaluation",
            f'本场 A 对数损失 {score["log_loss"]:.4f}，Brier 分数 {score["brier"]:.4f}。',"postmatch"))
    else:
        facts.append(fact("score","evaluation","本场尚无通过前瞻核验的 A 单场评分。","postmatch"))
    return {"as_of":stamp,"facts":facts}


def _archive_season(path, dest, stamp):
    games = sorted(json.loads(path.read_bytes()), key=lambda game: game["id"])
    with (dest/"archive"/path.name).open("w",encoding="utf8") as archive, \
            (dest/"reviews"/path.name).open("w",encoding="utf8") as review:
        archive.write("{")
        review.write("{")
        for index,game in enumerate(games):
            key = ("," if index else "") + canonical(game["id"]) + ":"
            archive.write(key + canonical(packet(game,stamp)))
            review.write(key + canonical(_review(game,stamp)))
        archive.write("}")
        review.write("}")


def export_assets(directory, dashboard, *, snapshot=None):
    """Recent fixtures are addressable individually. Archived seasons share one small-file index each."""
    dest = Path(directory)/"analysis"
    for folder in ("facts","archive","reviews","reports"):
        (dest/folder).mkdir(parents=True,exist_ok=True)
    (dest/"reports"/"index.json").write_text("{}",encoding="utf8")
    stamp = dashboard["updated_at"]
    games = dashboard["fixtures"]
    reviews = defaultdict(dict)
    for game in games:
        bundle = packet(game,stamp)
        (dest/"facts"/(game["id"]+".json")).write_text(canonical(bundle),encoding="utf8")
        reviews[game["season"]][game["id"]] = _review(game,stamp)
    recent = set(reviews)
    for season, items in reviews.items():
        (dest/"reviews"/(season+".json")).write_text(canonical(items),encoding="utf8")
    season_files = sorted((Path(directory)/"seasons").glob("*.json"))
    if season_files:
        from . import service
        inputs = (service.model_inputs() if snapshot is None else
                  {"archive":data.load_matches(), "versions":snapshot["versions"]})
        index = team_history.ResultIndex(**inputs)
    for path in season_files:
        if not re.fullmatch(r"20\d{2}-\d{2}",path.stem):
            continue
        if path.stem not in recent:
            _archive_season(path,dest,stamp)
        team_history.export_season(dest,path.stem,json.loads(path.read_bytes()),index,stamp)
    current = next((g for g in games if g["id"]==dashboard.get("current_id")),None)
    ids = []
    if current:
        for g in games:
            if g["season"]!=current["season"] or not schedule.confirmed_unplayed(g):
                continue
            same_week = g["round"]==current["round"] if current.get("round") else g["date"]==current["date"]
            if same_week and g.get("prediction"):
                ids.append(g["id"])
    (dest/"current.json").write_text(canonical({"schema":"epl-analysis-current/v1","fixture_ids":ids[:20]}),encoding="utf8")
    seasons = defaultdict(dict)
    for cohort,details in (("history",dashboard["report"]["history"]),("pilot",dashboard["report"].get("pilot",{})),
                           ("official",dashboard["report"])):
        for row in details.get("seasons",[]):
            seasons[row["season"]][cohort] = {k:row[k] for k in
                ("paired","finished","total","model_loss","market_loss","n","log_loss","accuracy","sample_note") if k in row}
    latest = max(recent,default=None)
    fixtures = [{k:g[k] for k in ("id","season","home","away","home_zh","away_zh","kickoff_utc")}
                for g in games if g["season"]==latest and schedule.confirmed_unplayed(g)]
    catalog = {"as_of":stamp,"last_complete_season":dashboard.get("last_complete_season"),"seasons":seasons,
               "status":dashboard["report"]["status"],"n_pairs":dashboard["report"]["n_pairs"],
               "aliases":dashboard.get("team_aliases",{}),"fixtures":fixtures}
    (dest/"catalog.json").write_text(canonical(catalog),encoding="utf8")


def live_packet(fid):
    from . import service
    return packet(service.prediction(fid),datetime.now(timezone.utc).isoformat())
