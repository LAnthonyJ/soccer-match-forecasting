from flask import Flask, abort, jsonify, request, send_from_directory

from . import operations, service
from .evaluation import public_ledger


def create_app():
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 8192
    app.json.ensure_ascii = False

    @app.get("/")
    def index():
        return send_from_directory(service.STATIC, "index.html")

    @app.get("/assets/<path:name>")
    def assets(name):
        return send_from_directory(service.STATIC/"assets",name)

    @app.get("/api/status")
    def status():
        return jsonify(operations.local_status())

    @app.get("/api/dashboard")
    def dashboard():
        try:
            return jsonify(service.dashboard(request.args.get("season")))
        except ValueError as exc:
            return jsonify(error=str(exc)),400

    @app.get("/api/fixtures/<fixture_id>")
    def fixture(fixture_id):
        try:
            return jsonify(service.prediction(fixture_id))
        except KeyError:
            abort(404)

    @app.get("/api/ledger")
    def ledger():
        return jsonify(public_ledger())

    @app.get("/api/history-rows")
    def history_rows():
        return jsonify(service.historical_rows())

    @app.post("/api/ask")
    def ask():
        body = request.get_json(silent=True)
        if not isinstance(body,dict):
            return jsonify(error="需要 JSON 对象"),400
        try:
            return jsonify(service.ask(body.get("question"),body.get("fixture_id"),body.get("timezone","Europe/London")))
        except ValueError as exc:
            return jsonify(error=str(exc)),400

    return app
