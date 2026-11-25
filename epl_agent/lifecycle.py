"""Project the same fixture revisions, audited scores and receipts onto every surface."""
from collections import defaultdict
from datetime import timedelta

from .market import instant


def attach(games, fixtures, versions, ledger, report, deployments, now):
    current = {m["id"]:m for m in fixtures}
    history, receipts = defaultdict(list), defaultdict(list)
    for row in versions:
        history[row["id"]].append(row)
    for row in ledger["records"]:
        receipts[row["id"]].append(row)
    posted = {(r["id"],r["kickoff"],r["revision"],r["hash"],r.get("result_observed_at")):r for r in deployments}
    scores = {r["id"]:r for cohort in (report,report.get("pilot",{}))
              for r in cohort.get("independent",{}).get("rows",[])}
    for game in games:
        fid = game["id"]
        m = current[fid]
        kickoff,revision = m.get("kickoff_utc"),m.get("schedule_revision",0)
        cutoff = instant(kickoff)-timedelta(days=1) if kickoff else None
        record = next((r for r in receipts[fid] if r["kickoff"]==kickoff and r["revision"]==revision),{})
        payload = record.get("payload") or {}
        witness = record.get("witnessed")
        scored = scores.get(fid,{})
        prior = {}
        for version in history[fid]:
            number = version.get("schedule_revision",0)
            prior.setdefault(number,{"revision":number,"kickoff":version.get("kickoff_utc"),
                                     "observed":version.get("schedule_observed_at"),"withdrawn":False})
            if version.get("withdrawn"):
                prior[number]["withdrawn"] = True
        for old in receipts[fid]:
            item = prior.setdefault(old["revision"],{"revision":old["revision"],"kickoff":old["kickoff"]})
            item.update(hash=old.get("hash"),evidence=old.get("evidence"))
        published = posted.get((fid,kickoff,revision,record.get("hash"),m.get("result_observed_at")))
        trace = {"id":fid,"revision":revision,"schedule_at":m.get("schedule_observed_at"),"kickoff":kickoff,
                 "cutoff":cutoff.isoformat() if cutoff else None,"frozen":payload.get("frozen"),
                 "hash":record.get("hash"),"witnessed":witness,"evidence":record.get("evidence"),
                 "witness_status":"late" if cutoff and witness and instant(witness)>cutoff else
                                  "verified" if (ledger.get("public_audit") or {}).get("forecasts",{}).get(record.get("hash"))
                                  else "recorded" if witness else "pending" if payload else None,
                 "freeze_reason":record.get("freeze_reason") or ("frozen" if payload else game.get("market_reason")),
                 "market_reason":record.get("market_reason") or game.get("market_reason"),
                 "result_at":m.get("result_observed_at"),"result_source":m.get("result_source"),
                 "settlement":scored.get("reason"),"score":None,"deployed":published,
                 "schedule_history":list(prior.values())}
        if scored.get("status")=="scored":
            trace["score"] = {k:scored.get(k) for k in ("log_loss","brier","correct","actual")}
            trace["score_as_of"] = now.isoformat()
        game["trace"] = trace
        if trace["score"]:
            score = trace["score"]
            message = f"模型 A 本场对数损失 {score['log_loss']:.4f}，Brier 分数 {score['brier']:.4f}。"
        elif m.get("source_conflict"):
            message = "两源比分冲突，暂停结算。"
        elif payload and not witness:
            message = "模型 A 已冻结，缺少公开见证。"
        elif trace["witness_status"]=="late":
            message = "公开见证晚于截止时间，本场不计入前瞻成绩。"
        elif payload and m.get("hg") is None:
            message = "模型 A 已冻结，等待确认赛果后评分。"
        elif payload:
            message = "模型 A 已冻结，等待独立核验或结算。"
        else:
            message = "本场没有可评分的前瞻冻结记录。"
        game["trace_answer"] = message + (" " + game["market_note"] if game.get("market_note") else "")
