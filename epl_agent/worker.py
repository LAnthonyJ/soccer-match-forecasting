"""Schedule independent work lanes and reconcile committed work after a restart."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time

from . import data, evidence, operations, snapshot, source_sync
from .market import database, instant, read_value, root_dir


@contextmanager
def single_worker(root):
    root.mkdir(exist_ok=True, parents=True)
    with (root/"worker.lock").open("a+b") as lock:
        if not lock.tell():
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("An EPL collector already holds this data directory") from exc
        yield


def synchronize(provider,root):
    db_path = root/"fixtures.sqlite3"
    today = datetime.now(data.LONDON).date()
    year = today.year - (today.month < 7)
    current = f"{year}-{str(year+1)[-2:]}"
    # Retain past seasons with unresolved, locally observed fixtures after July rollover.
    past = {m["season"] for m in data.load_matches(db_path) if m.get("observed_at")
            and m["season"]<current and m.get("hg") is None}
    for season in [current,*sorted(past,reverse=True)]:
        result = source_sync.synchronize(db_path,season,(provider,))
        if result["status"] != "ok":
            raise OSError("Fixture source did not return a usable batch")


def witness_pending(root):
    with database(root) as db:
        if not read_value(db,"registration"):
            return
        registration = read_value(db,"registration_evidence")
        waiting = db.execute("SELECT 1 FROM captures WHERE hash IS NOT NULL AND evidence IS NULL LIMIT 1").fetchone()
    if waiting or not registration:
        evidence.publish(root)


def publish_snapshot(root):
    directory = root/"public-export"
    directory.mkdir(parents=True,exist_ok=True)
    pending = directory/".pending-publication"
    if not pending.exists():
        snapshot.export_bundle(directory,root=root)
        pending.touch()
    try:
        sha = snapshot.publish_bundle(directory)
    except ValueError as exc:
        if str(exc) in {"New snapshot must follow the previous public-data commit",
                        "A newer public-data commit is already live"}:
            # Another successful batch superseded this one. Build forward on retry.
            pending.unlink(missing_ok=True)
        raise
    batch = json.loads((directory/snapshot.NAME).read_text("utf8"))["data"]
    operations.record_upload(root,sha,batch)
    pending.unlink(missing_ok=True)


def run(*, publish=False, publish_snapshot_enabled=False, once=False):
    from . import freezing
    root = root_dir()
    # A slow sync, deploy or witness never owns the capture executor.
    jobs = {"capture":(lambda:freezing.capture(False),20),
            "openfootball":(lambda:synchronize(source_sync.PRIMARY,root),3600),
            "openligadb":(lambda:synchronize(source_sync.SECONDARY,root),3600)}
    if publish or publish_snapshot_enabled:
        jobs["witness"] = (lambda:witness_pending(root),10)
    if publish_snapshot_enabled:
        jobs["upload"] = (lambda:publish_snapshot(root),3600)
        jobs["pages"] = (lambda:operations.check_deployment(root),120)
    if not once:
        def warm():
            now = datetime.now(timezone.utc)
            upcoming = now+timedelta(days=1)
            if any(m.get("kickoff_utc") and m.get("hg") is None and
                   upcoming<=instant(m["kickoff_utc"])<=upcoming+timedelta(minutes=35)
                   for m in data.load_matches(root/"fixtures.sqlite3")):
                freezing.prepare()
        jobs["prepare"] = (warm,300)
    due = {key:0 for key in jobs}
    pending, errors, failures, attempted = {}, {}, {}, set()
    heartbeat_due = 0
    with single_worker(root), ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        while True:
            now = time.monotonic()
            if now >= heartbeat_due:
                operations.set_values(root, heartbeat_at=operations.stamp())
                heartbeat_due = now+15
            for name,future in list(pending.items()):
                if not future.done():
                    continue
                del pending[name]
                error = future.exception()
                if error is not None:
                    errors[name] = type(error).__name__
                    failures[name] = failures.get(name,0)+1
                    delay = min(jobs[name][1], 300, 5*(2**min(6,failures[name]-1)))
                    if name in ("upload","pages"):
                        operations.set_values(root,publication_status="publish_failed" if name=="upload" else "check_failed")
                else:
                    errors.pop(name,None)
                    failures.pop(name,None)
                    delay = jobs[name][1]
                    if name=="capture" and "witness" in due and future.result().get("captured",0):
                        due["witness"] = 0
                due[name] = now+delay
                updates = {name+"_last_attempt":operations.stamp(), "job_errors":dict(errors)}
                if error is None:
                    updates[name+"_last_success"] = updates[name+"_last_attempt"]
                if name in source_sync.LICENSES:
                    failed_sources = set(errors).intersection(source_sync.LICENSES)
                    updates["sync_status"] = ("offline" if len(failed_sources)==2 else "partial"
                                              if failed_sources else "ok")
                operations.set_values(root,**updates)
            for name,(work,_) in jobs.items():
                if name in pending or now<due[name] or (once and name in attempted):
                    continue
                # With --once, finish one coherent pass before verifying/posting the batch.
                if once and ((name=="witness" and "capture" in pending)
                             or (name=="upload" and set(pending).intersection({"witness","capture",*source_sync.LICENSES}))
                             or (name=="pages" and ("upload" not in attempted or "upload" in pending))):
                    continue
                attempted.add(name)
                pending[name] = pool.submit(work)
            if once and len(attempted)==len(jobs) and not pending:
                return {"ok":not errors,"errors":errors}
            time.sleep(0.1 if once else 2)
