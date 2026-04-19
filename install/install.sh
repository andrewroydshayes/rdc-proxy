#!/usr/bin/env bash
# One-shot installer for rdc-proxy on a Raspberry Pi.
#
#   curl -fsSL https://raw.githubusercontent.com/andrewroydshayes/rdc-proxy/main/install/install.sh \
#     | sudo RDC_IP=10.0.0.50 bash
#
# Idempotent. Prints a green/red report at the end.

set -euo pipefail

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

# ── Bridge ─────────────────────────────────────────────────────────────────
step "6/8  bridge (br0 over eth0+eth1)"
if ip link show br0 >/dev/null 2>&1; then
  ok "br0 already exists"
else
  bash "$INSTALL_DIR/install/setup-bridge.sh"
  ok "br0 configured via setup-bridge.sh"
fi

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
