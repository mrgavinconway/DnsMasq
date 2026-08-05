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

if ! command -v git >/dev/null 2>&1; then
    echo "Installing git for in-app updates..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y git
fi

if ! dpkg-query -W -f='${Status}' ieee-data 2>/dev/null | grep -q "install ok installed"; then
    echo "Installing the local IEEE MAC vendor database..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y ieee-data
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

install -d -m 0755 "$APP_DIR" "$APP_DIR/web"
install -m 0755 "$SCRIPT_DIR/app.py" "$APP_DIR/app.py"
install -m 0755 "$SCRIPT_DIR/update.sh" "$APP_DIR/update.sh"
install -m 0644 "$SCRIPT_DIR/web/index.html" "$APP_DIR/web/index.html"
install -m 0644 "$SCRIPT_DIR/web/style.css" "$APP_DIR/web/style.css"
install -m 0644 "$SCRIPT_DIR/web/vendor.css" "$APP_DIR/web/vendor.css"
install -m 0644 "$SCRIPT_DIR/web/logs.css" "$APP_DIR/web/logs.css"
install -m 0644 "$SCRIPT_DIR/web/enhancements.css" "$APP_DIR/web/enhancements.css"
install -m 0644 "$SCRIPT_DIR/web/log-filter.css" "$APP_DIR/web/log-filter.css"
install -m 0644 "$SCRIPT_DIR/web/review.css" "$APP_DIR/web/review.css"
install -m 0644 "$SCRIPT_DIR/web/editor-tables.css" "$APP_DIR/web/editor-tables.css"
install -m 0644 "$SCRIPT_DIR/web/system.css" "$APP_DIR/web/system.css"
install -m 0644 "$SCRIPT_DIR/web/app.js" "$APP_DIR/web/app.js"
install -m 0644 "$SCRIPT_DIR/dnsmasq-web.service" "$UNIT"
if git -C "$SCRIPT_DIR" rev-parse HEAD >/dev/null 2>&1; then
    git -C "$SCRIPT_DIR" rev-parse HEAD > "$APP_DIR/VERSION"
else
    printf "development\n" > "$APP_DIR/VERSION"
fi

systemctl daemon-reload
systemctl enable dnsmasq-web.service
systemctl restart dnsmasq-web.service

if systemctl is-active --quiet dnsmasq-web.service; then
    address=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo "dnsmasq-web is running and enabled at boot."
    echo "Open: http://${address:-your-pi}/"
else
    echo "The service did not start. Recent log output:" >&2
    journalctl -u dnsmasq-web.service -n 20 --no-pager >&2
    exit 1
fi
