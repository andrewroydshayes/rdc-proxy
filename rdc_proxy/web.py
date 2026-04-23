"""Flask dashboard server."""

import os

from flask import Flask, jsonify, render_template, request

from rdc_proxy import __version__
from rdc_proxy.config import CFG, save_config
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
    snap["version"] = __version__
    return jsonify(snap)


@app.route("/api/reset-oil-check", methods=["POST"])
def reset_oil():
    STATE.reset_oil_check()
    return jsonify({"ok": True})


# Config keys the UI is allowed to change. Anything outside this set is
# ignored on POST to avoid letting the dashboard rewrite DNS or port values.
_SETTABLE = {"internet_stable_before_proxy_s"}


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify({k: CFG.get(k) for k in _SETTABLE})
    data = request.get_json(silent=True) or {}
    updated = {}
    for k, v in data.items():
        if k in _SETTABLE:
            CFG[k] = v
            updated[k] = v
    if updated:
        save_config()
    return jsonify({"ok": True, "updated": updated})


def run_web():
    app.run(host="0.0.0.0", port=CFG.get("web_port", 80), threaded=True, use_reloader=False)
