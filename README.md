# dnsmasq-web

A small, dependency-free web interface for dnsmasq on a Raspberry Pi. It reads live leases and manages two isolated include files for DHCP reservations and local DNS records. Changes are written atomically, checked with `dnsmasq --test`, and only then reloaded. A failed check restores the previous files.

## Install on Debian Trixie / Raspberry Pi OS

Copy this directory onto the Pi, then run:

```sh
chmod +x install.sh
sudo ./install.sh
```

The installer places the application under `/opt/dnsmasq-web`, starts it immediately, and enables it at every boot. Open `http://<pi-address>/` in a browser. The service listens on port 80 on all interfaces and has no login.

Restrict port 80 to your trusted LAN. Every device that can reach the page can change dnsmasq settings. Never expose it directly to the internet; use a VPN if remote access is ever needed.

The service manages `/etc/dnsmasq.d/web-reservations.conf` and `/etc/dnsmasq.d/web-dns.conf`. Existing dnsmasq files are left untouched. Ensure your main dnsmasq configuration loads `/etc/dnsmasq.d/*.conf` (the Raspberry Pi OS package does by default).

## Configuration

All settings are optional environment variables: `DNSMASQ_WEB_HOST`, `DNSMASQ_WEB_PORT`, `DNSMASQ_WEB_RESERVATIONS`, `DNSMASQ_WEB_DNS`, `DNSMASQ_WEB_LEASES`, `DNSMASQ_WEB_BINARY`, and `DNSMASQ_WEB_RESTART`.

When run manually, the server defaults to `127.0.0.1:8080`. The installed systemd unit explicitly uses `0.0.0.0:80`.

Useful service commands:

```sh
sudo systemctl status dnsmasq-web
sudo systemctl restart dnsmasq-web
sudo journalctl -u dnsmasq-web -f
```
