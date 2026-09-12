#!/usr/bin/env bash
# Runs ON THE PI as root — invoked by deploy.sh / deploy.ps1 via
#   ssh -t <pi> "sudo bash /opt/lares-sense/incoming/deploy/on-pi-install.sh ..."
# The deploy tarball has already been extracted to $DEST/incoming by then.
# Idempotent: safe to run on every deploy.
set -euo pipefail

DEST=/opt/lares-sense
UDP_HOST="${1:-192.168.0.192}"
UDP_PORT="${2:-5005}"

# -- 1. Swap in the new tree ---------------------------------------------
# Everything from the tarball sits in incoming/; the current tree moves to
# .previous/ (one generation kept for instant manual rollback:
#   sudo bash -c 'cd /opt/lares-sense && find . -mindepth 1 -maxdepth 1 \
#     ! -name .venv ! -name .previous ! -name incoming -exec rm -r {} + && \
#     find .previous -mindepth 1 -maxdepth 1 -exec mv -t . {} +'
# ). The venv survives every deploy.
cd "$DEST"
mkdir -p .previous
find .previous -mindepth 1 -delete
find . -mindepth 1 -maxdepth 1 \
    ! -name .venv ! -name .previous ! -name incoming -exec mv -t .previous/ {} +
find incoming -mindepth 1 -maxdepth 1 -exec mv -t . {} +
rmdir incoming

# -- 2. Kill pre-systemd leftovers from the ~/lares-sense era -------------
pkill -f '/home/darky/lares-sense' 2>/dev/null || true
pkill -f 'nohup.*setup_tool'       2>/dev/null || true

# -- 3. venv + deps (as darky, not root) ----------------------------------
[ -x .venv/bin/python ] || sudo -u darky python3 -m venv .venv
sudo -u darky .venv/bin/pip install -q -r requirements.txt

# -- 4. systemd unit (copied fresh each deploy) ---------------------------
cp "$DEST/deploy/lares-setup-tool.service" /etc/systemd/system/

# -- 5. Env file: the gear's own config — create once, never clobber ------
if [ ! -f /etc/default/lares-setup-tool ]; then
    printf 'LARES_UDP_HOST=%s\nLARES_UDP_PORT=%s\n' "$UDP_HOST" "$UDP_PORT" \
        > /etc/default/lares-setup-tool
    echo "created /etc/default/lares-setup-tool"
fi

# -- 6. One-time migration of the Working Config into the StateDirectory --
if [ -f /home/darky/.local/share/lares/setup_tool/working_config.cfg ] \
   && [ ! -f /var/lib/lares/setup_tool/working_config.cfg ]; then
    mkdir -p /var/lib/lares/setup_tool
    cp /home/darky/.local/share/lares/setup_tool/working_config.cfg \
       /var/lib/lares/setup_tool/
    chown -R darky:darky /var/lib/lares
    echo "migrated Working Config to /var/lib/lares/setup_tool"
fi

# -- 7. Restart + report ---------------------------------------------------
systemctl daemon-reload
systemctl enable lares-setup-tool >/dev/null
systemctl restart lares-setup-tool
sleep 3
journalctl -u lares-setup-tool -n 10 --no-pager
systemctl is-active lares-setup-tool
