# Architecture

```
                      ┌──── Home LAN (192.168.1.0/24) ────┐
                      │                                   │
                      │        Router / Internet           │
                      │                                   │
                      └─────────────── eth0 ──────────────┘
                                       │
                             ┌─────────────────┐
                             │   Pi (pibox)    │
                             │  ┌───────────┐  │
                             │  │    br0    │  │  ← single L2 broadcast domain
                             │  └───────────┘  │
                             └─────── eth1 ────┘
                                       │
                              ┌────────────────┐
                              │   Kohler RDC   │
                              │  192.168.4.50  │
                              └────────────────┘
```

The Pi sits as a transparent L2 bridge between the LAN and the RDC. Normally
frames pass through untouched; `rdc-proxy` uses `ebtables-legacy` BROUTING + an
`iptables` TPROXY rule to selectively divert the RDC's TCP 5253 traffic into a
local listening socket. Everything else (ARP, DHCP, other devices) passes
through unaffected.

## Module layout

```
rdc_proxy/
├── __main__.py     # Entry point. Wires everything up and runs the asyncio loop.
├── config.py       # DEFAULTS + load_config. Mutates CFG in place (don't rebind).
├── wire.py         # TLV decoder. Pure / stateless. Import target for the
│                   # kohler_corr correlation tool and any future consumers.
├── state.py        # GeneratorState singleton (STATE), HANDSHAKE persistence.
│                   # Thread-safe. Central data model.
├── proxy.py        # Asyncio TCP server, PROXY/LOCAL/WAITING mode logic,
│                   # internet_monitor(), read_exactly + forward_and_tap.
├── dashboard.py    # /sys/class/net/ iface counters + /proc/loadavg + thermal.
│                   # Reads STATE.get_side_channels() to surface plugin data.
├── web.py          # Flask app + /api/status + /api/reset-oil-check routes.
└── plugins.py      # importlib.metadata-based plugin loader for external
                    # packages registering under the "rdc_proxy.plugins" group.
```

**Import rule:** dependency graph goes one way.

```
config  →  wire  →  state  →  { proxy, dashboard, web }  →  __main__
                                                    ↑
                                             plugins (side channels)
```

## Three operating modes

| Mode | Trigger | Behavior |
|---|---|---|
| **PROXY** | internet stable ≥ 300 s AND RDC reconnects | Opens its own TCP to real cloud. Forwards bidirectionally. Taps RDC→cloud stream to decode telemetry into `STATE`. Captures handshake on first run. |
| **LOCAL** | no stable internet OR cloud unreachable AND `have_handshake()` | Replays the captured cloud greeting + config. Reads the RDC's response (ignored). Ingests telemetry into `STATE`. |
| **WAITING** | no internet AND no handshake | Accepts the RDC connection, sends nothing. Polls for internet; when it comes up, promotes to PROXY to capture handshake. |

The RDC has been observed to hold the TCP connection open for 60 s+ during
WAITING without disconnecting, so a brief "no internet + no handshake" window
at first boot is survivable.

## Plugin contract

A plugin is a Python package that declares an entry point under the
`rdc_proxy.plugins` group. Example `pyproject.toml`:

```toml
[project.entry-points."rdc_proxy.plugins"]
my-thing = "my_package:Plugin"
```

The referenced class must have a no-arg constructor and a `start(state)`
method. `state` is the `GeneratorState` singleton. The plugin typically:

1. Spawns its own daemon thread for whatever data source it polls.
2. Calls `state.update_side_channel("<name>", {...})` with fresh data.

`dashboard.collect_traffic()` merges all side channels into the dashboard
snapshot. A plugin may not be present — the core works fine without any.

## Handshake

Captured from one real cloud session and persisted to
`<config_dir>/handshake.json`. Byte-stable across Azure server rotations — the
576-byte cloud greeting, 576-byte RDC response, and 36-byte config message are
fixed per-device. See the spoof-server test results in the project memory for
empirical confirmation.

## TPROXY on a bridge — gotchas

- Must `modprobe br_netfilter` and `echo 1 > /proc/sys/net/bridge/bridge-nf-call-iptables`.
- `iptables -t mangle PREROUTING` alone doesn't divert bridged frames — the L2
  bridge forwards first. Fix: `ebtables-legacy -t broute` redirect to pull the
  frame into L3.
- Debian's default `ebtables` is the `nft` shim which **cannot** do broute
  redirects. Must use `/usr/sbin/ebtables-legacy`.
- Listening socket must set `IP_TRANSPARENT` (SOL_IP value 19) to accept
  packets with an unrelated original-destination address.
- Needs a route rule: `ip rule add fwmark 1 lookup 100` + `ip route add local 0.0.0.0/0 dev lo table 100`.

All of this is automated by the systemd unit's `ExecStartPre=` lines.
