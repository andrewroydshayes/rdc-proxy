#!/usr/bin/env bash
# One-shot installer for rdc-proxy on a Raspberry Pi.
#
#   curl -fsSL https://raw.githubusercontent.com/andrewroydshayes/rdc-proxy/main/install/install.sh \
#     | sudo RDC_IP=10.0.0.50 bash
#
# Idempotent. Prints a green/red report at the end.

set -euo pipefail

# ── Disclaimer ─────────────────────────────────────────────────────────────
# Shown before any action so the user has a chance to abort before `sudo`
# modifies anything.
if [[ "${RDC_SKIP_DISCLAIMER:-}" != "1" ]]; then
cat <<'DISCLAIMER'

  ╔═══════════════════════════════════════════════════════════════════════╗
  ║  rdc-proxy — unofficial community project                             ║
  ║                                                                       ║
  ║  Not made by, endorsed by, or supported by Kohler or Rehlko.          ║
  ║  Runs on YOUR hardware, YOUR network, near YOUR generator.            ║
  ║  You are responsible for your install.                                ║
  ║                                                                       ║
  ║  • Passive / read-only on the wire — should do no harm.               ║
  ║  • Nothing phones home. Only outbound traffic: your generator's       ║
  ║    existing Kohler cloud link, and apt/PyPI package downloads.        ║
  ║  • MIT licensed "AS IS." No warranty. No liability.                   ║
  ║  • Source open at https://github.com/andrewroydshayes/rdc-proxy       ║
  ║                                                                       ║
  ║  Full disclaimer: README.md on GitHub.                                ║
  ╚═══════════════════════════════════════════════════════════════════════╝

DISCLAIMER
for i in 10 9 8 7 6 5 4 3 2 1; do
  printf "\r  continuing in %2ds — press Ctrl+C to abort..." "$i"
  sleep 1
done
printf "\r%60s\r\n" ""
fi

# ── SSH survival ───────────────────────────────────────────────────────────
# Step 6 puts eth0 into the bridge, which strips its IP. If the user is SSH'd
# in on eth0, that kills their session. Ignore SIGHUP so we don't die with it,
# and tee everything to a log file so the output is recoverable after reconnect.
trap '' HUP
mkdir -p /var/log
exec > >(tee -a /var/log/rdc-proxy-install.log) 2>&1
echo "=== rdc-proxy install: $(date -Iseconds) ==="

# ── Config ─────────────────────────────────────────────────────────────────
REPO_URL=${REPO_URL:-https://github.com/andrewroydshayes/rdc-proxy.git}
INSTALL_DIR=${INSTALL_DIR:-/opt/rdc-proxy}
CONFIG_DIR=${CONFIG_DIR:-/etc/rdc-proxy}
SERVICE_NAME=rdc-proxy
RDC_IP=${RDC_IP:-10.0.0.50}
PROXY_PORT=${PROXY_PORT:-5253}
BRANCH=${BRANCH:-}   # empty = pick latest tag; else explicit branch/tag

# ── Terminal colours ───────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  G=$(printf '\033[32m'); R=$(printf '\033[31m'); Y=$(printf '\033[33m'); N=$(printf '\033[0m')
else
  G=""; R=""; Y=""; N=""
fi

PASS=()
FAIL=()
WARN=()

ok()    { PASS+=("$1"); echo "${G}✓${N} $1"; }
fail()  { FAIL+=("$1"); echo "${R}✗${N} $1"; }
warn()  { WARN+=("$1"); echo "${Y}!${N} $1"; }
step()  { echo; echo "${G}── $1 ──${N}"; }

# ── Prerequisites ──────────────────────────────────────────────────────────
step "1/8  prerequisites"

if [[ $EUID -ne 0 ]]; then
  fail "install.sh must be run as root (sudo)"
  exit 1
fi
ok "running as root"

if [[ "$(uname -s)" != "Linux" ]]; then
  fail "unsupported OS: $(uname -s) (need Linux)"
  exit 1
fi
ok "OS: Linux"

case "$(uname -m)" in
  aarch64|armv7l|arm64) ok "arch: $(uname -m)" ;;
  *) warn "untested arch: $(uname -m) (Pi targets are aarch64/armv7l)" ;;
esac

KREL=$(uname -r); KMAJ=${KREL%%.*}; KMIN=$(echo "$KREL" | cut -d. -f2)
if (( KMAJ > 5 || (KMAJ == 5 && KMIN >= 15) )); then
  ok "kernel $KREL (>= 5.15)"
else
  fail "kernel $KREL is too old for TPROXY+bridge (need >= 5.15)"
fi

if command -v apt-get >/dev/null 2>&1; then
  ok "apt-get present"
else
  fail "no apt-get (this installer targets Debian / Raspberry Pi OS)"
  exit 1
fi

DISK_FREE_GB=$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')
if (( DISK_FREE_GB >= 2 )); then
  ok "disk free: ${DISK_FREE_GB} GB"
else
  fail "disk free ${DISK_FREE_GB} GB < 2 GB"
fi

if curl -fsS -m 10 -o /dev/null https://github.com; then
  ok "internet reachable (github.com)"
else
  fail "cannot reach github.com"
fi

[[ ${#FAIL[@]} -gt 0 ]] && { echo; echo "${R}prerequisite check failed${N}"; exit 1; }

# ── Packages ───────────────────────────────────────────────────────────────
step "2/8  apt packages"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  git python3 python3-pip python3-venv python3-flask \
  iptables ebtables tcpdump bridge-utils \
  >/dev/null
ok "installed: git python3 pip venv flask iptables ebtables tcpdump"

# Check ebtables-legacy specifically (Debian's default is nft shim which breaks broute)
if [[ -x /usr/sbin/ebtables-legacy ]]; then
  ok "ebtables-legacy present (required for broute redirect)"
else
  fail "/usr/sbin/ebtables-legacy missing even after apt install ebtables"
fi

# ── Fetch source ───────────────────────────────────────────────────────────
step "3/8  clone repo"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch --tags --quiet
  ok "repo already present, fetched latest"
else
  git clone --quiet "$REPO_URL" "$INSTALL_DIR"
  ok "cloned $REPO_URL -> $INSTALL_DIR"
fi

# Check out requested branch or latest tag
if [[ -n "$BRANCH" ]]; then
  git -C "$INSTALL_DIR" checkout --quiet "$BRANCH"
  ok "checked out $BRANCH"
else
  LATEST_TAG=$(git -C "$INSTALL_DIR" tag --sort=-v:refname | head -1)
  if [[ -n "$LATEST_TAG" ]]; then
    git -C "$INSTALL_DIR" checkout --quiet "$LATEST_TAG"
    ok "checked out latest tag: $LATEST_TAG"
  else
    warn "no tags yet — using main"
    git -C "$INSTALL_DIR" checkout --quiet main
  fi
fi

# ── Python venv ────────────────────────────────────────────────────────────
step "4/8  python venv"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet "$INSTALL_DIR"
ok "venv + package installed at $INSTALL_DIR/venv"

# ── Config dir ─────────────────────────────────────────────────────────────
step "5/8  config"
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/rdc-proxy.env" ]]; then
  cat > "$CONFIG_DIR/rdc-proxy.env" <<EOF
RDC_IP=$RDC_IP
PROXY_PORT=$PROXY_PORT
RDC_PROXY_CONFIG_DIR=$CONFIG_DIR
EOF
  ok "wrote $CONFIG_DIR/rdc-proxy.env"
else
  ok "$CONFIG_DIR/rdc-proxy.env already exists (preserved)"
fi

# ── Pick the second bridge member ──────────────────────────────────────────
# setup-bridge.sh puts eth0 + a second Ethernet interface into br0. On a Pi
# with a USB→Ethernet adapter, the second interface is *usually* named eth1,
# but Debian/systemd can hand it a predictable-MAC-based name (enx<mac>),
# or a platform-specific name like usb0. Hardcoding "eth1" silently produces
# a half-assembled bridge. Detect candidates and, if there's only one,
# pick it; if there are multiple, prompt; if there are none, error out.
#
# Override: set SECOND_IFACE=<name> in the environment to skip detection.
if [[ -z "${SECOND_IFACE:-}" ]]; then
  CANDIDATES=()
  for path in /sys/class/net/*; do
    iface=$(basename "$path")
    [[ "$iface" == "lo" || "$iface" == "eth0" ]] && continue
    [[ "$iface" == br* ]] && continue                  # bridge itself
    [[ -d "$path/wireless" ]] && continue              # wifi
    [[ -d "$path/bridge" ]] && continue               # is a bridge
    # skip virtual/tunnel types
    case "$iface" in
      veth*|docker*|tap*|tun*|ppp*|vxlan*|wg*|tailscale*|zt*|cni*|flannel*) continue ;;
    esac
    # must be ethernet (type 1)
    [[ "$(cat "$path/type" 2>/dev/null)" == "1" ]] || continue
    CANDIDATES+=("$iface")
  done

  if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
    fail "no second Ethernet interface found besides eth0"
    echo
    echo "${R}The USB→Ethernet adapter isn't showing up. Plug it in and re-run,${N}"
    echo "${R}or pass SECOND_IFACE=<name> if it's named something non-standard.${N}"
    echo "Current interfaces:"
    ip -br link
    exit 1
  elif [[ ${#CANDIDATES[@]} -eq 1 ]]; then
    SECOND_IFACE="${CANDIDATES[0]}"
    ok "second bridge member: $SECOND_IFACE (auto-detected)"
  else
    # Multiple candidates — let the user pick. /dev/tty works even when
    # the script itself was piped in from `curl`.
    echo
    echo "${Y}Multiple Ethernet interfaces found besides eth0.${N}"
    echo "Which one is the adapter connected to your switch?"
    echo
    for i in "${!CANDIDATES[@]}"; do
      iface="${CANDIDATES[$i]}"
      mac=$(cat "/sys/class/net/$iface/address" 2>/dev/null || echo "?")
      carrier=$(cat "/sys/class/net/$iface/carrier" 2>/dev/null || echo "0")
      link_state=$([[ "$carrier" == "1" ]] && echo "${G}link up${N}" || echo "${Y}no carrier${N}")
      echo "  $((i+1))) $iface  (mac $mac, $link_state)"
    done
    echo
    if [[ -e /dev/tty ]]; then
      while true; do
        read -r -p "Enter the number [1-${#CANDIDATES[@]}]: " choice < /dev/tty
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#CANDIDATES[@]} )); then
          SECOND_IFACE="${CANDIDATES[$((choice-1))]}"
          break
        fi
        echo "${R}Invalid choice. Enter a number between 1 and ${#CANDIDATES[@]}.${N}"
      done
      ok "second bridge member: $SECOND_IFACE (selected)"
    else
      fail "no TTY available for prompt — re-run with SECOND_IFACE=<name>"
      exit 1
    fi
  fi
fi

export MEMBERS="eth0 $SECOND_IFACE"

# ── Bridge ─────────────────────────────────────────────────────────────────
# Heads-up: if the user is SSH'd in on an interface that's about to become a
# bridge member, their session will die (bridge members lose their own IPs).
# Only warn when that's actually the case — wifi/other-iface SSH is fine.
BRIDGE_MEMBERS="$MEMBERS"
SSH_DYING_IFACE=""
SSH_DYING_IP=""
if [[ -n "${SSH_CONNECTION:-}" ]]; then
  SSH_SERVER_IP=$(echo "$SSH_CONNECTION" | awk '{print $3}')
  for iface in $BRIDGE_MEMBERS; do
    IFACE_IP=$(ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
    if [[ -n "$IFACE_IP" && "$SSH_SERVER_IP" == "$IFACE_IP" ]]; then
      SSH_DYING_IFACE="$iface"
      SSH_DYING_IP="$IFACE_IP"
      break
    fi
  done
fi

if [[ -n "$SSH_DYING_IFACE" ]]; then
  HOSTNAME_SHORT=$(hostname)
  cat <<WARN

${Y}╔═══════════════════════════════════════════════════════════════════════╗${N}
${Y}║  HEADS UP — YOUR SSH SESSION IS ABOUT TO DROP                         ║${N}
${Y}╠═══════════════════════════════════════════════════════════════════════╣${N}
  You're SSH'd in on ${SSH_DYING_IFACE} (${SSH_DYING_IP}).
  The next step moves ${SSH_DYING_IFACE} into a bridge, which strips its IP.
  Your SSH session will die within a few seconds. This is expected,
  not a failure.

  The install keeps running in the background. When SSH drops:

    1. reconnect via wifi, or: ${G}ssh pi@${HOSTNAME_SHORT}.local${N}
    2. dashboard:              ${G}http://${HOSTNAME_SHORT}.local/${N}
    3. full install log:       ${G}sudo cat /var/log/rdc-proxy-install.log${N}
    4. service status:         ${G}systemctl status rdc-proxy${N}

  If anything looks off, re-run the installer — it's idempotent.

${Y}╚═══════════════════════════════════════════════════════════════════════╝${N}

WARN
  for i in 10 9 8 7 6 5 4 3 2 1; do
    printf "\r  bridge setup in %2ds — Ctrl+C to abort..." "$i"
    sleep 1
  done
  printf "\r%60s\r\n" ""
fi

step "6/8  bridge (br0 over $MEMBERS)"
# Always run setup-bridge.sh — it's idempotent and we need it to re-run
# whenever the second bridge member might have changed (e.g. earlier
# install picked the wrong iface, or USB adapter was replaced).
bash "$INSTALL_DIR/install/setup-bridge.sh"
ok "br0 configured via setup-bridge.sh (members: $MEMBERS)"

# ── Install systemd unit ───────────────────────────────────────────────────
step "7/8  systemd unit"
install -m 644 "$INSTALL_DIR/install/rdc-proxy.service" /etc/systemd/system/rdc-proxy.service
systemctl daemon-reload
systemctl enable --now $SERVICE_NAME.service >/dev/null 2>&1
ok "rdc-proxy.service enabled + started"

# ── Self-check ─────────────────────────────────────────────────────────────
step "8/8  self-check"
sleep 3

systemctl is-active --quiet $SERVICE_NAME && ok "$SERVICE_NAME is active" || fail "$SERVICE_NAME not active"

# br0 must actually exist with both members enslaved. The TPROXY + ebtables
# rules below match `-i br0`, so without the bridge they're inert: install
# looks green, generator traffic never gets diverted to the proxy. This is
# the silent failure mode that doctor exists to catch.
if ip link show br0 >/dev/null 2>&1; then
  ok "br0 interface exists"
  members_ok=1
  for m in $MEMBERS; do
    if bridge link show 2>/dev/null | grep -qE "^[[:space:]]*[0-9]+:[[:space:]]+${m}[@:].*master br0"; then
      :
    else
      members_ok=0
      fail "br0 member missing: $m"
    fi
  done
  [[ $members_ok -eq 1 ]] && ok "br0 members enslaved: $MEMBERS"
else
  fail "br0 interface does not exist (TPROXY rules will not intercept anything)"
fi

if ss -tlnp "( sport = :$PROXY_PORT )" 2>/dev/null | grep -q python; then
  ok "proxy listening on :$PROXY_PORT"
else
  fail "nothing listening on :$PROXY_PORT"
fi

if iptables -t mangle -L PREROUTING -n 2>/dev/null | grep -q "TPROXY.*$PROXY_PORT"; then
  ok "TPROXY rule present"
else
  fail "TPROXY rule missing"
fi

if /usr/sbin/ebtables-legacy -t broute -L BROUTING 2>/dev/null | grep -q "ip-dport $PROXY_PORT"; then
  ok "ebtables broute rule present"
else
  fail "ebtables broute rule missing"
fi

WEB_PORT=$(awk -F= '/^web_port/ {print $2}' "$CONFIG_DIR/config.json" 2>/dev/null || true)
WEB_PORT=${WEB_PORT:-80}
if curl -fsS -m 5 -o /dev/null "http://localhost:${WEB_PORT}/api/status"; then
  ok "dashboard /api/status OK (port ${WEB_PORT})"
else
  fail "dashboard not responding on port ${WEB_PORT}"
fi

# ── Report ─────────────────────────────────────────────────────────────────
echo
echo "── Install summary ──"
echo "${G}Passed (${#PASS[@]}):${N}"
printf '  ✓ %s\n' "${PASS[@]}"
if [[ ${#WARN[@]} -gt 0 ]]; then
  echo "${Y}Warnings (${#WARN[@]}):${N}"
  printf '  ! %s\n' "${WARN[@]}"
fi
if [[ ${#FAIL[@]} -gt 0 ]]; then
  echo "${R}Failed (${#FAIL[@]}):${N}"
  printf '  ✗ %s\n' "${FAIL[@]}"
  echo
  echo "Fix the failures above, then re-run this installer. It is idempotent."
  exit 1
fi

echo
echo "${G}rdc-proxy installed successfully.${N}"
echo "Dashboard: http://$(hostname -I | awk '{print $1}')/"
echo "Logs:      journalctl -u $SERVICE_NAME -f"
