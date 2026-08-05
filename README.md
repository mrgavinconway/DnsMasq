# dnsmasq-web

A small, dependency-free web interface for dnsmasq on a Raspberry Pi. It reads live leases and manages two isolated include files for DHCP reservations and local DNS records. Changes are written atomically, checked with `dnsmasq --test`, and only then reloaded. A failed check restores the previous files.

## Install on Debian Trixie / Raspberry Pi OS

Copy this directory onto the Pi, then run:

```sh
chmod +x install.sh
sudo ./install.sh
```

The installer places the application under `/opt/dnsmasq-web`, starts it immediately, and enables it at every boot. Open `http://<pi-address>/` in a browser. The service listens on port 80 on all interfaces and has no login.

Running the installer again upgrades the existing installation and restarts the service so backend and browser assets remain in sync.

The leases table shows each MAC address's registered manufacturer and vendor names are searchable alongside devices, IPs, and MACs. Lookups use Debian's local `ieee-data` database, installed automatically, so MAC addresses are never sent to an external service. Randomized/private MAC addresses are identified as such.

The Logs tab streams recent and live journal entries from `dnsmasq.service` and `dnsmasq-web.service`. It keeps at most 1,000 lines in the browser and supports searching, pausing, and clearing the view without changing the system journal.

Lease columns can be sorted in either direction by selecting their headings. DHCP reservations support optional comments, stored as encoded metadata comments beside the corresponding dnsmasq entry so the reservation file remains valid.

The Updates panel checks the GitHub `main` branch and can install it directly. Updates run as a separate transient systemd job, allowing the web service to restart safely during its own upgrade.

Restrict port 80 to your trusted LAN. Every device that can reach the page can change dnsmasq settings. Never expose it directly to the internet; use a VPN if remote access is ever needed.

The service manages `/etc/homelan-reservations` in dnsmasq `dhcp-hostsfile` format and `/etc/homelan-hosts` in standard hosts-file format. Configure dnsmasq to load them:

```ini
dhcp-hostsfile=/etc/homelan-reservations
addn-hosts=/etc/homelan-hosts
```

Saves use temporary sibling files under `/etc` so both configuration files can be replaced atomically. The systemd unit grants the service write access to `/etc` for this purpose; the application itself only targets the two configured files.

## Configuration

All settings are optional environment variables: `DNSMASQ_WEB_HOST`, `DNSMASQ_WEB_PORT`, `DNSMASQ_WEB_RESERVATIONS`, `DNSMASQ_WEB_DNS`, `DNSMASQ_WEB_LEASES`, `DNSMASQ_WEB_BINARY`, and `DNSMASQ_WEB_RESTART`.

When run manually, the server defaults to `127.0.0.1:8080`. The installed systemd unit explicitly uses `0.0.0.0:80`.

Useful service commands:

```sh
sudo systemctl status dnsmasq-web
sudo systemctl restart dnsmasq-web
sudo journalctl -u dnsmasq-web -f
```
