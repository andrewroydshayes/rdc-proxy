"""Flask dashboard server."""

import os

from flask import Flask, jsonify, render_template

from rdc_proxy.config import CFG
from rdc_proxy.dashboard import get_dashboard_state, snapshot_decoded
from rdc_proxy.state import STATE

_PKG = os.path.dirname(__file__)
app = Flask(
    __name__,
    template_folder=os.path.join(_PKG, "templates"),
    static_folder=os.path.join(_PKG, "static"),
)


@app.route("/")
def index():
    return render_template("status.html")


@app.route("/api/status")
def api_status():
    snap = STATE.snapshot()
    dash_current, dash_history = get_dashboard_state()
    snap["dash_current"] = dash_current
    snap["dash_history"] = dash_history
    snap["decoded"] = snapshot_decoded()
    return jsonify(snap)


@app.route("/api/reset-oil-check", methods=["POST"])
def reset_oil():
    STATE.reset_oil_check()
    return jsonify({"ok": True})


def run_web():
    app.run(host="0.0.0.0", port=CFG.get("web_port", 80), threaded=True, use_reloader=False)
