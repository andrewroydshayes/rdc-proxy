#!/usr/bin/env bash
# Uninstaller for rdc-proxy. Reverses what install/install.sh sets up.
#
#   curl -fsSL https://raw.githubusercontent.com/andrewroydshayes/rdc-proxy/main/install/uninstall.sh \
#     | sudo bash
#
# Or from a local checkout:
#   sudo bash /opt/rdc-proxy/install/uninstall.sh
#
# Flags (env vars):
#   KEEP_CONFIG=1   leave /etc/rdc-proxy in place (config + handshake)
#   PURGE_APT=1     also `apt-get remove` the packages install.sh added
#   YES=1           skip the confirmation prompt
#
# Safe to run twice. Each step checks before acting.

set -uo pipefail

INSTALL_DIR=${INSTALL_DIR:-/opt/rdc-proxy}
CONFIG_DIR=${CONFIG_DIR:-/etc/rdc-proxy}
SERVICE_NAME=rdc-proxy
BRIDGE_NAME=${BRIDGE_NAME:-br0}

if [[ -t 1 ]]; then
  G=$(printf '\033[32m'); R=$(printf '\033[31m'); Y=$(printf '\033[33m'); N=$(printf '\033[0m')
else
  G=""; R=""; Y=""; N=""
fi

ok()   { echo "${G}✓${N} $1"; }
skip() { echo "  $1 — skipped"; }
warn() { echo "${Y}!${N} $1"; }
step() { echo; echo "${G}── $1 ──${N}"; }

if [[ $EUID -ne 0 ]]; then
  echo "${R}must be run as root (sudo)${N}" >&2
  exit 1
fi

# Pull the configured port back out of the env file so the rule-deletion
# commands target the right port even if the user customized PROXY_PORT.
PROXY_PORT=5253
RDC_IP=10.0.0.50
if [[ -f "$CONFIG_DIR/rdc-proxy.env" ]]; then
  # shellcheck disable=SC1091
  source "$CONFIG_DIR/rdc-proxy.env" || true
fi

# ── Confirmation ───────────────────────────────────────────────────────────
if [[ "${YES:-0}" != "1" ]]; then
  cat <<EOF

About to uninstall rdc-proxy. This will:
  • stop and disable rdc-proxy.service
  • remove /etc/systemd/system/rdc-proxy.service
  • remove the br0 systemd-networkd config files
  • remove the NetworkManager unmanaged-bridge.conf
  • remove the TPROXY iptables, ebtables broute, ip rule + table-100 entries
  • remove $INSTALL_DIR (code + venv)
EOF
  if [[ "${KEEP_CONFIG:-0}" == "1" ]]; then
    echo "  • KEEP $CONFIG_DIR (KEEP_CONFIG=1)"
  else
    echo "  • remove $CONFIG_DIR (config + handshake) — set KEEP_CONFIG=1 to preserve"
  fi
  if [[ "${PURGE_APT:-0}" == "1" ]]; then
    echo "  • apt-get remove ebtables tcpdump bridge-utils  (PURGE_APT=1)"
    echo "    (we never remove python/git/iptables — those are too widely shared)"
  fi
  echo
  echo "It will NOT reboot, will NOT touch your DHCP/router config, and will NOT"
  echo "tear down br0 itself if it's currently carrying your network — see the"
  echo "post-uninstall note at the end."
  echo
  read -r -p "Continue? [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || { echo "aborted."; exit 1; }
fi

# ── 1. Stop & disable the service ──────────────────────────────────────────
step "1/6  stop service"
# Use the unit file as the source of truth — systemctl list-unit-files can
# under-report depending on systemd's cache state, and we don't want to
# silently skip the stop while the rules underneath get yanked.
unit_file=/etc/systemd/system/${SERVICE_NAME}.service
if [[ -f "$unit_file" ]] || systemctl cat "${SERVICE_NAME}.service" >/dev/null 2>&1; then
  systemctl disable --now "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
  ok "${SERVICE_NAME}.service stopped + disabled"
else
  skip "${SERVICE_NAME}.service not installed"
fi

# Belt and braces: kill any leftover process that didn't go down with the
# service (e.g. unit was disabled out from under a still-running PID, or
# someone launched the proxy by hand).
if pgrep -f 'python.* -m rdc_proxy$' >/dev/null 2>&1; then
  pkill -TERM -f 'python.* -m rdc_proxy$' || true
  sleep 1
  if pgrep -f 'python.* -m rdc_proxy$' >/dev/null 2>&1; then
    pkill -KILL -f 'python.* -m rdc_proxy$' || true
    warn "force-killed stuck rdc-proxy process"
  else
    ok "stopped stray rdc-proxy process"
  fi
fi

# Now remove the unit file. ExecStopPost handlers in the unit removed the
# iptables / ebtables / ip-rule entries when the stop above fired; we still
# re-issue the deletes below in case the service was already broken and
# the stop hook never ran.
if [[ -f "$unit_file" ]]; then
  rm -f "$unit_file"
  systemctl daemon-reload
  ok "removed $unit_file"
else
  skip "no systemd unit file to remove"
fi

# ── 2. Network rules (idempotent — ignore "rule does not exist") ───────────
step "2/6  network rules"

# iptables TPROXY mangle rule
if iptables -t mangle -C PREROUTING -i "$BRIDGE_NAME" -s "$RDC_IP" -p tcp \
     --dport "$PROXY_PORT" -j TPROXY --tproxy-mark 0x1/0x1 \
     --on-port "$PROXY_PORT" 2>/dev/null; then
  iptables -t mangle -D PREROUTING -i "$BRIDGE_NAME" -s "$RDC_IP" -p tcp \
    --dport "$PROXY_PORT" -j TPROXY --tproxy-mark 0x1/0x1 \
    --on-port "$PROXY_PORT" 2>/dev/null && ok "removed TPROXY mangle rule"
else
  skip "TPROXY mangle rule not present"
fi

# ebtables broute redirect
if [[ -x /usr/sbin/ebtables-legacy ]]; then
  if /usr/sbin/ebtables-legacy -t broute -L BROUTING 2>/dev/null \
       | grep -q "ip-src ${RDC_IP}.*ip-dport ${PROXY_PORT}"; then
    /usr/sbin/ebtables-legacy -t broute -D BROUTING -p IPv4 \
      --ip-proto tcp --ip-dport "$PROXY_PORT" --ip-src "$RDC_IP" \
      -j redirect --redirect-target ACCEPT 2>/dev/null \
      && ok "removed ebtables broute rule"
  else
    skip "ebtables broute rule not present"
  fi
else
  skip "ebtables-legacy not installed"
fi

# ip rule + routing table 100
if ip rule show | grep -q "fwmark 0x1 lookup 100"; then
  ip rule del fwmark 1 lookup 100 2>/dev/null && ok "removed ip rule fwmark 1 -> table 100"
else
  skip "no fwmark ip rule"
fi
if ip route show table 100 2>/dev/null | grep -q "local default dev lo"; then
  ip route del local 0.0.0.0/0 dev lo table 100 2>/dev/null \
    && ok "removed routing table 100 entry"
else
  skip "routing table 100 already empty"
fi

# Reset bridge-nf-call-iptables. Other bridges may want this on; leave it
# in its install-default state of 0 only if no other bridge is present.
if [[ -e /proc/sys/net/bridge/bridge-nf-call-iptables ]]; then
  other_bridges=""
  for path in /sys/class/net/*; do
    iface=$(basename "$path")
    [[ "$iface" == "$BRIDGE_NAME" ]] && continue
    [[ -d "$path/bridge" ]] && other_bridges+="${iface} "
  done
  other_bridges=${other_bridges% }
  if [[ -z "$other_bridges" ]]; then
    echo 0 > /proc/sys/net/bridge/bridge-nf-call-iptables 2>/dev/null \
      && ok "reset bridge-nf-call-iptables=0 (no other bridges present)"
  else
    skip "left bridge-nf-call-iptables alone (other bridge(s) present: $other_bridges)"
  fi
fi

# ── 3. Bridge config files (don't tear down br0 itself) ────────────────────
step "3/6  bridge config files"

# We deliberately do NOT `ip link del br0`. If the user is SSH'd in via br0,
# that kills their session. Removing the systemd-networkd config files and
# the NM unmanaged.conf is the persistent change; br0 itself drops at next
# reboot or when the user runs `networkctl reload && networkctl down br0`.
removed_any=0
for f in /etc/systemd/network/05-rdc-proxy-br0.netdev \
         /etc/systemd/network/05-rdc-proxy-br0-members.network \
         /etc/systemd/network/05-rdc-proxy-br0.network \
         /etc/systemd/network/10-br0.netdev \
         /etc/systemd/network/20-br0-members.network \
         /etc/systemd/network/30-br0.network; do
  if [[ -f "$f" ]]; then
    rm -f "$f"
    ok "removed $f"
    removed_any=1
  fi
done
[[ $removed_any -eq 0 ]] && skip "no systemd-networkd br0 config files"

# Restore any netplan files the bridge installer dispossessed.
shopt -s nullglob
backups=(/etc/netplan/*.rdc-backup)
shopt -u nullglob
if [[ ${#backups[@]} -gt 0 ]]; then
  for b in "${backups[@]}"; do
    orig="${b%.rdc-backup}"
    mv "$b" "$orig"
    ok "restored netplan: $orig"
  done
  command -v netplan >/dev/null 2>&1 && netplan generate >/dev/null 2>&1 || true
else
  skip "no netplan backups to restore"
fi

if [[ -f /etc/NetworkManager/conf.d/unmanaged-bridge.conf ]]; then
  rm -f /etc/NetworkManager/conf.d/unmanaged-bridge.conf
  ok "removed /etc/NetworkManager/conf.d/unmanaged-bridge.conf"
  if systemctl is-active --quiet NetworkManager; then
    systemctl reload NetworkManager 2>/dev/null || true
  fi
else
  skip "no NetworkManager unmanaged-bridge.conf"
fi

# Reload systemd-networkd so the config removal takes effect on next boot.
if systemctl is-active --quiet systemd-networkd; then
  networkctl reload 2>/dev/null || systemctl reload systemd-networkd 2>/dev/null || true
  ok "reloaded systemd-networkd"
fi

# ── 4. Application files ───────────────────────────────────────────────────
step "4/6  application files"
if [[ -d "$INSTALL_DIR" ]]; then
  rm -rf "$INSTALL_DIR"
  ok "removed $INSTALL_DIR"
else
  skip "$INSTALL_DIR not present"
fi

if [[ "${KEEP_CONFIG:-0}" == "1" ]]; then
  if [[ -d "$CONFIG_DIR" ]]; then
    ok "kept $CONFIG_DIR (KEEP_CONFIG=1)"
  else
    skip "$CONFIG_DIR not present"
  fi
elif [[ -d "$CONFIG_DIR" ]]; then
  rm -rf "$CONFIG_DIR"
  ok "removed $CONFIG_DIR"
else
  skip "$CONFIG_DIR not present"
fi

# Install log
if [[ -f /var/log/rdc-proxy-install.log ]]; then
  rm -f /var/log/rdc-proxy-install.log
  ok "removed /var/log/rdc-proxy-install.log"
fi

# ── 5. Optional apt purge ──────────────────────────────────────────────────
step "5/6  apt packages"
if [[ "${PURGE_APT:-0}" == "1" ]]; then
  # Only the things the user is least likely to need elsewhere. We never
  # touch python/git/iptables — those are baseline tools.
  DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq \
    ebtables tcpdump bridge-utils >/dev/null 2>&1 || true
  ok "apt-get remove ebtables tcpdump bridge-utils"
else
  skip "PURGE_APT=1 not set — leaving apt packages installed"
fi

# ── 6. Summary ─────────────────────────────────────────────────────────────
step "6/6  done"
cat <<EOF

${G}rdc-proxy uninstalled.${N}

Notes:
  • ${BRIDGE_NAME} itself was NOT torn down at runtime, only its boot
    config. To remove it without rebooting:

      sudo ip link set ${BRIDGE_NAME} down && sudo ip link del ${BRIDGE_NAME}

    If you're SSH'd in through ${BRIDGE_NAME}, that will drop your session.
    The bridge will not come back on next boot.

  • If you ran ${BRIDGE_NAME} for purposes other than rdc-proxy, recreate
    its systemd-networkd config before rebooting.

  • Verify nothing rdc-proxy-related remains:
      systemctl status rdc-proxy        # should say "could not be found"
      iptables -t mangle -S PREROUTING  # no TPROXY line
      ls /etc/systemd/network            # no br0 files
EOF
