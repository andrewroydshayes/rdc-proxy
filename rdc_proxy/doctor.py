"""Post-install diagnostics for rdc-proxy.

Runs the checks we keep asking forum users to run one by one, prints a
pass/fail/warn report, and — with --json — emits a machine-readable version
the support tooling can ingest directly.

Design notes
- Every check is a pure function taking a Probe (which encapsulates the side
  effects — running commands, reading files) and returning a CheckResult.
  That makes most checks unit-testable by stubbing Probe.
- Network-rule checks match on the *intent* (a TPROXY rule targeting the
  configured proxy port) rather than a literal line, so benign wording changes
  in iptables output don't break them.
- The checker NEVER mutates system state. Read-only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional


# ── Result model ─────────────────────────────────────────────────────────────

OK = "ok"
FAIL = "fail"
WARN = "warn"
SKIP = "skip"


@dataclass
class CheckResult:
    name: str
    status: str           # ok | fail | warn | skip
    summary: str
    detail: str = ""
    fix: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class Report:
    checks: list = field(default_factory=list)
    config: dict = field(default_factory=dict)

    def add(self, r: CheckResult) -> None:
        self.checks.append(r)

    def counts(self) -> dict:
        c = {OK: 0, FAIL: 0, WARN: 0, SKIP: 0}
        for r in self.checks:
            c[r.status] = c.get(r.status, 0) + 1
        return c

    @property
    def ok(self) -> bool:
        return self.counts().get(FAIL, 0) == 0


# ── Probe: the side-effect boundary ──────────────────────────────────────────

class Probe:
    """Thin wrapper over shell-outs and filesystem reads. Tests stub this."""

    def run(self, cmd: list, timeout: float = 5.0) -> tuple:
        """Return (returncode, stdout, stderr). Never raises on non-zero rc."""
        try:
            p = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=False,
            )
            return p.returncode, p.stdout, p.stderr
        except FileNotFoundError:
            return 127, "", f"not found: {cmd[0]}"
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout: {' '.join(cmd)}"

    def read(self, path: str) -> Optional[str]:
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            return None

    def exists(self, path: str) -> bool:
        return os.path.exists(path)


# ── Config discovery ─────────────────────────────────────────────────────────

def discover_config(probe: Probe, config_dir: str) -> dict:
    """Read RDC_IP / PROXY_PORT from the env file the installer writes, with
    sane defaults if the file is missing (doctor can still run pre-install)."""
    env_path = os.path.join(config_dir, "rdc-proxy.env")
    cfg = {"RDC_IP": "10.0.0.50", "PROXY_PORT": 5253,
           "config_dir": config_dir, "env_path": env_path, "env_present": False}
    raw = probe.read(env_path)
    if raw is None:
        return cfg
    cfg["env_present"] = True
    for line in raw.splitlines():
        m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.*?)\s*$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip('"').strip("'")
        if k == "PROXY_PORT":
            try:
                cfg[k] = int(v)
            except ValueError:
                cfg[k] = v
        else:
            cfg[k] = v
    return cfg


# ── Individual checks ────────────────────────────────────────────────────────

def check_kernel(probe: Probe) -> CheckResult:
    rc, out, _ = probe.run(["uname", "-r"])
    rel = out.strip()
    if rc != 0 or not rel:
        return CheckResult("kernel", FAIL, "couldn't read kernel release",
                           fix="run `uname -r` manually; this shouldn't fail")
    m = re.match(r"^(\d+)\.(\d+)", rel)
    if not m:
        return CheckResult("kernel", WARN, f"unrecognized kernel: {rel}")
    maj, mn = int(m.group(1)), int(m.group(2))
    if maj > 5 or (maj == 5 and mn >= 15):
        return CheckResult("kernel", OK, f"{rel} (>= 5.15)")
    return CheckResult(
        "kernel", FAIL, f"{rel} is too old (need >= 5.15 for TPROXY + bridge)",
        fix="upgrade to Raspberry Pi OS Bookworm or newer",
    )


def check_service_active(probe: Probe) -> CheckResult:
    rc, out, _ = probe.run(["systemctl", "is-active", "rdc-proxy"])
    state = out.strip()
    if state == "active":
        return CheckResult("service", OK, "rdc-proxy.service is active")
    return CheckResult(
        "service", FAIL, f"rdc-proxy.service is {state or 'unknown'}",
        fix="sudo systemctl status rdc-proxy; sudo journalctl -u rdc-proxy -n 100",
    )


def check_listen_port(probe: Probe, port: int) -> CheckResult:
    rc, out, _ = probe.run(["ss", "-tlnp"])
    hit = any(f":{port}" in line for line in out.splitlines())
    if hit:
        return CheckResult("listen_port", OK, f"proxy listening on :{port}")
    return CheckResult(
        "listen_port", FAIL, f"nothing listening on :{port}",
        fix="the proxy process isn't up; check `systemctl status rdc-proxy` "
            "and the journal for import/permission errors",
    )


def check_bridge(probe: Probe) -> CheckResult:
    rc, out, _ = probe.run(["ip", "-br", "link", "show", "br0"])
    if rc != 0:
        return CheckResult("bridge", FAIL, "br0 does not exist",
                           fix="sudo bash /opt/rdc-proxy/install/setup-bridge.sh "
                               "(or re-run the installer)")
    state = "UP" if " UP " in f" {out} " else "DOWN"
    # Members
    rc2, out2, _ = probe.run(["bridge", "link"])
    members = []
    for line in out2.splitlines():
        m = re.search(r"\d+:\s+([^@:\s]+)[@:].*master br0", line)
        if m:
            members.append(m.group(1))
    if not members:
        return CheckResult(
            "bridge", FAIL, f"br0 exists ({state}) but has no member interfaces",
            fix="setup-bridge.sh ran, but nothing enslaved to br0. Check "
                "SECOND_IFACE env and /etc/systemd/network/20-br0-members.network",
            data={"state": state, "members": []},
        )
    return CheckResult(
        "bridge", OK if state == "UP" else WARN,
        f"br0 {state}, members: {' + '.join(members)}",
        data={"state": state, "members": members},
    )


def check_bridge_has_ip(probe: Probe) -> CheckResult:
    rc, out, _ = probe.run(["ip", "-4", "-o", "addr", "show", "dev", "br0"])
    m = re.search(r"inet (\S+)", out)
    if m:
        return CheckResult("bridge_ip", OK, f"br0 has IP {m.group(1)}",
                           data={"ip": m.group(1)})
    return CheckResult(
        "bridge_ip", FAIL, "br0 has no IPv4 address",
        fix="DHCP on br0 hasn't succeeded. Check your router sees br0's MAC, "
            "or give it a static IP. `networkctl status br0` shows why.",
    )


def check_bridge_nf(probe: Probe) -> CheckResult:
    v = (probe.read("/proc/sys/net/bridge/bridge-nf-call-iptables") or "").strip()
    if v == "1":
        return CheckResult("bridge_nf_call_iptables", OK, "bridge-nf-call-iptables=1")
    return CheckResult(
        "bridge_nf_call_iptables", FAIL,
        f"bridge-nf-call-iptables={v or 'missing'} (need 1)",
        fix="sudo modprobe br_netfilter && "
            "echo 1 | sudo tee /proc/sys/net/bridge/bridge-nf-call-iptables",
    )


def check_ip_rule(probe: Probe) -> CheckResult:
    _, out, _ = probe.run(["ip", "rule", "show"])
    if re.search(r"fwmark\s+0x1\s+lookup\s+100", out):
        return CheckResult("ip_rule_fwmark", OK, "fwmark 0x1 -> table 100 present")
    return CheckResult(
        "ip_rule_fwmark", FAIL, "ip rule for fwmark 0x1 -> table 100 missing",
        fix="sudo systemctl restart rdc-proxy (the unit's ExecStartPre installs it)",
    )


def check_ip_route_local(probe: Probe) -> CheckResult:
    _, out, _ = probe.run(["ip", "route", "show", "table", "100"])
    if "local" in out and "dev lo" in out:
        return CheckResult("ip_route_tproxy", OK, "table 100 has local 0/0 dev lo")
    return CheckResult(
        "ip_route_tproxy", FAIL, "TPROXY route (table 100 local 0/0 dev lo) missing",
        fix="sudo systemctl restart rdc-proxy",
    )


def check_iptables_mangle(probe: Probe, rdc_ip: str, port: int) -> CheckResult:
    _, out, _ = probe.run(["iptables", "-t", "mangle", "-S", "PREROUTING"])
    hit = False
    for line in out.splitlines():
        if (f"--dport {port}" in line and "TPROXY" in line and
                (f"-s {rdc_ip}" in line or f"-s {rdc_ip}/" in line)):
            hit = True
            break
    if hit:
        return CheckResult("iptables_mangle_tproxy", OK,
                           f"TPROXY rule present ({rdc_ip}:{port})")
    return CheckResult(
        "iptables_mangle_tproxy", FAIL,
        f"TPROXY rule for {rdc_ip}:{port} missing in mangle PREROUTING",
        fix="sudo systemctl restart rdc-proxy (rule is installed by ExecStartPre)",
    )


def check_ebtables_broute(probe: Probe, rdc_ip: str, port: int) -> CheckResult:
    legacy = "/usr/sbin/ebtables-legacy"
    if not probe.exists(legacy):
        return CheckResult(
            "ebtables_broute", FAIL, "ebtables-legacy binary missing",
            fix="sudo apt-get install -y ebtables  (Debian's default nft shim "
                "cannot do broute redirect — legacy is required)",
        )
    _, out, _ = probe.run([legacy, "-t", "broute", "-L", "BROUTING"])
    if f"ip-src {rdc_ip}" in out and f"ip-dport {port}" in out:
        return CheckResult("ebtables_broute", OK,
                           f"broute redirect present ({rdc_ip} -> :{port})")
    return CheckResult(
        "ebtables_broute", FAIL,
        f"broute redirect for {rdc_ip}:{port} missing",
        fix="sudo systemctl restart rdc-proxy",
    )


def check_dashboard(probe: Probe) -> CheckResult:
    # Dashboard port comes from config.json but 80 is the default; try both.
    for port in (80, 8080):
        rc, out, _ = probe.run(
            ["curl", "-fsS", "-m", "3", "-o", "/dev/null",
             "-w", "%{http_code}", f"http://127.0.0.1:{port}/api/status"],
            timeout=6,
        )
        if rc == 0 and out.strip().startswith("2"):
            return CheckResult("dashboard", OK,
                               f"/api/status OK on :{port}", data={"port": port})
    return CheckResult(
        "dashboard", WARN, "dashboard /api/status not reachable on :80 or :8080",
        fix="only a warn — the dashboard port is user-configurable. "
            "Check `web_port` in /etc/rdc-proxy/config.json",
    )


def check_rdc_reachable(probe: Probe, rdc_ip: str) -> CheckResult:
    # ping is a noisy positive (the RDC may not respond to ICMP), so use arp.
    _, out, _ = probe.run(["ip", "neigh", "show", "to", rdc_ip])
    if "REACHABLE" in out or "STALE" in out or "DELAY" in out or "PERMANENT" in out:
        return CheckResult("rdc_reachable", OK, f"arp entry for {rdc_ip} present")
    return CheckResult(
        "rdc_reachable", WARN,
        f"no arp entry for {rdc_ip} — the generator hasn't talked to us yet",
        fix="plug the generator into the Pi's second Ethernet, power it on, "
            "wait 30s, then re-run doctor",
    )


def check_proxy_traffic(probe: Probe) -> CheckResult:
    rc, out, _ = probe.run(
        ["journalctl", "-u", "rdc-proxy", "--no-pager", "-n", "300"],
        timeout=8,
    )
    if rc != 0:
        return CheckResult("proxy_traffic", SKIP,
                           "journalctl unavailable (not root? not systemd?)")
    proxy_lines = [l for l in out.splitlines() if "[proxy]" in l or "[internet]" in l]
    web_lines   = [l for l in out.splitlines() if "/api/status" in l]
    if proxy_lines:
        return CheckResult("proxy_traffic", OK,
                           f"{len(proxy_lines)} proxy/internet lines in journal")
    if web_lines and not proxy_lines:
        return CheckResult(
            "proxy_traffic", FAIL,
            "journal shows web polls but no [proxy]/[internet] lines — "
            "the proxy isn't intercepting generator traffic",
            fix="run `rdc-proxy doctor` and look at iptables_mangle_tproxy / "
                "ebtables_broute / rdc_reachable — one of those is likely failing",
        )
    return CheckResult("proxy_traffic", WARN,
                       "no recent proxy or web lines in the journal")


def check_nm_unmanaged(probe: Probe) -> CheckResult:
    rc, out, _ = probe.run(["systemctl", "is-active", "NetworkManager"])
    if out.strip() != "active":
        return CheckResult("nm_unmanaged", SKIP, "NetworkManager not active")
    conf = probe.read("/etc/NetworkManager/conf.d/unmanaged-bridge.conf")
    if conf and "unmanaged-devices" in conf and "br0" in conf:
        return CheckResult("nm_unmanaged", OK,
                           "NetworkManager told to ignore br0 and members")
    return CheckResult(
        "nm_unmanaged", WARN,
        "NetworkManager is running but unmanaged-bridge.conf is missing or stale",
        fix="re-run setup-bridge.sh, or restart NetworkManager after editing the conf",
    )


# ── Driver ───────────────────────────────────────────────────────────────────

CHECKS: list = [
    ("host", [
        ("kernel",                  lambda p, c: check_kernel(p)),
        ("service",                 lambda p, c: check_service_active(p)),
    ]),
    ("network plumbing", [
        ("bridge",                  lambda p, c: check_bridge(p)),
        ("bridge_ip",               lambda p, c: check_bridge_has_ip(p)),
        ("bridge_nf_call_iptables", lambda p, c: check_bridge_nf(p)),
        ("nm_unmanaged",            lambda p, c: check_nm_unmanaged(p)),
    ]),
    ("TPROXY plumbing", [
        ("ip_rule_fwmark",          lambda p, c: check_ip_rule(p)),
        ("ip_route_tproxy",         lambda p, c: check_ip_route_local(p)),
        ("iptables_mangle_tproxy",  lambda p, c: check_iptables_mangle(p, c["RDC_IP"], c["PROXY_PORT"])),
        ("ebtables_broute",         lambda p, c: check_ebtables_broute(p, c["RDC_IP"], c["PROXY_PORT"])),
        ("listen_port",             lambda p, c: check_listen_port(p, c["PROXY_PORT"])),
    ]),
    ("runtime", [
        ("rdc_reachable",           lambda p, c: check_rdc_reachable(p, c["RDC_IP"])),
        ("proxy_traffic",           lambda p, c: check_proxy_traffic(p)),
        ("dashboard",               lambda p, c: check_dashboard(p)),
    ]),
]


def run_all(probe: Probe, config_dir: str) -> Report:
    report = Report()
    report.config = discover_config(probe, config_dir)
    for _group, group_checks in CHECKS:
        for _slug, fn in group_checks:
            try:
                report.add(fn(probe, report.config))
            except Exception as e:
                report.add(CheckResult(
                    name=_slug, status=FAIL,
                    summary=f"check crashed: {type(e).__name__}: {e}",
                    fix="this is a doctor bug — please report with the journal of the crash",
                ))
    return report


# ── Output ───────────────────────────────────────────────────────────────────

_ICON = {OK: "✓", FAIL: "✗", WARN: "!", SKIP: "·"}


def _color(stream) -> dict:
    if not hasattr(stream, "isatty") or not stream.isatty():
        return {k: "" for k in ("g", "r", "y", "m", "n")}
    return {"g": "\033[32m", "r": "\033[31m", "y": "\033[33m",
            "m": "\033[90m", "n": "\033[0m"}


def format_text(report: Report, stream=sys.stdout) -> str:
    c = _color(stream)
    lines: list = []
    color_for = {OK: c["g"], FAIL: c["r"], WARN: c["y"], SKIP: c["m"]}
    cfg = report.config
    lines.append(f"rdc-proxy doctor · RDC={cfg.get('RDC_IP')} "
                 f"PROXY_PORT={cfg.get('PROXY_PORT')} "
                 f"config_dir={cfg.get('config_dir')}")
    by_name = {r.name: r for r in report.checks}
    for group, group_checks in CHECKS:
        lines.append("")
        lines.append(f"── {group} ──")
        for slug, _ in group_checks:
            r = by_name.get(slug)
            if not r:
                continue
            icon = _ICON.get(r.status, "?")
            col = color_for.get(r.status, "")
            lines.append(f"  {col}{icon}{c['n']} {r.summary}")
            if r.fix and r.status in (FAIL, WARN):
                lines.append(f"    {c['m']}fix:{c['n']} {r.fix}")
    counts = report.counts()
    lines.append("")
    lines.append(
        f"{counts.get(OK, 0)} ok · "
        f"{c['y']}{counts.get(WARN, 0)} warn{c['n']} · "
        f"{c['r']}{counts.get(FAIL, 0)} fail{c['n']} · "
        f"{counts.get(SKIP, 0)} skip"
    )
    return "\n".join(lines)


def format_json(report: Report) -> str:
    return json.dumps({
        "config": report.config,
        "counts": report.counts(),
        "ok": report.ok,
        "checks": [asdict(r) for r in report.checks],
    }, indent=2)


# ── CLI entry ────────────────────────────────────────────────────────────────

def main(argv: Optional[list] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in argv
    if as_json:
        argv.remove("--json")
    config_dir = os.environ.get("RDC_PROXY_CONFIG_DIR", "/etc/rdc-proxy")
    report = run_all(Probe(), config_dir)
    if as_json:
        print(format_json(report))
    else:
        print(format_text(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
