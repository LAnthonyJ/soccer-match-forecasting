"""Prepare one verified build for the live gateway or its read-only report mirror."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

import requests


def origin(value):
    parts = urlsplit(value.rstrip("/"))
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise ValueError("Expected a public HTTPS origin")
    return value.rstrip("/")


def cloud_site(site, target):
    if not re.fullmatch("[0-9a-f]{40}",(site/"public-data-commit.txt").read_text().strip()):
        raise ValueError("A verified public data commit is required")
    shutil.copytree(site,target,dirs_exist_ok=False)
    index = target/"index.html"
    html = index.read_text("utf8")
    marker = 'name="data-mode" content="static"'
    if marker not in html:
        raise ValueError("Expected a static Pages export")
    index.write_text(html.replace("</head>",'<meta name="analysis-mode" content="gateway"></head>'),encoding="utf8")


def fetch(url):
    response = requests.get(url,timeout=(4,25),allow_redirects=False)
    if response.status_code==404:
        return None
    response.raise_for_status()
    return response.json()


def checked_report(report, packet):
    if (not isinstance(report,dict) or report.get("source")!="deepseek" or report.get("view","prematch")!="prematch"
            or report.get("model")!="deepseek-flash" or report.get("fact_hash")!=packet["fact_hash"]
            or report.get("fixture_id")!=packet["fixture_id"]):
        return None
    try:
        generated = datetime.fromisoformat(report["generated_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if generated.tzinfo is None:
        return None
    facts={f["id"]:f for f in packet["facts"] if f["stage"]=="prematch"}
    titles={"fixture":"赛程与时点","prediction":"赛果判断","market":"市场比较","evaluation":"模型成绩"}
    supplemental={"赛前判断":{"probabilities","favourite"}, "球队近况":{"home_form","away_form","expected_goals"},
                  "市场对照":{"market","market_gap","blended_gap"}, "资料边界":{"injuries"}}
    clean=[]
    used=set()
    sections=report.get("sections")
    if not isinstance(sections,list) or not 1 <= len(sections) <= 8:
        return None
    for entry in sections:
        if not isinstance(entry,dict):
            return None
        ids=entry.get("fact_ids",[])
        if (not isinstance(ids,list) or not 1 <= len(ids) <= 5
                or any(not isinstance(k,str) or k not in facts or k in used for k in ids)
                or len(set(ids))!=len(ids)):
            return None
        selected=[facts[k] for k in ids]
        expected=" ".join(f["text"] for f in selected)
        title=entry.get("title")
        if not isinstance(title,str) or entry.get("text")!=expected:
            return None
        if title in supplemental:
            if not set(ids)<=supplemental[title]:
                return None
        elif any(title!=titles[f["tool"]] for f in selected):
            return None
        clean.append({"title":title,"text":expected,"fact_ids":ids})
        used.update(ids)
    if "probabilities" not in used:
        return None
    return {"fixture_id":packet["fixture_id"],"fact_hash":packet["fact_hash"],"as_of":packet["as_of"],
            "source":"deepseek","model":report["model"],"generated_at":report["generated_at"],
            "sections":clean,"answer":"\n\n".join(s["text"] for s in clean)}


def mirror(site, gateway, previous=None, *, warm=False):
    gateway = origin(gateway)
    dest = site/"analysis"/"reports"
    dest.mkdir(exist_ok=True)
    previous_index = {}
    if previous:
        # The project path is required for a GitHub project Pages site.
        if not re.fullmatch(r"https://[\w-]+\.github\.io/[\w.-]+/?",previous):
            raise ValueError("Expected the existing project Pages URL")
        previous = previous.rstrip("/")+"/analysis/reports"
        previous_index = fetch(previous+"/index.json") or {}
    current = json.loads((site/"analysis"/"current.json").read_text("utf8"))["fixture_ids"]
    ids = sorted(set(previous_index).union(current))
    if any(not re.fullmatch(r"20\d\d-\d\d_[a-zA-Z0-9_-]{1,150}",fid) for fid in ids):
        raise ValueError("Invalid fixture in report index")
    archives = {}
    expected = {}
    for fid in ids:
        single = site/"analysis"/"facts"/(fid+".json")
        if single.exists():
            expected[fid]=json.loads(single.read_text("utf8"))
        else:
            season = fid.split("_",1)[0]
            path = site/"analysis"/"archive"/(season+".json")
            if season not in archives and path.exists():
                archives[season]=json.loads(path.read_text("utf8"))
            row = archives.get(season,{}).get(fid)
            if row:
                expected[fid]=row

    def retrieve(fid):
        packet=expected.get(fid)
        if not packet:
            return
        version=packet["fact_hash"]
        report = None
        if previous and previous_index.get(fid)==version:
            report=fetch(previous+"/"+fid+".json")
        if not report and fid in current:
            # Only the verified, published round is eligible to trigger generation.
            try:
                report=fetch(gateway+"/api/matches/"+fid+"/analysis"+("" if warm else "?cached=1"))
            except (requests.RequestException,ValueError):
                return
        result=checked_report(report,packet)
        if not result:
            return
        (dest/(fid+".json")).write_text(json.dumps(result,ensure_ascii=False),encoding="utf8")
        return fid,version

    with ThreadPoolExecutor(max_workers=3) as pool:
        manifest=dict(item for item in pool.map(retrieve,ids) if item)
    (dest/"index.json").write_text(json.dumps(manifest,sort_keys=True),encoding="utf8")
    return len(manifest)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("cloud","mirror"))
    parser.add_argument("site",type=Path)
    parser.add_argument("--target",type=Path,default=Path("cloudflare/site"))
    parser.add_argument("--origin",default=os.getenv("EPL_ANALYSIS_ORIGIN"))
    parser.add_argument("--previous",default=os.getenv("EPL_PREVIOUS_PAGES"))
    parser.add_argument("--warm",action="store_true")
    args=parser.parse_args()
    if args.action=="cloud":
        cloud_site(args.site,args.target)
    else:
        print("Mirrored shared reports:",mirror(args.site,args.origin,args.previous,warm=args.warm))
