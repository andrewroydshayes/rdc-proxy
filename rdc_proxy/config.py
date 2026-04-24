"""Defaults and config loader for rdc-proxy.

Note: load_config MUTATES the module-level CFG dict in place (clear + update)
rather than rebinding it. This is important because other modules do
`from rdc_proxy.config import CFG` — they hold a reference to this dict, and
rebinding would leave them pointing at an empty stale object.
"""

import json
import os

# Per-field, per-mode stale threshold (seconds). snapshot() prunes a field
# when time since its last update exceeds the threshold for the current mode.
# Missing field entry → fall back to stale_seconds. Missing MODE entry inside
# a field → the field is engine-gated / not emitted in that mode → pruned
# from the snapshot regardless of how recent the last value was. Derived
# from real captures on a Kohler RDC2 (see docs/CADENCE-ANALYSIS.md).
STALE_THRESHOLDS_DEFAULTS = {
    "batteryVoltageV":       {"standby": 30,   "exercise": 10,   "running": 10},
    "controllerTempC":       {"standby": 1800, "exercise": 120,  "running": 120},
    "engineFrequencyHz":                       {"exercise": 5,   "running": 5},
    "engineFrequencyHz_2":                     {"exercise": 15,  "running": 15},
    "engineSpeedRpm":                          {"exercise": 5,   "running": 5},
    "engineSpeedRpm_2":                        {"exercise": 5,   "running": 5},
    "generatorVoltageV":                       {"exercise": 30,  "running": 30},
    "generatorVoltageV_2":                     {"exercise": 30,  "running": 30},
    "generatorVoltageV_3":                     {"exercise": 15,  "running": 15},
    "generatorVoltageV_4":                     {"exercise": 15,  "running": 15},
    "lubeOilTempC":          {"standby": 30,   "exercise": 30,   "running": 30},
    "maintHoursSinceLast":                     {"exercise": 30,  "running": 30},
    "timestamp":             {"standby": 5,    "exercise": 5,    "running": 5},
    "totalOperationHours":   {"standby": 600,  "exercise": 10,   "running": 10},
    "totalOperationHours_2": {"standby": 1200, "exercise": 1200, "running": 1200},
    "totalRuntimeHours":                       {"exercise": 1200,"running": 1200},
    "utilityFrequencyHz":    {"standby": 30,   "exercise": 30,   "running": 30},
    "utilityVoltageV":       {"standby": 15,   "exercise": 30,   "running": 30},
    "utilityVoltageV_B":     {"standby": 15,   "exercise": 30,   "running": 30},
    # Session-header fields. The RDC only emits these at TCP connect (plus
    # engineState transitions). -1 = sticky: once the value has been
    # received in this session, it never goes stale. On the next reconnect
    # it re-emits and the value just refreshes; if we never get it, the
    # field stays absent from the snapshot (No Data overlay).
    "engineState":           {"standby": -1, "exercise": -1, "running": -1},
    "loadShed_HVAC_A":       {"standby": -1, "exercise": -1, "running": -1},
    "loadShed_HVAC_B":       {"standby": -1, "exercise": -1, "running": -1},
    "loadShed_Load_A":       {"standby": -1, "exercise": -1, "running": -1},
    "loadShed_Load_B":       {"standby": -1, "exercise": -1, "running": -1},
    "loadShed_Load_C":       {"standby": -1, "exercise": -1, "running": -1},
    "loadShed_Load_D":       {"standby": -1, "exercise": -1, "running": -1},
    "modelCode":             {"standby": -1, "exercise": -1, "running": -1},
    "serialNumber":          {"standby": -1, "exercise": -1, "running": -1},
}

# Per-field, per-mode visibility. False → the dashboard hides the element
# entirely (no gauge, no No-Data overlay) because the field is genuinely not
# meaningful in that mode. True (or absent) → show the element; if the value
# is stale/missing, the No-Data overlay applies. Defaults below hide engine-
# gated fields in standby — they're physically not emitted, so showing a
# perpetual No-Data is noise, not information.
VISIBILITY_DEFAULTS = {
    "standby": {
        "engineSpeedRpm": False, "engineSpeedRpm_2": False,
        "engineFrequencyHz": False, "engineFrequencyHz_2": False,
        "generatorVoltageV": False, "generatorVoltageV_2": False,
        "generatorVoltageV_3": False, "generatorVoltageV_4": False,
        "maintHoursSinceLast": False,
        "totalRuntimeHours": False,
    },
    "exercise": {},
    "running": {},
}

DEFAULT_CONFIG = {
    "web_port": 80,
    "proxy_port": 5253,
    "rdc_ip": "10.0.0.50",
    "cloud_dns": "devices.kohler.com",
    "cloud_port": 5253,
    "internet_check_interval_s": 30,
    "internet_stable_before_proxy_s": 300,
    "oil_check_runtime_hours": 24,
    "stale_seconds": 45,
    "stale_thresholds": STALE_THRESHOLDS_DEFAULTS,
    "visibility": VISIBILITY_DEFAULTS,
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


def _deep_merge_per_mode(defaults, user):
    """Merge user overrides into a {field: {mode: value}} dict (stale_thresholds)
    or {mode: {field: value}} dict (visibility). User's inner dict is shallow-
    merged on top of the default's inner dict for each outer key, so a user
    touching one field/mode doesn't wipe out defaults for the others."""
    merged = {**defaults}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
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
        if "stale_thresholds" in user:
            merged["stale_thresholds"] = _deep_merge_per_mode(
                STALE_THRESHOLDS_DEFAULTS, user["stale_thresholds"]
            )
        if "visibility" in user:
            merged["visibility"] = _deep_merge_per_mode(
                VISIBILITY_DEFAULTS, user["visibility"]
            )
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
