"""Unit tests for rdc_proxy.doctor.

The Probe class is stubbed so we never shell out, never touch /proc, and
never read /etc — every check runs against scripted command output. This
is what lets the logic cover post-install states we can't easily reach in
CI (broken ebtables, NM managing br0, missing TPROXY rule, etc.).
"""

from rdc_proxy import doctor
from rdc_proxy.doctor import CheckResult, FAIL, OK, SKIP, WARN


class StubProbe:
    def __init__(self, *, cmds=None, files=None, exists=None):
        self.cmds = cmds or {}    # tuple(cmd...) -> (rc, stdout, stderr)
        self.files = files or {}  # path -> contents (None means missing)
        self.exists_map = exists or {}
        self.calls = []

    def run(self, cmd, timeout=5.0):
        key = tuple(cmd)
        self.calls.append(("run", key))
        for k, v in self.cmds.items():
            if tuple(k) == key:
                return v
        return (0, "", "")

    def read(self, path):
        self.calls.append(("read", path))
        return self.files.get(path)

    def exists(self, path):
        self.calls.append(("exists", path))
        if path in self.exists_map:
            return self.exists_map[path]
        return path in self.files


def test_discover_config_with_env_file():
    probe = StubProbe(files={
        "/etc/rdc-proxy/rdc-proxy.env":
            'RDC_IP=10.0.0.50\nPROXY_PORT=5253\nOTHER="x"\n',
    })
    cfg = doctor.discover_config(probe, "/etc/rdc-proxy")
    assert cfg["RDC_IP"] == "10.0.0.50"
    assert cfg["PROXY_PORT"] == 5253
    assert cfg["env_present"] is True


def test_discover_config_falls_back_to_defaults_when_missing():
    cfg = doctor.discover_config(StubProbe(), "/etc/rdc-proxy")
    assert cfg["RDC_IP"] == "10.0.0.50"
    assert cfg["PROXY_PORT"] == 5253
    assert cfg["env_present"] is False


def test_check_kernel_passes_on_new_kernel():
    probe = StubProbe(cmds={("uname", "-r"): (0, "6.6.20-rpi-v8\n", "")})
    r = doctor.check_kernel(probe)
    assert r.status == OK


def test_check_kernel_fails_on_old_kernel():
    probe = StubProbe(cmds={("uname", "-r"): (0, "5.10.0\n", "")})
    r = doctor.check_kernel(probe)
    assert r.status == FAIL


def test_check_service_active():
    probe = StubProbe(cmds={
        ("systemctl", "is-active", "rdc-proxy"): (0, "active\n", ""),
    })
    assert doctor.check_service_active(probe).status == OK


def test_check_service_inactive():
    probe = StubProbe(cmds={
        ("systemctl", "is-active", "rdc-proxy"): (3, "failed\n", ""),
    })
    r = doctor.check_service_active(probe)
    assert r.status == FAIL
    assert "failed" in r.summary


def test_check_listen_port_found():
    probe = StubProbe(cmds={
        ("ss", "-tlnp"): (
            0,
            'LISTEN 0 128  0.0.0.0:5253  0.0.0.0:* users:(("python3",pid=1,fd=6))\n',
            "",
        ),
    })
    assert doctor.check_listen_port(probe, 5253).status == OK


def test_check_listen_port_missing():
    probe = StubProbe(cmds={("ss", "-tlnp"): (0, "", "")})
    assert doctor.check_listen_port(probe, 5253).status == FAIL


def test_check_bridge_healthy():
    probe = StubProbe(cmds={
        ("ip", "-br", "link", "show", "br0"):
            (0, "br0  UP  bb:aa ...\n", ""),
        ("bridge", "link"): (0,
            "2: eth0@NONE: <BROADCAST,MULTICAST,UP,LOWER_UP> master br0 state forwarding\n"
            "3: eth1@NONE: <BROADCAST,MULTICAST,UP,LOWER_UP> master br0 state forwarding\n",
            ""),
    })
    r = doctor.check_bridge(probe)
    assert r.status == OK
    assert r.data["members"] == ["eth0", "eth1"]


def test_check_bridge_missing():
    probe = StubProbe(cmds={
        ("ip", "-br", "link", "show", "br0"): (1, "", "device not found"),
    })
    assert doctor.check_bridge(probe).status == FAIL


def test_check_bridge_nf_on():
    probe = StubProbe(files={"/proc/sys/net/bridge/bridge-nf-call-iptables": "1\n"})
    assert doctor.check_bridge_nf(probe).status == OK


def test_check_bridge_nf_off():
    probe = StubProbe(files={"/proc/sys/net/bridge/bridge-nf-call-iptables": "0\n"})
    assert doctor.check_bridge_nf(probe).status == FAIL


def test_check_ip_rule_present():
    probe = StubProbe(cmds={
        ("ip", "rule", "show"): (0,
            "0:  from all lookup local\n"
            "32765: from all fwmark 0x1 lookup 100\n"
            "32766: from all lookup main\n", ""),
    })
    assert doctor.check_ip_rule(probe).status == OK


def test_check_ip_rule_missing():
    probe = StubProbe(cmds={
        ("ip", "rule", "show"): (0, "0: from all lookup local\n", ""),
    })
    assert doctor.check_ip_rule(probe).status == FAIL


def test_check_iptables_mangle_present():
    probe = StubProbe(cmds={
        ("iptables", "-t", "mangle", "-S", "PREROUTING"): (0,
            "-P PREROUTING ACCEPT\n"
            "-A PREROUTING -i br0 -s 10.0.0.50 -p tcp -m tcp --dport 5253 "
            "-j TPROXY --on-port 5253 --on-ip 0.0.0.0 --tproxy-mark 0x1/0x1\n",
            ""),
    })
    assert doctor.check_iptables_mangle(probe, "10.0.0.50", 5253).status == OK


def test_check_iptables_mangle_missing():
    probe = StubProbe(cmds={
        ("iptables", "-t", "mangle", "-S", "PREROUTING"): (0, "-P PREROUTING ACCEPT\n", ""),
    })
    r = doctor.check_iptables_mangle(probe, "10.0.0.50", 5253)
    assert r.status == FAIL
    assert "10.0.0.50" in r.summary


def test_check_ebtables_broute_present():
    probe = StubProbe(
        exists={"/usr/sbin/ebtables-legacy": True},
        cmds={
            ("/usr/sbin/ebtables-legacy", "-t", "broute", "-L", "BROUTING"): (0,
                "Bridge table: broute\n"
                "Bridge chain: BROUTING, entries: 1, policy: ACCEPT\n"
                "-p IPv4 --ip-proto tcp --ip-src 10.0.0.50 --ip-dport 5253 "
                "-j redirect --redirect-target ACCEPT\n", ""),
        },
    )
    assert doctor.check_ebtables_broute(probe, "10.0.0.50", 5253).status == OK


def test_check_ebtables_broute_missing_binary():
    probe = StubProbe(exists={"/usr/sbin/ebtables-legacy": False})
    r = doctor.check_ebtables_broute(probe, "10.0.0.50", 5253)
    assert r.status == FAIL
    assert "legacy" in r.summary.lower()


def test_check_proxy_traffic_sees_web_only_flags_fail():
    """Paul's scenario: web polls but no [proxy]/[internet] lines."""
    journal = "\n".join(
        "Apr 24 15:00:00 pi rdc-proxy[123]: [web] GET /api/status -> 200"
        for _ in range(20)
    )
    probe = StubProbe(cmds={
        ("journalctl", "-u", "rdc-proxy", "--no-pager", "-n", "300"): (0, journal, ""),
    })
    r = doctor.check_proxy_traffic(probe)
    assert r.status == FAIL
    assert "intercept" in r.summary


def test_check_proxy_traffic_healthy():
    journal = (
        "Apr 24 15:00:00 pi rdc-proxy[123]: [proxy] RDC connected from 10.0.0.50\n"
        "Apr 24 15:00:05 pi rdc-proxy[123]: [internet] cloud reachable\n"
    )
    probe = StubProbe(cmds={
        ("journalctl", "-u", "rdc-proxy", "--no-pager", "-n", "300"): (0, journal, ""),
    })
    assert doctor.check_proxy_traffic(probe).status == OK


def test_check_nm_unmanaged_skipped_when_nm_inactive():
    probe = StubProbe(cmds={
        ("systemctl", "is-active", "NetworkManager"): (3, "inactive\n", ""),
    })
    assert doctor.check_nm_unmanaged(probe).status == SKIP


def test_check_nm_unmanaged_warns_when_nm_active_but_conf_missing():
    probe = StubProbe(cmds={
        ("systemctl", "is-active", "NetworkManager"): (0, "active\n", ""),
    })
    assert doctor.check_nm_unmanaged(probe).status == WARN


def test_report_exits_with_fail_when_any_check_fails():
    probe = StubProbe(files={"/proc/sys/net/bridge/bridge-nf-call-iptables": "0\n"})
    r = CheckResult("x", FAIL, "broken")
    rep = doctor.Report()
    rep.add(CheckResult("y", OK, "fine"))
    rep.add(r)
    assert not rep.ok
    assert rep.counts()[FAIL] == 1


def test_format_json_roundtrips():
    rep = doctor.Report()
    rep.config = {"RDC_IP": "10.0.0.50", "PROXY_PORT": 5253}
    rep.add(CheckResult("kernel", OK, "6.6.20"))
    rep.add(CheckResult("iptables", FAIL, "rule missing", fix="systemctl restart"))
    import json as _json
    obj = _json.loads(doctor.format_json(rep))
    assert obj["ok"] is False
    assert obj["counts"][FAIL] == 1
    assert obj["checks"][1]["fix"].startswith("systemctl")
