"""Private Windows collector operations. Tokens are never command-line arguments."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid


def backup(home, destination):
    if any(destination.resolve().is_relative_to((home/name).resolve()) for name in ("models","public-export")):
        raise ValueError("Choose a backup directory outside models and public-export")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    staging = destination / (".incomplete-" + uuid.uuid4().hex)
    staging.mkdir()
    for filename in ("fixtures.sqlite3", "market.sqlite3", "operations.sqlite3"):
        source = home / filename
        if source.exists():
            with sqlite3.connect(source.resolve().as_uri()+"?mode=ro", uri=True) as reader:
                with sqlite3.connect(staging/filename) as writer:
                    reader.backup(writer, pages=256)
                    if writer.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Backup database failed integrity check")
                writer.close()
            reader.close()
    for directory in ("models", "public-export"):
        if (home/directory).is_dir():
            shutil.copytree(home/directory, staging/directory)
    if not any(staging.iterdir()):
        staging.rmdir()
        raise ValueError("No existing collector data found at the selected path")
    files = {}
    for item in sorted(staging.rglob("*")):
        if item.is_file():
            with item.open("rb") as stream:
                files[item.relative_to(staging).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    (staging/"backup-manifest.json").write_text(json.dumps(files, indent=2), encoding="utf8")
    target = destination/(stamp+"-"+uuid.uuid4().hex[:8])
    staging.rename(target)
    return {"backup":str(target), "verified_files":len(files)}


def github_credentials(repo, use_git):
    if use_git and (not os.getenv("EPL_GITHUB_TOKEN") or not os.getenv("EPL_SNAPSHOT_GITHUB_TOKEN")):
        result = subprocess.run(
            ["git","-c","credential.interactive=false","credential","fill"],
            input=f"protocol=https\nhost=github.com\npath={repo}.git\n\n",
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "GIT_TERMINAL_PROMPT":"0", "GCM_INTERACTIVE":"Never"})
        fields = dict(line.split("=",1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode or not fields.get("password"):
            raise ValueError("Git Credential Manager did not return a GitHub credential for this task identity")
        for key in ("EPL_GITHUB_TOKEN","EPL_SNAPSHOT_GITHUB_TOKEN"):
            if not os.getenv(key):
                os.environ[key] = fields["password"]
    if not os.getenv("EPL_AUDIT_GITHUB_TOKEN") and os.getenv("EPL_GITHUB_TOKEN"):
        os.environ["EPL_AUDIT_GITHUB_TOKEN"] = os.environ["EPL_GITHUB_TOKEN"]


def check(home, repo, issue):
    import requests
    from epl_agent import __version__, models
    result = {"python":sys.executable, "version":__version__, "data_root":str(home),
              "model_manifest":(models.MODEL_DIR/"manifest.json").is_file(),
              "odds_configured":bool(os.getenv("ODDS_API_KEY"))}
    for filename in ("fixtures.sqlite3","market.sqlite3","operations.sqlite3"):
        database = home/filename
        if database.exists():
            with sqlite3.connect(database.resolve().as_uri()+"?mode=ro",uri=True) as conn:
                conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
            conn.close()
    with requests.Session() as client:
        client.trust_env = False
        for name, token_name in (("witness","EPL_GITHUB_TOKEN"),("snapshot","EPL_SNAPSHOT_GITHUB_TOKEN")):
            token = os.getenv(token_name)
            if not token:
                raise ValueError(name+" credential is unavailable")
            headers = {"Authorization":"Bearer "+token, "Accept":"application/vnd.github+json"}
            base = f"https://api.github.com/repos/{repo}"
            permissions = client.get(base,headers=headers,timeout=10,allow_redirects=False)
            permissions.raise_for_status()
            if permissions.json().get("private") is not False:
                raise ValueError("Configured repository is not public")
            if name=="witness":
                response = client.get(base+f"/issues/{issue}",headers=headers,timeout=10,allow_redirects=False)
                response.raise_for_status()
                if response.json().get("state")!="open" or "pull_request" in response.json():
                    raise ValueError("Expected an open evidence issue")
                result["witness_issue_readable"] = True
            else:
                result["repository_push_access"] = bool(permissions.json().get("permissions",{}).get("push"))
                if not result["repository_push_access"]:
                    raise ValueError("Snapshot identity has no push access to the configured repository")
    return result


def run_worker(home):
    logdir = home/"collector-logs"
    logdir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("epl.collector")
    log.setLevel(logging.INFO)
    sink = RotatingFileHandler(logdir/"worker.log", maxBytes=4_000_000, backupCount=4, encoding="utf8")
    sink.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(sink)
    try:
        command = [sys.executable, "-u", "-m", "epl_agent", "worker"]
        if os.getenv("EPL_GITHUB_TOKEN"):
            command.append("--publish")
        if os.getenv("EPL_SNAPSHOT_GITHUB_TOKEN"):
            command.append("--publish-snapshot")
        if "--publish-snapshot" not in command or "--publish" not in command:
            from epl_agent import operations
            operations.set_values(home, publication_status="credentials_missing")
            log.warning("GitHub credentials unavailable for one or more publication steps. "
                        "Local collection continues. Restart the collector after restoring credentials.")
        with subprocess.Popen(command,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf8", errors="replace") as child:
            for line in child.stdout:
                log.info("%s",line.rstrip())
            return child.wait()
    finally:
        sink.close()
        log.removeHandler(sink)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("backup","check","run","stop"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--backup-base", type=Path)
    parser.add_argument("--git-credential", action="store_true")
    args = parser.parse_args()
    if args.action == "stop":
        from epl_agent.worker import request_stop
        request_stop(args.root)
        print(json.dumps({"stopped": True, "lock_released": True}))
        return 0
    if args.action=="backup":
        print(json.dumps(backup(args.root,args.backup_base or args.root/"collector-backups"), ensure_ascii=True))
        return 0
    repo = os.environ["EPL_EVIDENCE_REPO"]
    issue = os.environ["EPL_EVIDENCE_ISSUE"]
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+",repo) or not re.fullmatch(r"[1-9][0-9]*",issue):
        raise ValueError("Invalid GitHub repository or issue")
    if args.action=="check":
        github_credentials(repo,args.git_credential)
        print(json.dumps(check(args.root,repo,issue),ensure_ascii=True))
        return 0
    try:
        github_credentials(repo,args.git_credential)
    except (OSError,ValueError,subprocess.SubprocessError):
        # Scheduled identities may temporarily lose their Git credential store.
        # Local deadlines remain binding while the publication lanes are offline.
        pass
    return run_worker(args.root)


if __name__=="__main__":
    try:
        sys.exit(main())
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,sqlite3.Error) as exc:
        # No response bodies, HTTP headers, git stderr or credential output in logs.
        print(f"Collector operation failed ({type(exc).__name__}).", file=sys.stderr)
        if isinstance(exc,ValueError) and type(exc).__module__=="builtins":
            print(str(exc), file=sys.stderr)
        sys.exit(1)
