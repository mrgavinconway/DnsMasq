#!/bin/sh
set -eu

APP_DIR=/opt/dnsmasq-web
UNIT=/etc/systemd/system/dnsmasq-web.service

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root: sudo ./install.sh" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required (it is included in standard Debian installations)." >&2
    exit 1
fi

if ! command -v dnsmasq >/dev/null 2>&1; then
    echo "dnsmasq is not installed. Run: sudo apt install dnsmasq" >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

install -d -m 0755 "$APP_DIR" "$APP_DIR/web"
install -m 0755 "$SCRIPT_DIR/app.py" "$APP_DIR/app.py"
install -m 0644 "$SCRIPT_DIR/web/index.html" "$APP_DIR/web/index.html"
install -m 0644 "$SCRIPT_DIR/web/style.css" "$APP_DIR/web/style.css"
install -m 0644 "$SCRIPT_DIR/web/app.js" "$APP_DIR/web/app.js"
install -m 0644 "$SCRIPT_DIR/dnsmasq-web.service" "$UNIT"

systemctl daemon-reload
systemctl enable --now dnsmasq-web.service

if systemctl is-active --quiet dnsmasq-web.service; then
    address=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo "dnsmasq-web is running and enabled at boot."
    echo "Open: http://${address:-your-pi}/"
else
    echo "The service did not start. Recent log output:" >&2
    journalctl -u dnsmasq-web.service -n 20 --no-pager >&2
    exit 1
fi
