"""Config loader: defaults file creation, overlay merge, deep gauge merge."""

import json
import os
from pathlib import Path

from rdc_proxy import config as cfg_mod


def test_creates_defaults_if_missing(tmp_path):
    p = tmp_path / "config.json"
    cfg = cfg_mod.load_config(str(p))
    assert p.exists()
    assert cfg["proxy_port"] == 5253
    # File content matches defaults
    on_disk = json.loads(p.read_text())
    assert on_disk["cloud_dns"] == "devices.kohler.com"


def test_user_override_replaces_top_level(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"proxy_port": 9999, "web_port": 8080}))
    cfg = cfg_mod.load_config(str(p))
    assert cfg["proxy_port"] == 9999
    assert cfg["web_port"] == 8080
    # Un-overridden keys still come from defaults
    assert cfg["cloud_port"] == 5253


def test_deep_merge_gauges_preserves_unchanged_keys(tmp_path):
    """User overrides ONE field of ONE gauge. The other fields of that gauge
    AND all other gauges must remain untouched."""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "gauges": {
            "battery_v": {"min": 11.0}  # override only min
        }
    }))
    cfg = cfg_mod.load_config(str(p))
    bv = cfg["gauges"]["battery_v"]
    assert bv["min"] == 11.0            # overridden
    assert bv["max"] == 16.0            # still default
    assert bv["green"] == [12.0, 14.0]  # still default
    # A completely un-overridden gauge should be present in full
    assert cfg["gauges"]["rpm"]["max"] == 4500


def test_user_can_add_new_gauge(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "gauges": {
            "custom": {"label": "Custom", "unit": "X"}
        }
    }))
    cfg = cfg_mod.load_config(str(p))
    assert cfg["gauges"]["custom"] == {"label": "Custom", "unit": "X"}
    # Default gauges still present
    assert "battery_v" in cfg["gauges"]
