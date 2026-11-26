"""Deterministic local answers using the same topic rules and evidence IDs as the public site."""
import json
import re
from datetime import timezone
from pathlib import Path

from . import analysis, data, schedule, service
from .market import instant

_RULES = json.loads((Path(__file__).parent / "assets/data/fan_questions.json").read_text("utf8"))
_TOPICS = {row["topic"]: row for row in _RULES}
_ID = re.compile(r"\d{4}-\d{2}_[a-z0-9-]+_[a-z0-9-]+")
_HASH = re.compile(r"[0-9a-f]{64}")


def valid_context(context):
    return context is None or (isinstance(context,dict)
        and isinstance(context.get("fixture_id"),str) and _ID.fullmatch(context["fixture_id"])
        and isinstance(context.get("topic"),str) and len(context["topic"])<=40
        and (not context.get("fact_hash") or isinstance(context["fact_hash"],str) and _HASH.fullmatch(context["fact_hash"])))


def classify(question,context):
    if re.search("不要剧透|不剧透|隐藏赛果",question) and re.search("赛前|预测",question):
        return _TOPICS["probabilities"]
    for rule in _RULES:
        if re.search(rule["pattern"],question,re.I):
            return rule
    if re.search("本页|这场呢|那场|那他们|那主队|那客队|主队呢|客队呢",question):
        prior=(context or {}).get("topic")
        if re.search("主队|客队",question) and prior not in ("history","standing","probabilities","draw","form"):
            return _TOPICS["form"]
        return _TOPICS.get("kickoff" if prior=="schedule" else prior)
    return None


def team_sides(info,question):
    if "主队" in question:
        return ["home"]
    if "客队" in question:
        return ["away"]
    teams=data.mentioned_teams(question)
    if teams-{info["home"],info["away"]}:
        return []
    return [side for side in ("home","away") if info[side] in teams
            or info.get(side+"_zh",info[side]) in question] or ["home","away"]


def reply(page,question,context,zone):
    from .team_history import live_context
    target=page
    if context and context["fixture_id"]!=page["fixture_id"] and not re.search("本页|当前页|页面这场",question):
        target=analysis.live_packet(context["fixture_id"])
        if context.get("fact_hash") and target["fact_hash"]!=context["fact_hash"]:
            raise ValueError("context_changed")
    rule=classify(question,context) or {}
    topic=rule.get("topic","unknown")
    def answer(text,evidence=(),missing=None,**extra):
        return {"fixture_id":page["fixture_id"],"fact_hash":page["fact_hash"],"answer_fixture_id":target["fixture_id"],
                "answer_fact_hash":target["fact_hash"],"fixture_label":next((f["text"] for f in target["facts"] if f["id"]=="fixture"),target["fixture_id"]),
                "answer_kickoff":next((f["text"] for f in target["facts"] if f["id"]=="kickoff"),None),
                "as_of":target["as_of"],"source":"template","topic":topic,"answer":text,
                "missing_reason":missing,"evidence":list(evidence),"choices":[],**extra}
    def missing(text):
        return answer(text,missing="missing_"+topic)
    if not rule:
        return missing("当前资料无法回答这个问题。可以问本场概率、近期表现或比赛时间。")
    if rule.get("unavailable"):
        return missing(rule["unavailable"])
    if topic in ("schedule","model_results"):
        lookup=service.ask(question,None if topic=="schedule" else target["fixture_id"],zone)
        choices=lookup.get("choices",[])
        found=lookup.get("fixture_id")
        if topic=="schedule" and found and _ID.fullmatch(found) and found!=target["fixture_id"]:
            target=analysis.live_packet(found)
        key="schedule_next" if found else "schedule_choices" if choices else "schedule_missing"
        if topic=="model_results":
            key="official_status"
        return answer(lookup["answer"],[key],"missing_schedule" if key=="schedule_missing" else None,
                      choices=choices,needs_choice=key=="schedule_choices")
    teams=data.mentioned_teams(question)
    slugs={re.sub(r"[^a-z0-9]+","-",team.casefold()).strip("-") for team in teams}
    if slugs-set(target["fixture_id"].split("_")[1:]):
        now=service.datetime.now(timezone.utc)
        candidates=sorted((m for m in service.fixtures() if schedule.confirmed_unplayed(m)
                           and instant(m["kickoff_utc"])>now and teams<={m["home"],m["away"]}),
                          key=lambda m:(m["kickoff_utc"],m["id"]))
        if not candidates:
            return missing("已核实的未来赛程中没有找到所问球队的对阵。")
        if len(candidates)>1:
            choices=[{"id":m["id"],"label":f"{data.DISPLAY_ZH.get(m['home'],m['home'])} – {data.DISPLAY_ZH.get(m['away'],m['away'])} · {m['kickoff_utc']}"} for m in candidates[:8]]
            return answer("请选择要查询的球队或场次。",["schedule_choices"],choices=choices,needs_choice=True)
        target=analysis.live_packet(candidates[0]["id"])
    facts={f["id"]:f for f in target["facts"]}
    ids=rule.get("ids",[])
    stamp=target["as_of"]
    extra={}
    if topic in ("history","h2h","standing"):
        scope="current" if re.search("现在|目前|最新|当前|截至今天|此刻",question) else "prematch"
        info=live_context(target["fixture_id"],scope,as_of=target["as_of"] if scope=="prematch" else None)
        if not info:
            return missing("本场缺少可核验的球队历史资料。")
        sides=team_sides(info,question)
        if not sides:
            return missing("提到的球队不在这场对阵中，请先查询该队的下一场比赛。")
        facts={f["id"]:f for f in info["facts"]}
        stamp=info["as_of"]
        extra["scope"]=scope
        if topic=="h2h":
            ids=["h2h"]
        elif topic=="standing" and len(sides)==2 and not re.search("两队|双方",question):
            ids=["standings"]
            extra["standings"]=info["standings"]["rows"]
        else:
            ids=[side+("_recent" if topic=="history" else "_standing") for side in sides]
    elif topic in ("score","result","review"):
        if re.search("不要剧透|不剧透|隐藏赛果|赛前分析",question):
            return missing("赛前解读只使用开球前可见的事实。")
        info=analysis._review(service.prediction(target["fixture_id"]),service.datetime.now(timezone.utc).isoformat())
        facts={f["id"]:f for f in info["facts"]}
        stamp=info["as_of"]
    elif topic=="form" and re.search("主队|客队",question):
        ids=["home_form" if "主队" in question else "away_form"]
    selected=[facts[key] for key in ids if key in facts]
    if not selected:
        return missing("这场比赛暂缺可核对的对应资料。")
    unavailable=all(re.search("缺少|尚无|暂无",f["text"]) for f in selected)
    return answer(" ".join(f["text"] for f in selected),[f["id"] for f in selected],
                  "missing_"+topic if unavailable else None,as_of=stamp,**extra)
