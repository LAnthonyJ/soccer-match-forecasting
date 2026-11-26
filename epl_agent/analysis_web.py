"""Local demo endpoints. A configured gateway shares the same budget as the public app."""
import os
import re
from urllib.parse import urlsplit

import requests
from flask import Blueprint, abort, jsonify, request

from . import analysis, fan_questions, service

bp = Blueprint("analysis",__name__)


def _gateway(path, body=None):
    origin = os.environ.get("EPL_ANALYSIS_ORIGIN","").rstrip("/")
    if not origin:
        return None
    url = urlsplit(origin)
    if (url.scheme != "https" and not (url.scheme=="http" and url.hostname in {"localhost","127.0.0.1","::1"})) or (
            not url.hostname or url.username or url.password or url.path or url.query or url.fragment):
        return None
    try:
        result = requests.request("GET" if body is None else "POST",origin+path,json=body,
                                  timeout=(3,20),allow_redirects=False)
        if result.status_code not in (200,409,429):
            return None
        data = result.json()
        return (data,result.status_code) if isinstance(data,dict) else None
    except (requests.RequestException,ValueError):
        return None


@bp.get("/api/matches/<fid>/analysis")
def read_analysis(fid):
    if not analysis.FIXTURE_ID.fullmatch(fid):
        abort(404)
    try:
        bundle = analysis.live_packet(fid)
    except KeyError:
        abort(404)
    remote = _gateway("/api/matches/"+fid+"/analysis")
    if remote and remote[1]==200 and (remote[0].get("fixture_id"),remote[0].get("fact_hash"))==(fid,bundle["fact_hash"]):
        return jsonify({**remote[0],"available_topics":bundle.get("available_topics")})
    return jsonify(analysis.template_report(bundle, "gateway_snapshot_differs" if remote else "provider_unavailable"))


@bp.get("/analysis/facts/<fid>.json")
def read_facts(fid):
    if not analysis.FIXTURE_ID.fullmatch(fid):
        abort(404)
    try:
        return jsonify(analysis.live_packet(fid))
    except KeyError:
        abort(404)


@bp.post("/api/analysis/ask")
def followup():
    body = request.get_json(silent=True)
    if not isinstance(body,dict):
        return jsonify(error="需要 JSON 对象"),400
    fid,question,version = (body.get(k) for k in ("fixture_id","question","fact_hash"))
    history = body.get("history",[])
    if (not isinstance(fid,str) or not analysis.FIXTURE_ID.fullmatch(fid)
            or not isinstance(question,str) or not 0 < len(question.strip()) <= 350
            or not isinstance(version,str) or not re.fullmatch("[0-9a-f]{64}",version)
            or not isinstance(history,list) or len(history)>6
            or any(not isinstance(m,dict) or m.get("role") not in ("user","assistant")
                   or not isinstance(m.get("content"),str) or len(m["content"])>500 for m in history)):
        return jsonify(error="比赛、报告版本或追问格式无效"),400
    try:
        bundle = analysis.live_packet(fid)
    except KeyError:
        abort(404)
    if version!=bundle["fact_hash"]:
        return jsonify(error="version_changed",fact_hash=bundle["fact_hash"]),409
    context=body.get("context")
    if not fan_questions.valid_context(context):
        return jsonify(error="无效对话目标"),400
    zone=body.get("timezone","Europe/London")
    if not isinstance(zone,str) or len(zone)>80:
        return jsonify(error="无效时区"),400
    remote = _gateway("/api/analysis/ask",{"fixture_id":fid,"question":question,"fact_hash":version,
                                           "history":history,"timezone":zone,"context":context})
    if remote and (remote[1]!=200 or (remote[0].get("fixture_id"),remote[0].get("fact_hash"))==(fid,version)):
        return jsonify(remote[0]),remote[1]
    try:
        result = fan_questions.reply(bundle,question,context,zone)
    except KeyError:
        return jsonify(error="unknown_fixture"),404
    except ValueError as exc:
        if str(exc)=="context_changed":
            return jsonify(error="context_changed",fact_hash=version),409
        raise
    return jsonify(result)
