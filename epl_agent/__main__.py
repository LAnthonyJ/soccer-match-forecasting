import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

from . import data, models, service
from .evaluation import formal_report, public_ledger
from .market import canonical, collect, database, freeze_protocol, instant, root_dir


def output(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False))


def register():
    now = datetime.now(timezone.utc)
    path = Path(service.model_dir() or models.MODEL_DIR)
    manifest = json.loads((path/"manifest.json").read_text("utf8"))
    eligible = [s for s in manifest["seasons"] if instant(s["valid_from"])<=now]
    if not eligible:
        raise ValueError("No model available at this time")
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        return freeze_protocol(db,eligible[-1]["version"],now)


def capture(witness=False):
    from .evidence import publish
    from .market import read_value
    register()
    def post_pending():
        try:
            publish()
            return None
        except (OSError,ValueError) as exc:
            return type(exc).__name__
    with database() as db:
        witnessed = read_value(db,"registration_evidence")
    witness_error = post_pending() if witness and not witnessed else None
    matches = service.fixtures()
    def frozen_model(match,when,version):
        dirs = [service.model_dir() or models.MODEL_DIR,models.MODEL_DIR]
        dirs.extend(p.parent for p in sorted((root_dir()/"models").glob("*/.complete")))
        for directory in dict.fromkeys(dirs):
            if (directory/(version+".json.gz")).is_file():
                return models.forecast(match,matches,when,model_dir=directory,version=version)
        return None
    result = collect(matches,frozen_model)
    if witness and not witness_error:
        with database() as db:
            pending = db.execute("SELECT 1 FROM captures WHERE hash IS NOT NULL AND evidence IS NULL LIMIT 1").fetchone()
        if pending:
            witness_error = post_pending()
    if witness_error:
        result["publication_error"] = witness_error
    return result


def export_site(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve().is_relative_to(service.STATIC.resolve()):
        raise ValueError("Choose a separate output directory for the Pages build.")
    shutil.copytree(service.STATIC,directory,dirs_exist_ok=True)
    index = directory/"index.html"
    index.write_text(index.read_text("utf8").replace('name="data-mode" content="server"',
                                        'name="data-mode" content="static"'),encoding="utf8")
    (directory/"dashboard.json").write_text(canonical(service.dashboard(static=True)),encoding="utf8")
    (directory/"ledger.json").write_text(canonical(public_ledger()),encoding="utf8")
    (directory/".nojekyll").touch()
    return str(directory.resolve())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="epl-agent",description="Open EPL prediction workbench")
    commands = parser.add_subparsers(dest="command",required=True)
    demo = commands.add_parser("demo"); demo.add_argument("--port",type=int,default=5051)
    sync = commands.add_parser("sync"); sync.add_argument("--season")
    train = commands.add_parser("train"); train.add_argument("--output",type=Path)
    predict = commands.add_parser("predict"); predict.add_argument("match_id")
    bt = commands.add_parser("backtest"); bt.add_argument("--formal",action="store_true")
    bt.add_argument("--recompute",action="store_true"); bt.add_argument("--output",type=Path)
    commands.add_parser("register")
    cap = commands.add_parser("capture"); cap.add_argument("--publish",action="store_true")
    worker = commands.add_parser("worker"); worker.add_argument("--publish",action="store_true")
    commands.add_parser("ledger")
    commands.add_parser("witness")
    commands.add_parser("audit")
    candidate = commands.add_parser("candidate")
    candidate.add_argument("action", choices=("train","score","predict"))
    candidate.add_argument("--fixture")
    exp = commands.add_parser("export"); exp.add_argument("directory")
    qa = commands.add_parser("ask"); qa.add_argument("question"); qa.add_argument("--fixture")
    args = parser.parse_args(argv)
    from .web import create_app
    try:
        if args.command=="demo":
            create_app().run(host="127.0.0.1",port=args.port,debug=False,use_reloader=False)
        elif args.command=="sync":
            output(data.sync(root_dir()/"fixtures.sqlite3",season=args.season))
        elif args.command=="train":
            target = args.output or root_dir()/"models"
            target.mkdir(parents=True,exist_ok=True)
            # Publish completed artifacts before the marker selects custom weights.
            with tempfile.TemporaryDirectory(dir=target) as stage:
                matches = service.fixtures()
                build = Path(stage)/"model"
                models.train(matches,build)
                models.backtest(matches,build)
                signature = hashlib.sha256()
                for f in sorted(build.iterdir()):
                    signature.update(f.name.encode())
                    signature.update(hashlib.sha256(f.read_bytes()).digest())
                key = signature.hexdigest()
                (build/".complete").touch()
                try:
                    build.rename(target/key)
                except OSError:
                    if not (target/key/".complete").exists():
                        raise
                pointer = Path(stage)/"ACTIVE"
                pointer.write_text(key,encoding="ascii")
                os.replace(pointer,target/"ACTIVE")
            output({"models":str(target),"fixtures":len(matches)})
        elif args.command=="predict":
            output(service.prediction(args.match_id))
        elif args.command=="ask":
            output(service.ask(args.question,args.fixture))
        elif args.command=="backtest":
            if args.formal:
                report = formal_report(service.fixtures(),public_ledger())
            elif args.recompute:
                with tempfile.TemporaryDirectory() as tmp:
                    matches = service.fixtures()
                    models.train(matches,tmp)
                    models.backtest(matches,tmp)
                    report = models.load_report(tmp)
            else:
                report = service.history()[1]
            if args.output:
                args.output.write_text(canonical(report),encoding="utf8")
            else:
                output(report)
        elif args.command=="register":
            output(register())
        elif args.command=="capture":
            output(capture(args.publish))
        elif args.command=="worker":
            last_sync = float("-inf")
            while True:
                if time.monotonic()-last_sync>=3600:
                    try:
                        data.sync(root_dir()/"fixtures.sqlite3")
                    except Exception as exc:
                        print("Fixture sync failed:",type(exc).__name__,file=sys.stderr,flush=True)
                    last_sync = time.monotonic()
                try:
                    output(capture(args.publish))
                except Exception as exc:
                    print("Capture failed:",type(exc).__name__,file=sys.stderr,flush=True)
                time.sleep(60)
        elif args.command=="ledger":
            output(public_ledger())
        elif args.command=="witness":
            from .evidence import publish
            output(publish())
        elif args.command=="audit":
            from .evidence import audit_and_store
            result = audit_and_store()
            output(result)
            if not result["ok"]:
                return 1
        elif args.command=="candidate":
            from . import challenger
            if args.action=="predict" and not args.fixture:
                parser.error("candidate predict requires --fixture")
            result = (challenger.train_candidate() if args.action=="train" else
                      challenger.score_candidate() if args.action=="score" else
                      challenger.forecast_candidate(args.fixture))
            output(result)
        elif args.command=="export":
            print(export_site(args.directory))
    except (KeyError,ValueError,OSError) as exc:
        parser.exit(2,f"{exc}\n")


if __name__=="__main__":
    sys.exit(main())
