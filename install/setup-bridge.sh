#!/usr/bin/env bash
# Bring up a transparent L2 bridge over eth0 + eth1 using systemd-networkd.
#
# Idempotent — safe to re-run.
#
# Why this is more involved than just dropping .network files:
# Pi OS Bookworm and most cloud-image Debians ship with a different network
# manager already owning the would-be bridge members. NetworkManager (Pi OS
# default) and netplan-generated systemd-networkd files (cloud images) both
# get a higher-priority claim on eth0 than our 20-* files do. So the bridge
# config is written, systemd-networkd is enabled, but eth0 never actually
# enslaves into the bridge. install.sh's TPROXY rule references `-i br0`,
# so on that broken-but-quiet state, no traffic is intercepted.
#
# This script actively dispossesses NetworkManager / netplan of the member
# interfaces, writes the systemd-networkd config at a priority that wins,
# triggers a reload, and verifies br0 exists with members enslaved before
# returning success.
set -euo pipefail

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "setup-bridge.sh must be run as root" >&2
    exit 1
  fi
}

BRIDGE_NAME=${BRIDGE_NAME:-br0}
MEMBERS=${MEMBERS:-eth0 eth1}

# ── helpers ─────────────────────────────────────────────────────────────────

# Wait up to N seconds for `cmd` to succeed. Used so we don't spin forever
# during the verify step but also don't race networkctl reload.
wait_for() {
  local timeout=$1; shift
  local i=0
  until "$@" >/dev/null 2>&1; do
    (( i++ >= timeout )) && return 1
    sleep 1
  done
}

# Membership predicate: true once `bridge link` reports both members under br0.
bridge_has_all_members() {
  command -v bridge >/dev/null 2>&1 || return 0  # if `bridge` isn't installed, skip
  local out
  out=$(bridge link show 2>/dev/null) || return 1
  for m in $MEMBERS; do
    grep -qE "^[[:space:]]*[0-9]+:[[:space:]]+${m}[@:].*master ${BRIDGE_NAME}" <<<"$out" || return 1
  done
  return 0
}

# ── 1. Write systemd-networkd config ────────────────────────────────────────
# 05-* prefix: must beat netplan-generated 10-netplan-*.network files which
# would otherwise claim eth0 first (lexical match order across /etc and /run).
write_networkd_config() {
  mkdir -p /etc/systemd/network

  cat > /etc/systemd/network/05-rdc-proxy-br0.netdev <<EOF
[NetDev]
Name=${BRIDGE_NAME}
Kind=bridge
EOF

  cat > /etc/systemd/network/05-rdc-proxy-br0-members.network <<EOF
[Match]
Name=$(echo "$MEMBERS" | sed 's/ /|/g')

[Network]
Bridge=${BRIDGE_NAME}
EOF

  cat > /etc/systemd/network/05-rdc-proxy-br0.network <<EOF
[Match]
Name=${BRIDGE_NAME}

[Network]
DHCP=yes
IPForward=yes
ConfigureWithoutCarrier=yes
EOF

  # If a previous installer wrote the old 10-/20-/30- file names, remove them.
  # (They'd still match systemd-networkd but our 05-* files take precedence;
  # we delete the stale ones so the system has a single source of truth.)
  rm -f /etc/systemd/network/10-br0.netdev \
        /etc/systemd/network/20-br0-members.network \
        /etc/systemd/network/30-br0.network
}

# ── 2. Dispossess NetworkManager (Pi OS Bookworm default) ───────────────────
# NM holds active connection profiles on eth0 / eth1. Writing unmanaged.conf
# alone doesn't release them — NM keeps the existing connection up until the
# device is explicitly disconnected or its profile deleted. Delete the
# profiles so NM lets go, then write unmanaged.conf so it never reclaims.
dispossess_networkmanager() {
  systemctl is-active --quiet NetworkManager || return 0
  if ! command -v nmcli >/dev/null 2>&1; then
    return 0
  fi

  # Delete any active connection bound to a member iface. nmcli's -t/-f
  # gives us machine-parseable output (NAME:DEVICE) that survives spaces.
  for m in $MEMBERS; do
    while IFS=: read -r name device; do
      [[ -z "$name" || "$device" != "$m" ]] && continue
      echo "[bridge] dispossessing NetworkManager: deleting connection '$name' on $device"
      nmcli connection delete "$name" >/dev/null 2>&1 || true
    done < <(nmcli -t -f NAME,DEVICE connection show 2>/dev/null)
  done

  # Now mark the bridge + members as unmanaged so NM doesn't reclaim them
  # on hotplug or service reload.
  mkdir -p /etc/NetworkManager/conf.d
  nm_list="interface-name:${BRIDGE_NAME}"
  for m in $MEMBERS; do
    nm_list="${nm_list};interface-name:${m}"
  done
  cat > /etc/NetworkManager/conf.d/unmanaged-bridge.conf <<EOF
[keyfile]
unmanaged-devices=${nm_list}
EOF
  systemctl reload NetworkManager 2>/dev/null || true
}

# ── 3. Dispossess netplan (cloud Debian / Ubuntu Server) ───────────────────
# netplan compiles its YAML into /run/systemd/network/10-netplan-*.network,
# which beats anything we'd put at priority 20+. Easiest fix: rewrite the
# netplan config so it knows about our bridge (renderer=networkd, no
# eth0/eth1 stanzas — let our 05-* files own them). Backs up the original.
dispossess_netplan() {
  command -v netplan >/dev/null 2>&1 || return 0
  shopt -s nullglob
  local files=(/etc/netplan/*.yaml /etc/netplan/*.yml)
  shopt -u nullglob
  [[ ${#files[@]} -eq 0 ]] && return 0

  local touched=0
  for f in "${files[@]}"; do
    # Only intervene if this file mentions one of our member ifaces by name.
    local match=0
    for m in $MEMBERS; do
      if grep -qE "(^|[[:space:]])${m}:" "$f"; then match=1; break; fi
    done
    [[ $match -eq 0 ]] && continue

    [[ ! -f "${f}.rdc-backup" ]] && cp -p "$f" "${f}.rdc-backup"
    cat > "$f" <<EOF
# Replaced by rdc-proxy setup-bridge.sh — original at $(basename "$f").rdc-backup
# rdc-proxy manages eth0/eth1 + br0 directly via /etc/systemd/network/05-*.
network:
  version: 2
  renderer: networkd
EOF
    chmod 600 "$f"
    echo "[bridge] dispossessed netplan in $f (original backed up)"
    touched=1
  done
  if [[ $touched -eq 1 ]]; then
    netplan generate >/dev/null 2>&1 || true
  fi
}

# ── 4. Apply + verify ──────────────────────────────────────────────────────
apply_and_verify() {
  systemctl enable --now systemd-networkd >/dev/null 2>&1 || true

  # `networkctl reload` re-reads .network/.netdev files without bouncing the
  # service (so existing connections are preserved). On systemd < 248 this
  # command doesn't exist; fall back to a service reload.
  if networkctl reload 2>/dev/null; then :;
  else systemctl reload systemd-networkd 2>/dev/null || true
  fi

  # Bring members up explicitly. ConfigureWithoutCarrier handles the case
  # where a member has no link yet (e.g. the second NIC waiting for the
  # generator), but we still want carrier on the LAN-side member.
  for m in $MEMBERS; do
    ip link set "$m" up 2>/dev/null || true
  done

  # Wait up to 30s for the bridge to materialize.
  if ! wait_for 30 ip link show "$BRIDGE_NAME"; then
    echo "[bridge] FAIL: ${BRIDGE_NAME} did not appear after 30s." >&2
    echo "[bridge]   networkctl status ${BRIDGE_NAME}:" >&2
    networkctl status "${BRIDGE_NAME}" 2>&1 | head -20 >&2 || true
    return 1
  fi

  # Wait up to another 15s for member enslavement (bridge link).
  if ! wait_for 15 bridge_has_all_members; then
    echo "[bridge] FAIL: ${BRIDGE_NAME} exists but members not enslaved." >&2
    echo "[bridge]   bridge link:" >&2
    bridge link 2>&1 | sed 's/^/    /' >&2 || true
    return 1
  fi

  echo "[bridge] ${BRIDGE_NAME} is up with members: $MEMBERS"
}

main() {
  require_root
  echo "[bridge] setting up $BRIDGE_NAME (members: $MEMBERS)"
  write_networkd_config
  dispossess_networkmanager
  dispossess_netplan
  apply_and_verify
  echo "[bridge] done. ${BRIDGE_NAME} may take 10-30s more to acquire a DHCP lease."
  echo "[bridge] check with: ip -br addr show ${BRIDGE_NAME}"
}

main "$@"
