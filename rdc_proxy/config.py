"""Defaults and config loader for rdc-proxy.

Note: load_config MUTATES the module-level CFG dict in place (clear + update)
rather than rebinding it. This is important because other modules do
`from rdc_proxy.config import CFG` — they hold a reference to this dict, and
rebinding would leave them pointing at an empty stale object.
"""

import json
import os

DEFAULT_CONFIG = {
    "web_port": 80,
    "proxy_port": 5253,
    "rdc_ip": "10.0.0.50",
    "cloud_dns": "devices.kohler.com",
    "cloud_port": 5253,
    "internet_check_interval_s": 30,
    "internet_stable_before_proxy_s": 300,
    "oil_check_runtime_hours": 24,
    "config_dir": "/etc/rdc-proxy",
    "gauges": {
        "battery_v": {"min": 10.0, "max": 16.0, "green": [12.0, 14.0], "yellow": [11.5, 14.5], "unit": "V", "label": "Battery Voltage"},
        "utility_v": {"min": 200, "max": 280, "green": [228, 252], "yellow": [216, 264], "unit": "V", "label": "Utility Voltage"},
        "generator_v": {"min": 0, "max": 300, "green": [228, 252], "yellow": [216, 264], "unit": "V", "label": "Generator Voltage"},
        "controller_temp_f": {"min": 30, "max": 220, "green": [30, 120], "yellow": [120, 160], "unit": "\u00b0F", "label": "Controller Temp"},
        "oil_temp_f": {"min": 30, "max": 350, "green": [30, 200], "yellow": [200, 240], "unit": "\u00b0F", "label": "Oil Temp"},
        "rpm": {"min": 0, "max": 4500, "green": [3540, 3660], "yellow": [3400, 3800], "unit": "RPM", "label": "Engine Speed"},
        "frequency_hz": {"green": [59.5, 60.5], "yellow": [59.0, 61.0], "unit": "Hz", "label": "Frequency"},
        "utility_hz": {"green": [59.5, 60.5], "yellow": [59.0, 61.0], "unit": "Hz", "label": "Utility Frequency"},
    },
}

CFG = {}
CONFIG_FILE = None


def deep_merge_gauges(defaults, user):
    merged = {**defaults}
    for k, v in user.items():
        merged[k] = {**merged[k], **v} if k in merged else v
    return merged


def _install(new_cfg):
    CFG.clear()
    CFG.update(new_cfg)


def load_config(path):
    global CONFIG_FILE
    CONFIG_FILE = path
    if os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        merged = {**DEFAULT_CONFIG, **user}
        if "gauges" in user:
            merged["gauges"] = deep_merge_gauges(DEFAULT_CONFIG["gauges"], user["gauges"])
        _install(merged)
        print(f"[config] loaded from {path}", flush=True)
    else:
        _install(DEFAULT_CONFIG)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"[config] generated defaults at {path}", flush=True)
    return CFG


def save_config():
    """Persist the live CFG back to CONFIG_FILE. In-memory updates to CFG
    take effect on the next proxy loop iteration without a service restart."""
    if not CONFIG_FILE:
        raise RuntimeError("save_config called before load_config")
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(CFG, f, indent=2)
    print(f"[config] saved to {CONFIG_FILE}", flush=True)
