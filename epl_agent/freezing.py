"""Local freeze entry points shared by the worker and CLI."""
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

from . import models, service
from .market import collect, database, freeze_protocol, read_value, root_dir


def _latest(directory, now):
    manifest = json.loads((directory / "manifest.json").read_text("utf8"))
    return models._select_artifact(manifest, now)


def register():
    now = datetime.now(timezone.utc)
    directory = Path(service.model_dir() or models.MODEL_DIR)
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        return read_value(db, "registration") or freeze_protocol(db, _latest(directory,now), now)


def _stamp(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _fixture_stamp(folder):
    return tuple(_stamp(folder/name) for name in ("fixtures.sqlite3","fixtures.sqlite3-wal"))


@lru_cache(maxsize=1)
def _prepared(folder, stamp, directory, version, model_stamp):
    matches = service.fixtures()
    inputs = service.model_inputs()
    context = models.ForecastContext.prepare(matches, directory, version=version, **inputs)
    if _fixture_stamp(folder) != stamp:
        raise RuntimeError("Fixture revisions changed while preparing the model")
    return matches, context


def prepare(version=None):
    """Warm the observed fixture history and frozen weights without contacting providers."""
    folder = root_dir()
    active = Path(service.model_dir() or models.MODEL_DIR)
    if version is None:
        ledger = folder/"market.sqlite3"
        if ledger.exists():
            with closing(sqlite3.connect(ledger.resolve().as_uri()+"?mode=ro",uri=True)) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='state'").fetchone():
                    version = (read_value(db,"registration") or {}).get("model")
        version = version or _latest(active, datetime.now(timezone.utc))
    directories = [active, models.MODEL_DIR]
    directories.extend(p.parent for p in sorted((folder / "models").glob("*/.complete")))
    for directory in dict.fromkeys(directories):
        artifact = directory/(version+".json.gz")
        if artifact.is_file():
            stamp = _fixture_stamp(folder)
            return _prepared(folder,stamp,directory,version,(_stamp(artifact),_stamp(directory/"manifest.json")))
    raise ValueError("Registered model artifact is unavailable")


def capture(witness=False, *, clock=None, get=None):
    registration = register()
    matches, context = prepare(registration["model"])

    def batch(targets, when, version):
        return models.forecast_many(targets,matches,when,version=version,context=context)

    def single(match, when, version):
        return batch([match],when,version).get(match["id"])

    options = {}
    if clock is not None:
        options["clock"] = clock
    if get is not None:
        options["get"] = get
    result = collect(matches,single,predict_many=batch,**options)
    if witness:
        from .evidence import publish
        try:
            with database() as db:
                pending = (not read_value(db,"registration_evidence") or db.execute(
                    "SELECT 1 FROM captures WHERE hash IS NOT NULL AND evidence IS NULL LIMIT 1").fetchone())
            if pending:
                publish()
        except (OSError,ValueError) as exc:
            result["publication_error"] = type(exc).__name__
    return result
