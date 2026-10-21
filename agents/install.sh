#!/bin/sh
# Installs labhud-agent as a systemd service that PUSHES this machine's readings to labhud
# (CPU, RAM, disk, load, uptime, temperatures, GPUs). It opens no port. Served by labhud itself:
#
#   curl -fsSL http://<labhud>:8095/agent/install.sh | sudo sh -s -- <name> http://<labhud>:8095
#
# <name> is the one given to `init.py agent <name>` on labhud's side, which printed this line and
# the key. The key is asked for here (not on the command line, so it stays out of the shell
# history) and stored in /etc/labhud-agent/push-key, readable by root only; systemd hands it to the
# service, which runs as a throwaway user (DynamicUser) with a read-only system.
#
# Over plain HTTP anyone in between could change this script: run it on a network you trust, or
# serve labhud over HTTPS (LABHUD_TLS_CERT). Uninstall: systemctl disable --now labhud-agent &&
# rm -r /opt/labhud-agent /etc/labhud-agent /etc/systemd/system/labhud-agent.service
set -eu

NAME="${1:-}"
LABHUD="${2:-}"
SHA256="@AGENT_SHA256@"   # filled in by labhud when it serves this script

case "$NAME" in
  ""|*[!a-z0-9_]*) echo "usage: install.sh <name: a-z 0-9 _> <labhud URL>" >&2; exit 2 ;;
esac
case "$LABHUD" in
  http://*|https://*) LABHUD="${LABHUD%/}" ;;
  *) echo "usage: install.sh <name> <labhud URL, http(s)://host:port>" >&2; exit 2 ;;
esac
[ "$(id -u)" = 0 ] || { echo "run it as root (sudo sh -s -- ...)" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is needed (3.11 or newer)" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd is needed" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "python3 is older than 3.11" >&2; exit 1; }

mkdir -p /opt/labhud-agent /etc/labhud-agent
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
if command -v curl >/dev/null; then curl -fsSL "$LABHUD/agent/labhud-agent.py" -o "$TMP"
else wget -qO "$TMP" "$LABHUD/agent/labhud-agent.py"; fi
GOT="$(sha256sum "$TMP" | cut -d' ' -f1)"
[ "$GOT" = "$SHA256" ] || { echo "the agent does not match its checksum ($GOT): not installed" >&2; exit 1; }
install -m 0755 "$TMP" /opt/labhud-agent/labhud-agent.py

if [ ! -s /etc/labhud-agent/push-key ]; then
  printf "Key for %s (from init.py agent; not shown): " "$NAME" >/dev/tty
  stty -echo </dev/tty 2>/dev/null || true
  read -r KEY </dev/tty
  stty echo </dev/tty 2>/dev/null || true
  printf "\n" >/dev/tty
  [ -n "$KEY" ] || { echo "no key given" >&2; exit 1; }
  umask 077
  printf "%s\n" "$KEY" > /etc/labhud-agent/push-key
fi
chmod 600 /etc/labhud-agent/push-key

cat > /etc/systemd/system/labhud-agent.service <<UNIT
# Written by labhud's install.sh. Pushes this machine's readings to $LABHUD as "$NAME".
[Unit]
Description=labhud agent (pushes readings to labhud)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/env python3 /opt/labhud-agent/labhud-agent.py
Environment=LABHUD_AGENT_PORT=0
Environment=LABHUD_AGENT_PUSH_URL=$LABHUD/api/push/$NAME
LoadCredential=push-key:/etc/labhud-agent/push-key
Environment=LABHUD_AGENT_PUSH_KEY_FILE=%d/push-key
DynamicUser=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now labhud-agent >/dev/null
systemctl restart labhud-agent
sleep 2
if systemctl is-active --quiet labhud-agent; then
  echo "labhud-agent is running and pushes to $LABHUD as \"$NAME\"."
  echo "Its card turns live within a few seconds; journalctl -u labhud-agent shows any error."
else
  echo "labhud-agent did not start: journalctl -u labhud-agent" >&2
  exit 1
fi
