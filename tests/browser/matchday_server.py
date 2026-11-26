"""Exercise the production handlers on an isolated, deterministic matchweek."""
from datetime import datetime, timezone
import csv
import os
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from epl_agent import data, service
from epl_agent.__main__ import export_site
from epl_agent.web import create_app
from werkzeug.serving import make_server
from flask import send_from_directory


class Matchday(datetime):
    @classmethod
    def now(cls, tz=None):
        kickoff_week = cls(2026, 11, 26, 8, tzinfo=timezone.utc)
        return kickoff_week.astimezone(tz) if tz else kickoff_week.replace(tzinfo=None)


def seed(*, audited_round=False):
    directory = Path(os.environ["EPL_AGENT_HOME"])
    directory.mkdir(exist_ok=False)
    games = [m for m in data.load_matches() if m["season"] == "2026-27"]
    if audited_round:
        with Path(__file__).with_name("matchday12-odbl.csv").open(encoding="utf8") as source:
            updates = {row["id"]: row for row in csv.DictReader(source)}
    for game in games:
        if audited_round:
            if game["id"] in updates:
                row = updates[game["id"]]
                game.update(date=row["date"], kickoff_utc=row["kickoff_utc"],
                            hg=int(row["hg"]) if row["hg"] else None,
                            ag=int(row["ag"]) if row["ag"] else None)
            if game["round"] == 12:
                game["schedule_status"] = "verified"
        elif game["round"] == 12:
            game["schedule_status"] = "verified"
            if game["home"] == "Nottingham Forest":
                game.update(date="2026-11-27", kickoff_utc="2026-11-27T20:00:00.000000Z")
            elif game["home"] == "Arsenal":
                game.update(date="2026-11-29", kickoff_utc="2026-11-29T16:30:00.000000Z")
            elif game["home"] == "Everton":
                game.update(kickoff_utc=None, schedule_status="conflict")
    with sqlite3.connect(directory / "fixtures.sqlite3") as db:
        data._prepare_versions(db)
        data._record_view(db, "2026-27", games, source_commit="offline-browser-fixture",
                          observed="2026-11-25T12:00:00+00:00")


if __name__ == "__main__":
    with patch("epl_agent.service.datetime", Matchday), patch("epl_agent.fan.datetime", Matchday), \
            patch("epl_agent.analysis.datetime", Matchday):
        if sys.argv[1] == "export":
            seed()
            export_site(sys.argv[2])
        else:
            app = create_app()
            if sys.argv[1] == "matrix":
                site = Path(sys.argv[2]).resolve()
                if os.environ.get("EPL_MATRIX_FRONTEND"):
                    service.STATIC = Path(os.environ["EPL_MATRIX_FRONTEND"]).resolve()
                seed(audited_round=True)
                export_site(site)

                @app.get("/static-batch/")
                @app.get("/static-batch/<path:asset>")
                def matrix_site(asset="index.html"):
                    return send_from_directory(site, asset)
            server = make_server("127.0.0.1", 0, app, threaded=True)
            print(server.server_port, flush=True)
            server.serve_forever()
