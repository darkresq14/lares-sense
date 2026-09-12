#!/usr/bin/env bash
# One-command deploy from a dev machine with bash (Git Bash on Windows works).
# PowerShell users: run deploy.ps1 instead — same thing.
#
#   ./deploy.sh [ssh-host]          # default host: urad-pi
#
# Packs the source tree, ships it to the Pi, and lets
# deploy/on-pi-install.sh (running as root on the Pi) swap the tree in,
# install/refresh the systemd unit, and restart the service.
# First run also creates /opt/lares-sense, the venv, /etc/default/lares-setup-tool,
# and migrates an existing Working Config from the old ~/lares-sense install.
set -euo pipefail

HOST="${1:-urad-pi}"
DEST=/opt/lares-sense
# Recording server for the Running Stage UDP fan-out.
# Only used on first run, when /etc/default/lares-setup-tool is created.
UDP_HOST="${LARES_UDP_HOST:-192.168.0.192}"
UDP_PORT="${LARES_UDP_PORT:-5005}"
TGZ="$(mktemp -d)/lares-deploy.tgz"

echo "==> [1/4] Packing source tree"
tar -czf "$TGZ" \
    --exclude=.git --exclude=.venv --exclude=.pytest_cache \
    --exclude=__pycache__ --exclude='*.pyc' \
    --exclude=docs_and_tools --exclude=.env \
    .

echo "==> [2/4] Uploading to $HOST"
scp -q "$TGZ" "$HOST:/tmp/lares-deploy.tgz"
rm -rf "$(dirname "$TGZ")"

echo "==> [3/4] Preparing target dir (sudo prompt only on first run)"
ssh -t "$HOST" "test -d $DEST && test -O $DEST || sudo mkdir -p $DEST && sudo chown darky:darky $DEST"
ssh "$HOST" "mkdir -p $DEST/incoming && tar -xzf /tmp/lares-deploy.tgz -C $DEST/incoming && rm /tmp/lares-deploy.tgz"

echo "==> [4/4] Install + restart on the Pi (sudo password may be asked)"
ssh -t "$HOST" "sudo bash $DEST/incoming/deploy/on-pi-install.sh '$UDP_HOST' '$UDP_PORT'"

echo "==> Done. Setup Tool: http://$HOST.local:8000/"
