#!/usr/bin/env bash
# Bring up a transparent L2 bridge over eth0 + eth1 using systemd-networkd.
# Idempotent — running twice is fine.
set -euo pipefail

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "setup-bridge.sh must be run as root" >&2
    exit 1
  fi
}

BRIDGE_NAME=${BRIDGE_NAME:-br0}
MEMBERS=${MEMBERS:-eth0 eth1}

main() {
  require_root
  echo "[bridge] setting up $BRIDGE_NAME (members: $MEMBERS)"

  # 1. Make sure systemd-networkd is the active network manager for these ifaces.
  mkdir -p /etc/systemd/network

  cat > /etc/systemd/network/10-br0.netdev <<EOF
[NetDev]
Name=${BRIDGE_NAME}
Kind=bridge
EOF

  cat > /etc/systemd/network/20-br0-members.network <<EOF
[Match]
Name=$(echo "$MEMBERS" | sed 's/ /|/g')

[Network]
Bridge=${BRIDGE_NAME}
EOF

  cat > /etc/systemd/network/30-br0.network <<EOF
[Match]
Name=${BRIDGE_NAME}

[Network]
DHCP=yes
IPForward=yes
ConfigureWithoutCarrier=yes
EOF

  # 2. If NetworkManager is present, tell it not to manage our ports/bridge
  if systemctl is-active --quiet NetworkManager; then
    mkdir -p /etc/NetworkManager/conf.d
    # Build the unmanaged-devices list from the actual bridge members
    # (so USB adapters named enx<mac> or usb0 get excluded too).
    nm_list="interface-name:${BRIDGE_NAME}"
    for m in $MEMBERS; do
      nm_list="${nm_list};interface-name:${m}"
    done
    cat > /etc/NetworkManager/conf.d/unmanaged-bridge.conf <<EOF
[keyfile]
unmanaged-devices=${nm_list}
EOF
    systemctl reload NetworkManager || true
  fi

  # 3. Enable systemd-networkd
  systemctl enable --now systemd-networkd

  echo "[bridge] done. It can take 10-30s to get a DHCP lease on ${BRIDGE_NAME}."
  echo "[bridge] check with: ip -br addr show ${BRIDGE_NAME}"
}

main "$@"
