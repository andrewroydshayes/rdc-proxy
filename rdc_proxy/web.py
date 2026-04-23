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
    # dashboard_version is baked into the served HTML. If the user's browser
    # has a stale cached copy, the dashboard version shown in the UI will
    # lag behind the /api/status version — an immediate visible tell.
    return render_template("status.html", dashboard_version=__version__)


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


# Top-level config keys the UI is allowed to change. Anything outside this set
# is ignored on POST so the dashboard can't rewrite ports, config_dir, etc.
_SETTABLE = {
    "internet_stable_before_proxy_s",
    "internet_check_interval_s",
    "oil_check_runtime_hours",
    "stale_seconds",
    "rdc_ip",
    "cloud_dns",
}

# Per-gauge fields the UI can edit. `min`/`max` are the scale; `green`/`yellow`
# are [lo, hi] bands; `unit`/`label` are cosmetic.
_GAUGE_FIELDS = {"min", "max", "green", "yellow", "unit", "label"}


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        out = {k: CFG.get(k) for k in _SETTABLE}
        out["gauges"] = {k: dict(v) for k, v in CFG.get("gauges", {}).items()}
        return jsonify(out)
    data = request.get_json(silent=True) or {}
    updated = {}
    for k, v in data.items():
        if k == "gauges" and isinstance(v, dict):
            gauges = CFG.setdefault("gauges", {})
            merged = {}
            for gname, gpatch in v.items():
                if not isinstance(gpatch, dict) or gname not in gauges:
                    continue
                wrote = False
                for field, val in gpatch.items():
                    if field in _GAUGE_FIELDS:
                        gauges[gname][field] = val
                        wrote = True
                if wrote:
                    merged[gname] = dict(gauges[gname])
            if merged:
                updated["gauges"] = merged
        elif k in _SETTABLE:
            CFG[k] = v
            updated[k] = v
    if updated:
        save_config()
    return jsonify({"ok": True, "updated": updated})


def run_web():
    app.run(host="0.0.0.0", port=CFG.get("web_port", 80), threaded=True, use_reloader=False)
