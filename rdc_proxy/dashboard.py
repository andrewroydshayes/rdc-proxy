"""Dashboard data collectors: interface counters, Pi health, telemetry snapshot.

Plugin-provided side channels (e.g. UniFi switch counters) are merged from
STATE.get_side_channels() — this module knows nothing about which plugins
are installed.
"""

import shutil
import threading
import time
from collections import deque
from datetime import datetime

from rdc_proxy.state import STATE
from rdc_proxy.wire import PARAM_MAP


STAT_FIELDS = [
    "rx_packets", "tx_packets", "rx_bytes", "tx_bytes",
    "rx_errors", "tx_errors", "rx_dropped", "tx_dropped", "rx_crc_errors",
]


def read_iface(iface):
    base = f"/sys/class/net/{iface}/statistics/"
    out = {}
    for f in STAT_FIELDS:
        try:
            with open(base + f) as fh:
                out[f] = int(fh.read())
        except Exception:
            out[f] = 0
    return out


def delta_rate(curr, prev, field, dt):
    return max(0.0, (curr[field] - prev[field]) / dt)


def iface_sample(curr, prev, dt):
    dpkts = (curr["rx_packets"] + curr["tx_packets"]) - (prev["rx_packets"] + prev["tx_packets"])
    dbytes = (curr["rx_bytes"] + curr["tx_bytes"]) - (prev["rx_bytes"] + prev["tx_bytes"])
    return {
        "rx_pps": round(delta_rate(curr, prev, "rx_packets", dt), 2),
        "tx_pps": round(delta_rate(curr, prev, "tx_packets", dt), 2),
        "rx_bps": round(delta_rate(curr, prev, "rx_bytes", dt), 2),
        "tx_bps": round(delta_rate(curr, prev, "tx_bytes", dt), 2),
        "rx_errors": curr["rx_errors"],
        "tx_errors": curr["tx_errors"],
        "rx_dropped": curr["rx_dropped"],
        "crc_errors": curr.get("rx_crc_errors", 0),
        "avg_pkt_size": round(dbytes / dpkts, 1) if dpkts > 0 else 0,
    }


def pi_health():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp_c = round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        temp_c = None
    try:
        with open("/proc/loadavg") as f:
            p = f.read().split()
            load1, load5, load15 = float(p[0]), float(p[1]), float(p[2])
    except Exception:
        load1 = load5 = load15 = None
    try:
        du = shutil.disk_usage("/")
        disk_free_gb = round(du.free / (1024**3), 1)
        disk_total_gb = round(du.total / (1024**3), 1)
    except Exception:
        disk_free_gb = disk_total_gb = None
    return {
        "cpu_temp_c": temp_c, "load1": load1, "load5": load5, "load15": load15,
        "disk_free_gb": disk_free_gb, "disk_total_gb": disk_total_gb,
    }


_dash_history = deque(maxlen=300)
_dash_current = {}
_dash_lock = threading.Lock()


def collect_traffic():
    prev0 = prev1 = prev_t = None
    while True:
        now = time.time()
        e0 = read_iface("eth0")
        e1 = read_iface("eth1")
        if prev_t is not None:
            dt = max(now - prev_t, 0.001)
            s0 = iface_sample(e0, prev0, dt)
            s1 = iface_sample(e1, prev1, dt)
            point = {
                "t": int(now * 1000),
                "ts": datetime.now().strftime("%H:%M:%S"),
                "eth0": s0, "eth1": s1,
                "pi": pi_health(),
                "side_channels": STATE.get_side_channels(),
            }
            # back-compat key for the existing UI: surface unifi side-channel as "switch"
            if "switch" in point["side_channels"]:
                point["switch"] = point["side_channels"]["switch"]
            with _dash_lock:
                _dash_history.append(point)
                _dash_current.update(point)
        prev0, prev1, prev_t = e0, e1, now
        time.sleep(1)


def get_dashboard_state():
    with _dash_lock:
        return dict(_dash_current), list(_dash_history)[-120:]


def snapshot_decoded():
    with STATE.lock:
        out = []
        for rid, (name, transform, units) in PARAM_MAP.items():
            val = STATE.values.get(name)
            if val is None:
                continue
            out.append({
                "id": rid, "id_hex": f"0x{rid:04x}",
                "name": name, "value": val, "units": units,
            })
        out.sort(key=lambda x: x["id"])
        return out
