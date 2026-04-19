# Raspberry Pi setup — from factory to running rdc-proxy

This guide assumes a brand-new Raspberry Pi. End state: a Pi acting as a
transparent L2 bridge between your home LAN and the Kohler RDC's ethernet port,
capturing + decoding telemetry and serving a local dashboard.

## 0. Hardware you need

- Raspberry Pi 4 or 5 (tested on Pi 4B, 4 GB)
- microSD card, 16 GB or larger (Class 10 / A2 recommended)
- Two ethernet interfaces on the Pi:
  - Built-in `eth0`
  - USB-to-ethernet adapter for `eth1`  (any RTL8153 / ASIX AX88179 works)
- Two Cat5e/Cat6 cables
- USB-C power supply (5 V / 3 A minimum on Pi 4)

## 1. Flash the OS image

Use the official **Raspberry Pi Imager** (`rpi-imager`). Choose:

- **OS:** Raspberry Pi OS Lite (64-bit). Headless — no desktop needed.
- **Storage:** your microSD card.

Before clicking "Write", open the **customization / settings** (gear icon) and
set these:

| Field | Value |
|---|---|
| Hostname | `pibox` (or whatever you like — referenced below as `<pi>`) |
| Username | your preferred user, e.g. `andrew` |
| Password | strong, or public-key only |
| Enable SSH | ✅ yes. Upload your SSH public key |
| Configure Wi-Fi | ✅ optional, useful as a failover admin path if the bridge ever breaks your wired access |
| Locale / Timezone | set to your region |

Write, eject, done.

## 2. First boot and login

1. Insert the card into the Pi.
2. Plug the **built-in eth0** into your home LAN.
3. Plug in power.
4. Wait 60 seconds for first-boot.
5. From another machine on the LAN, find the Pi's IP:
   - Easiest: check your router's DHCP client list for the hostname you set.
   - Or: `arp -a | grep -i pi` / `nmap -sn 192.168.1.0/24`.
6. SSH in: `ssh <user>@<pi-ip>`  (e.g. `ssh andrew@192.168.1.42`).

Confirm you're in:

```
uname -a    # should say Linux pibox ... aarch64 GNU/Linux
```

## 3. Run the installer

Once you're SSH'd into the Pi, run:

```bash
curl -fsSL https://raw.githubusercontent.com/andrewroydshayes/rdc-proxy/main/install/install.sh \
  | sudo RDC_IP=192.168.4.50 bash
```

Arguments you may want to override:
- `RDC_IP=...` — the RDC's static IP on your LAN (usually `192.168.4.50`).
- `PROXY_PORT=...` — defaults to 5253.
- `BRANCH=main` — pin to an explicit branch/tag. Default = latest tag.
- `REPO_URL=...` — if you forked the repo, point at your fork.

The installer is idempotent. It will:

1. Check prerequisites (arch, kernel, disk, internet).
2. Install required apt packages.
3. Clone or update `rdc-proxy` to `/opt/rdc-proxy`.
4. Create a Python venv at `/opt/rdc-proxy/venv` and install the package.
5. Set up `/etc/rdc-proxy/rdc-proxy.env`.
6. Configure a transparent bridge (`br0` over eth0+eth1) via systemd-networkd.
7. Install + start `rdc-proxy.service`.
8. Self-check (service active, TPROXY rule, broute rule, dashboard reachable).

You'll get a green/red summary at the end. **If anything fails, re-run the
same command** — the installer will pick up where it left off.

## 4. Wire it up

Once the installer reports success and `br0` is up:

1. Unplug eth0 from the LAN (the Pi will briefly lose its SSH session — that's
   fine, reconnect via Wi-Fi or reboot).
2. Plug **eth0 into your home LAN/router**.
3. Plug **eth1 into the Kohler RDC's ethernet port**.
4. The bridge passes frames transparently between eth0 and eth1. The RDC keeps
   talking to Kohler cloud as if nothing changed, and rdc-proxy silently
   intercepts.

## 5. Verify

From any device on your LAN:

```
http://<pi>/               # web dashboard
```

You should see the generator status, live telemetry, and the proxy's mode
(PROXY / LOCAL / WAITING). In PROXY mode it's transparently relaying to
Kohler's cloud; in LOCAL mode it's replaying the captured handshake when the
internet is down.

`journalctl -u rdc-proxy -f` on the Pi shows live logs.

## 6. Optional: install the UniFi dashboard plugin

If you use a UniFi switch and want port counters in the dashboard, install the
plugin **after** rdc-proxy is up. See the separate repo for instructions.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `cannot reach github.com` | Pi has no internet | Check eth0 is plugged into LAN, DHCP lease OK |
| `kernel too old` | Pre-5.15 kernel | `sudo apt-get update && sudo apt-get dist-upgrade` then reboot |
| `/usr/sbin/ebtables-legacy missing` | apt installed `ebtables` but not the legacy binary | `sudo apt-get install --reinstall ebtables` |
| Dashboard loads but mode stuck on `waiting` | No internet + no captured handshake | Plug in LAN, wait for first successful RDC session so the proxy can capture a handshake from real cloud |
| SSH drops when plugging into br0 | You plugged LAN into eth1 instead of eth0 | Swap the cables — eth0 = LAN, eth1 = RDC |
