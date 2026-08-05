#!/bin/sh
set -eu

STATUS=/run/dnsmasq-web-update.json
REPOSITORY=${DNSMASQ_WEB_REPOSITORY:-https://github.com/mrgavinconway/DnsMasq.git}
WORK_DIR=$(mktemp -d /tmp/dnsmasq-web-update.XXXXXX)
finished=0

write_status() {
    state=$1
    message=$2
    python3 -c 'import json,sys; print(json.dumps({"state":sys.argv[1],"message":sys.argv[2]}))' "$state" "$message" > "$STATUS"
}

cleanup() {
    if [ "$finished" -ne 1 ]; then
        write_status failed "Update failed; check the Logs page"
    fi
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

write_status updating "Downloading latest release..."
git clone --quiet --depth 1 --branch main "$REPOSITORY" "$WORK_DIR/repo"
write_status updating "Installing update..."
chmod +x "$WORK_DIR/repo/install.sh"
"$WORK_DIR/repo/install.sh"
version=$(git -C "$WORK_DIR/repo" rev-parse --short HEAD)
write_status complete "Updated successfully to $version"
finished=1
