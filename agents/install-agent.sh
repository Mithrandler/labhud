#!/bin/sh
# Installs labhud-agent (the Go agent, docs/agents.md) as a systemd service. Served by labhud:
#
#   curl -fsSL http://<labhud>:8095/agent/install-agent.sh | sudo sh -s -- --url http://<labhud>:8095 --code <code>
#
# The code comes from ⚙ > Agents > Add an agent (once, 5 minutes), which shows this line with the
# code in it. The binary is downloaded from the release and checked against the SHA-256 labhud
# itself pins (agents/agent-release.json), not against a file next to the binary. When labhud
# pins no release (built from main, or before the agent's first release) nothing is downloaded:
# bring the binary with --binary and the SHA-256 you got where you built it with --sha256.
# Run again to upgrade: the agent keeps its key.
#
# Options:
#   --url URL           labhud's address (required)
#   --code CODE         the pairing code (not needed when /etc/labhud-agent/key exists)
#   --name NAME         the name in labhud (default: the host name)
#   --binary FILE       use this file instead of downloading (still checked: against --sha256
#                       when given, else against the checksum labhud pins)
#   --sha256 HEX        the expected SHA-256 of --binary, from where it was built
#   --ptero-url URL     a Pterodactyl panel; its client API key is asked for (not on the command line)
#   --ptero-pin HEX     the SHA-256 of the panel's certificate, for a self-signed one
#                       (python3 init.py fingerprint https://panel… on labhud prints it)
#   --base-w W, --price-kwh P   for the power estimate (see the agent's README)
#   --uninstall         stops and removes the agent, its key and settings (and its Proxmox user)
#
# On Proxmox VE it makes a user labhud@pve with only VM.PowerMgmt, VM.Audit and Sys.Audit, and an
# API token for it, written to /etc/labhud-agent/config (mode 600): the token never leaves the host.
set -eu

VERSION="@AGENT_VERSION@"
SUM_AMD64="@AGENT_SHA256_AMD64@"
SUM_ARM64="@AGENT_SHA256_ARM64@"
RELEASES="@AGENT_RELEASES@"
DIR=/etc/labhud-agent
# The binary in a folder of its own, the only one besides $DIR the service may write (it
# replaces itself on an update); /usr/local/bin has a link to it for the command line.
BINDIR=/usr/local/lib/labhud-agent
BIN=$BINDIR/labhud-agent
LINK=/usr/local/bin/labhud-agent
UNIT=/etc/systemd/system/labhud-agent.service

URL="" CODE="" NAME="" BINARY="" WANT="" PTERO_URL="" PTERO_PIN="" BASE_W="" PRICE="" UNINSTALL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="${2:-}"; shift 2 ;;
    --code) CODE="${2:-}"; shift 2 ;;
    --name) NAME="${2:-}"; shift 2 ;;
    --binary) BINARY="${2:-}"; shift 2 ;;
    --sha256) WANT="${2:-}"; shift 2 ;;
    --ptero-url) PTERO_URL="${2:-}"; shift 2 ;;
    --ptero-pin) PTERO_PIN="${2:-}"; shift 2 ;;
    --base-w) BASE_W="${2:-}"; shift 2 ;;
    --price-kwh) PRICE="${2:-}"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "unknown option: $1 (see the top of this script)" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" = 0 ] || { echo "run it as root (sudo sh -s -- ...)" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd is needed" >&2; exit 1; }

if [ -n "$UNINSTALL" ]; then
  systemctl disable --now labhud-agent 2>/dev/null || true
  rm -f "$UNIT" "$LINK"
  rm -rf "$BINDIR"
  rm -rf "$DIR"
  systemctl daemon-reload
  if command -v pveum >/dev/null && pveum user list --output-format json 2>/dev/null | grep -q '"userid": *"labhud@pve"'; then
    pveum user delete labhud@pve && echo "Proxmox user labhud@pve removed (with its token)."
  fi
  echo "labhud-agent removed. Remove it in labhud's ⚙ > Agents too."
  exit 0
fi

case "$URL" in
  http://*|https://*) URL="${URL%/}" ;;
  *) echo "--url http(s)://<labhud>:<port> is required" >&2; exit 2 ;;
esac
if [ ! -s "$DIR/key" ] && [ -z "$CODE" ]; then
  echo "--code is required: make one in labhud, ⚙ > Agents > Add an agent" >&2; exit 2
fi
case "$CODE" in *[!A-Za-z0-9_-]*) echo "the code has characters a code never has" >&2; exit 2 ;; esac
case "$NAME" in *[!A-Za-z0-9\ ._-]*) echo "--name: letters, digits, space, '.', '_' and '-' only" >&2; exit 2 ;; esac

case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64; PINNED="$SUM_AMD64" ;;
  aarch64|arm64) ARCH=arm64; PINNED="$SUM_ARM64" ;;
  *) echo "no labhud-agent build for $(uname -m)" >&2; exit 1 ;;
esac
case "$PINNED" in @*) PINNED="" ;; esac

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
if [ -n "$BINARY" ]; then
  [ -f "$BINARY" ] || { echo "--binary $BINARY: no such file" >&2; exit 2; }
  cp "$BINARY" "$TMP"
  [ -z "$WANT" ] || PINNED="$WANT"
  [ -n "$PINNED" ] || { echo "labhud pins no checksum for this build: give --sha256 (from where you built it)" >&2; exit 1; }
else
  [ -n "$PINNED" ] || { printf '%s\n' "This labhud pins no release of labhud-agent $VERSION ($ARCH), so there is nothing to download." \
    "Build it (see the labhud-agent README) or copy it here, then run this again with" \
    "  --binary ./labhud-agent --sha256 <the SHA-256 from where you built it>" >&2; exit 1; }
  FROM="$RELEASES/v$VERSION/labhud-agent-linux-$ARCH"
  echo "Downloading labhud-agent $VERSION ($ARCH)…"
  if command -v curl >/dev/null; then curl -fsSL "$FROM" -o "$TMP"; else wget -qO "$TMP" "$FROM"; fi
fi
GOT="$(sha256sum "$TMP" | cut -d' ' -f1)"
[ "$GOT" = "$PINNED" ] || { echo "the agent does not match its checksum (got $GOT): not installed" >&2; exit 1; }

# the old Python agent of the same name (push) keeps running until its card moves over
systemctl stop labhud-agent 2>/dev/null || true
install -d -m 0755 "$BINDIR"
install -m 0755 "$TMP" "$BIN"
ln -sf "$BIN" "$LINK"   # also replaces the binary that was there before
install -d -m 0700 "$DIR"

# settings: kept across runs, only what this run gives is changed
umask 077
CONF="$DIR/config"
touch "$CONF"
chmod 600 "$CONF"
set_conf() {   # set_conf KEY VALUE: replaces the line, or adds it
  grep -v "^$1=" "$CONF" > "$CONF.new" || true
  printf '%s=%s\n' "$1" "$2" >> "$CONF.new"
  mv "$CONF.new" "$CONF"
}
set_conf LABHUD_URL "$URL"
[ -z "$CODE" ] || set_conf LABHUD_AGENT_CODE "$CODE"
[ -z "$NAME" ] || set_conf LABHUD_AGENT_NAME "$NAME"
[ -z "$BASE_W" ] || set_conf LABHUD_AGENT_BASE_W "$BASE_W"
[ -z "$PRICE" ] || set_conf LABHUD_AGENT_PRICE_KWH "$PRICE"
# where the agent's own updates come from: a mirror is written here, by root on this host, never
# sent by labhud (the agent's default is its GitHub releases)
case "$RELEASES" in
  https://github.com/Mithrandler/labhud-agent/releases/download|"") ;;
  https://*) set_conf LABHUD_AGENT_RELEASES "$RELEASES" ;;
esac

if command -v pveum >/dev/null && ! grep -q '^LABHUD_PVE_TOKEN=' "$CONF"; then
  echo "Proxmox VE: making the user labhud@pve and its token (VM.PowerMgmt, VM.Audit, Sys.Audit)…"
  pveum role list --output-format json | grep -q '"roleid": *"LabhudAgent"' ||
    pveum role add LabhudAgent --privs "VM.PowerMgmt,VM.Audit,Sys.Audit"
  pveum user list --output-format json | grep -q '"userid": *"labhud@pve"' ||
    pveum user add labhud@pve --comment "labhud-agent: start, shut down, reboot guests"
  pveum acl modify / --users labhud@pve --roles LabhudAgent
  pveum user token remove labhud@pve agent 2>/dev/null || true
  VALUE="$(pveum user token add labhud@pve agent --privsep 0 --comment labhud-agent --output-format json |
    sed -n 's/.*"value"[[:space:]]*:[[:space:]]*"\([0-9a-f-]*\)".*/\1/p')"
  [ -n "$VALUE" ] || { echo "could not make the Proxmox token" >&2; exit 1; }
  set_conf LABHUD_PVE_TOKEN "labhud@pve!agent=$VALUE"
fi

if [ -n "$PTERO_URL" ]; then
  printf "Pterodactyl client API key (Account > API Credentials; not shown): " >/dev/tty
  stty -echo </dev/tty 2>/dev/null || true
  read -r KEY </dev/tty
  stty echo </dev/tty 2>/dev/null || true
  printf "\n" >/dev/tty
  [ -n "$KEY" ] || { echo "no key given" >&2; exit 1; }
  set_conf LABHUD_PTERO_URL "${PTERO_URL%/}"
  set_conf LABHUD_PTERO_KEY "$KEY"
  [ -z "$PTERO_PIN" ] || set_conf LABHUD_PTERO_PIN "$PTERO_PIN"
fi

cat > "$UNIT" <<EOF
# Written by labhud's install-agent.sh. Settings: $DIR/config. The agent dials $URL.
[Unit]
Description=labhud agent
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$BIN
# Root: it reboots the host, updates packages, talks to Docker. What it may do is decided in
# labhud's ⚙ > Agents and checked again by the agent itself; it never runs a shell.
NoNewPrivileges=yes
ProtectSystem=full
ReadWritePaths=$DIR $BINDIR
ProtectHome=yes
PrivateTmp=yes
Restart=on-failure
RestartSec=5
# 2: pairing refused (a wrong or expired code); 0: removed in labhud. Neither is retried.
RestartPreventExitStatus=2

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable labhud-agent >/dev/null 2>&1
systemctl restart labhud-agent
sleep 3
if systemctl is-active --quiet labhud-agent; then
  # once paired, the code is of no further use
  [ ! -s "$DIR/key" ] || { grep -v '^LABHUD_AGENT_CODE=' "$CONF" > "$CONF.new" || true; mv "$CONF.new" "$CONF"; }
  echo "labhud-agent $("$BIN" -version) is running. In labhud, ⚙ > Agents: tick what it may do."
  echo "Its log: journalctl -u labhud-agent"
else
  echo "labhud-agent did not start:" >&2
  journalctl -u labhud-agent -n 5 --no-pager >&2 || true
  exit 1
fi
