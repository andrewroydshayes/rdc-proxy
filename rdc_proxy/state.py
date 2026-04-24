"""Mutable global state for rdc-proxy.

Holds the in-memory GeneratorState plus the handshake bytes captured from a
real cloud session. Centralizing these makes the rest of the codebase a one-way
dependency graph (config -> wire -> state -> {proxy, dashboard}).
"""

import json
import os
import threading
import time
from collections import deque
from datetime import datetime

from rdc_proxy.config import CFG
from rdc_proxy.wire import parse_tlv_records


class GeneratorState:
    """Thread-safe container for decoded telemetry, proxy state, and side channels."""

    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}
        # Per-field "last refreshed" timestamps. Used by snapshot() to prune
        # stale fields before handing them to the dashboard, so one silent
        # field (e.g. oil temp) shows "No Data" while the rest keep updating.
        self.value_timestamps = {}
        self.last_update = 0.0
        self.proxy_mode = "startup"
        self.cloud_ip = None
        self.cloud_connected = False
        self.rdc_connected = False
        self.internet_up = False
        self.internet_stable_since = None
        # Latest result of a cloud-reachability probe (TCP connect to cloud:5253).
        # Distinct from cloud_connected — "reachable" means probe succeeded,
        # "connected" means we have a live relay session right now.
        self.cloud_reachable_ip = None
        self.cloud_last_checked_ts = 0.0
        self.gen_started_at = None
        self.oil_check_runtime_start = 0.0
        self.oil_check_warned = False
        self._events = deque(maxlen=50)
        self._side_channels = {}

    def set_cloud_check_result(self, ip_or_none):
        with self.lock:
            self.cloud_reachable_ip = ip_or_none
            self.cloud_last_checked_ts = time.time()

    def update(self, name, value):
        with self.lock:
            old = self.values.get(name)
            prev_engine_mode = self._display_mode()
            now = time.time()
            self.values[name] = value
            self.value_timestamps[name] = now
            self.last_update = now

            if name == "utilityVoltageV" and value < 10 and old and old > 100:
                self.gen_started_at = datetime.now()
                self._events.appendleft({
                    "ts": datetime.now().strftime("%H:%M:%S"),
                    "msg": "Utility power lost — generator engaging",
                })

            if name == "utilityVoltageV" and value > 100 and old is not None and old < 10:
                if self.gen_started_at:
                    dur = datetime.now() - self.gen_started_at
                    self._events.appendleft({
                        "ts": datetime.now().strftime("%H:%M:%S"),
                        "msg": f"Utility restored (generator ran {dur})",
                    })
                self.gen_started_at = None

            new_engine_mode = self._display_mode()
            if new_engine_mode != prev_engine_mode and "exercise" in (prev_engine_mode, new_engine_mode):
                self._events.appendleft({
                    "ts": datetime.now().strftime("%H:%M:%S"),
                    "msg": f"Engine state: {prev_engine_mode} → {new_engine_mode}",
                })

    def ingest_buffer(self, buf):
        for rid, name, value, units in parse_tlv_records(buf):
            self.update(name, value)

    def update_side_channel(self, name, data):
        with self.lock:
            self._side_channels[name] = dict(data)

    def get_side_channels(self):
        with self.lock:
            return {k: dict(v) for k, v in self._side_channels.items()}

    def _display_mode(self):
        rpm = self.values.get("engineSpeedRpm", 0) or 0
        util_v = self.values.get("utilityVoltageV", 0) or 0
        if rpm > 100 and util_v < 10:
            return "running"
        if rpm > 100:
            return "exercise"
        return "standby"

    def get_display_mode(self):
        with self.lock:
            return self._display_mode()

    def snapshot(self):
        with self.lock:
            mode = self._display_mode()
            now = time.time()

            # Per-field staleness pruning, mode-aware. Three-valued logic per
            # (field, mode):
            #   1. missing entry        → field is not emitted in this mode
            #                             (engine-gated); always pruned
            #   2. threshold == -1      → sticky; once we've seen a value,
            #                             never prune it in this mode
            #   3. threshold >  0       → normal: prune if age exceeds it
            # Unknown fields (no stale_thresholds entry at all) fall back to
            # the global stale_seconds integer — same as before.
            fallback = CFG.get("stale_seconds", 45)
            thresholds = CFG.get("stale_thresholds", {})

            def _threshold(field):
                per_mode = thresholds.get(field)
                if per_mode is None:
                    return fallback
                # Absence for THIS mode → always stale (engine-gated).
                return per_mode.get(mode, 0)

            def _is_fresh(field, ts):
                t = _threshold(field)
                if t == -1:
                    return True   # sticky — never stale
                if t <= 0:
                    return False  # engine-gated or invalid
                return (now - ts) <= t

            fresh_vals = {
                k: v for k, v in self.values.items()
                if (ts := self.value_timestamps.get(k)) is not None
                and _is_fresh(k, ts)
            }

            # Derived Fahrenheit temps — only emit when the Celsius source
            # is still fresh. Omitting them triggers the dashboard's
            # per-field no-data overlay.
            oil_c = fresh_vals.get("lubeOilTempC")
            ctrl_c = fresh_vals.get("controllerTempC")
            if oil_c is not None:
                fresh_vals["oilTempF"] = round(oil_c * 9 / 5 + 32, 1)
            if ctrl_c is not None:
                fresh_vals["controllerTempF"] = round(ctrl_c * 9 / 5 + 32, 1)

            vals = fresh_vals

            gen_duration = None
            if self.gen_started_at:
                gen_duration = (datetime.now() - self.gen_started_at).total_seconds()

            # Oil-check logic reads from the *last-seen* runtime (not the
            # pruned snapshot) so a stale hours reading doesn't accidentally
            # reset the oil-check warning state.
            runtime = self.values.get("totalRuntimeHours", 0) or 0
            oil_runtime_since_check = runtime - self.oil_check_runtime_start
            oil_warn = oil_runtime_since_check >= CFG.get("oil_check_runtime_hours", 24)

            stable_threshold_s = CFG.get("internet_stable_before_proxy_s", 300)
            seconds_to_stable = None
            if self.internet_stable_since is not None:
                elapsed = time.time() - self.internet_stable_since
                seconds_to_stable = max(0, int(stable_threshold_s - elapsed))

            # Per-mode visibility flags for the CURRENT mode, so the client
            # doesn't have to know all three mode sub-dicts. Fields missing
            # from the dict default to True (shown).
            visibility = CFG.get("visibility", {}).get(mode, {})

            return {
                "mode": mode,
                "values": vals,
                "visibility": visibility,
                "proxy_mode": self.proxy_mode,
                "cloud_ip": self.cloud_ip,
                "cloud_connected": self.cloud_connected,
                "cloud_reachable_ip": self.cloud_reachable_ip,
                "cloud_last_checked_ts": self.cloud_last_checked_ts,
                "rdc_connected": self.rdc_connected,
                "internet_up": self.internet_up,
                "internet_stable_since": self.internet_stable_since,
                "stable_threshold_s": stable_threshold_s,
                "seconds_to_stable": seconds_to_stable,
                "gen_started_at": self.gen_started_at.isoformat() if self.gen_started_at else None,
                "gen_duration_s": gen_duration,
                "oil_check_warn": oil_warn,
                "oil_runtime_since_check": round(oil_runtime_since_check, 2),
                "last_update": self.last_update,
                "events": list(self._events),
                "gauges": CFG.get("gauges", {}),
                "side_channels": {k: dict(v) for k, v in self._side_channels.items()},
            }

    def set_proxy_mode(self, new_mode):
        """Update proxy mode and log a user-facing event on transition."""
        with self.lock:
            old = self.proxy_mode
            if old == new_mode:
                return
            self.proxy_mode = new_mode
            msg_map = {
                ("startup", "proxy"):  "Proxy service started — relaying to Kohler cloud",
                ("startup", "local"):  "Proxy service started — serving locally (no cloud)",
                ("startup", "waiting"): "Proxy service started — waiting for internet",
                ("proxy",   "local"):  "Switched to LOCAL mode (cloud unreachable)",
                ("local",   "proxy"):  "Switched to PROXY mode (cloud back online)",
                ("waiting", "proxy"):  "Captured handshake — now in PROXY mode",
                ("waiting", "local"):  "Switched to LOCAL mode from WAITING",
                ("proxy",   "waiting"): "Lost cloud connection — WAITING",
                ("local",   "waiting"): "Entered WAITING mode",
            }
            msg = msg_map.get((old, new_mode), f"Proxy mode: {old} → {new_mode}")
            self._events.appendleft({
                "ts": datetime.now().strftime("%H:%M:%S"),
                "msg": msg,
            })

    def reset_oil_check(self):
        with self.lock:
            runtime = self.values.get("totalRuntimeHours", 0) or 0
            self.oil_check_runtime_start = runtime
            self._events.appendleft({
                "ts": datetime.now().strftime("%H:%M:%S"),
                "msg": "Oil check counter reset",
            })


STATE = GeneratorState()


HANDSHAKE = {"cloud_greeting": None, "rdc_response": None, "config_msg": None}


def handshake_path():
    return os.path.join(CFG.get("config_dir", "/etc/rdc-proxy"), "handshake.json")


def load_handshake():
    p = handshake_path()
    if not os.path.exists(p):
        return False
    with open(p) as f:
        data = json.load(f)
    HANDSHAKE["cloud_greeting"] = bytes.fromhex(data["cloud_greeting"]) if data.get("cloud_greeting") else None
    HANDSHAKE["rdc_response"] = bytes.fromhex(data["rdc_response"]) if data.get("rdc_response") else None
    HANDSHAKE["config_msg"] = bytes.fromhex(data["config_msg"]) if data.get("config_msg") else None
    print(f"[handshake] loaded from {p}", flush=True)
    return True


def save_handshake():
    p = handshake_path()
    data = {
        "cloud_greeting": HANDSHAKE["cloud_greeting"].hex() if HANDSHAKE["cloud_greeting"] else None,
        "rdc_response": HANDSHAKE["rdc_response"].hex() if HANDSHAKE["rdc_response"] else None,
        "config_msg": HANDSHAKE["config_msg"].hex() if HANDSHAKE["config_msg"] else None,
        "captured_at": datetime.now().isoformat(),
    }
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[handshake] saved to {p}", flush=True)


def have_handshake():
    return all(HANDSHAKE[k] is not None for k in ("cloud_greeting", "config_msg"))
