#!/usr/bin/env python3
"""dnsmasq-web: a dependency-free dnsmasq editor for small Linux hosts."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
MAC_RE = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
NAME_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*(?<!-)$")
LOCK = threading.Lock()
OUI_LOCK = threading.Lock()
OUI_CACHE: dict[str, str] | None = None


@dataclass
class Settings:
    reservations: Path
    dns: Path
    leases: Path
    dnsmasq: str
    restart: list[str]


def clean(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("All fields must be text")
    value = value.strip()
    if not value or any(c in value for c in "\r\n#,"):
        raise ValueError("Fields cannot be empty or contain commas, #, or newlines")
    return value


def valid_ip(value: object) -> str:
    value = clean(value)
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise ValueError(f"Invalid IP address: {value}") from exc


def valid_name(value: object) -> str:
    value = clean(value).lower()
    if not NAME_RE.fullmatch(value):
        raise ValueError(f"Invalid hostname: {value}")
    return value


def read_reservations(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        value = line.removeprefix("dhcp-host=")
        parts = [p.strip() for p in value.split(",")]
        if len(parts) >= 2:
            ip_index = next((i for i, part in enumerate(parts) if is_ip(part)), None)
            mac_index = next((i for i, part in enumerate(parts) if MAC_RE.fullmatch(part)), None)
            if ip_index is not None and mac_index is not None:
                hostname = next((part for i, part in enumerate(parts)
                                 if i not in (ip_index, mac_index) and NAME_RE.fullmatch(part)), "")
                rows.append({"mac": parts[mac_index].lower(), "ip": parts[ip_index], "hostname": hostname})
    return rows


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def read_dns(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("address=/"):
            parts = line[len("address=/"):].rsplit("/", 1)
            if len(parts) == 2:
                rows.append({"hostname": parts[0].rstrip("/"), "ip": parts[1]})
        else:
            parts = line.split()
            if len(parts) >= 2 and is_ip(parts[0]):
                for hostname in parts[1:]:
                    rows.append({"hostname": hostname, "ip": parts[0]})
    return rows


def read_leases(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    now = int(time.time())
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            expiry = int(parts[0])
        except ValueError:
            continue
        rows.append({"expires": expiry, "remaining": max(0, expiry - now), "mac": parts[1],
                     "ip": parts[2], "hostname": "" if parts[3] == "*" else parts[3], "clientId": parts[4]})
    return sorted(rows, key=lambda row: str(row["ip"]))


def read_number(path: Path, divisor: float = 1) -> float | None:
    try:
        value = path.read_text(encoding="utf-8").strip().split()[0]
        return round(float(value) / divisor, 1)
    except (OSError, ValueError):
        return None


def system_stats() -> dict[str, object]:
    """Read lightweight health data from Linux virtual filesystems."""
    temperature = None
    for path in (Path("/sys/class/thermal/thermal_zone0/temp"),
                 Path("/sys/devices/virtual/thermal/thermal_zone0/temp")):
        temperature = read_number(path, 1000)
        if temperature is not None:
            break

    memory_percent = None
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0])
        total, available = values["MemTotal"], values["MemAvailable"]
        memory_percent = round((total - available) / total * 100, 1)
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        pass

    try:
        load = round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        load = None
    cpu_count = os.cpu_count() or 1
    load_percent = round(load / cpu_count * 100, 1) if load is not None else None
    uptime = read_number(Path("/proc/uptime"))
    try:
        disk = shutil.disk_usage("/")
        disk_percent = round(disk.used / disk.total * 100, 1)
    except (OSError, ZeroDivisionError):
        disk_percent = None
    return {"hostname": socket.gethostname(), "temperature": temperature, "load": load,
            "loadPercent": load_percent, "cpuCount": cpu_count, "memoryPercent": memory_percent,
            "diskPercent": disk_percent, "uptime": uptime}


def oui_database() -> dict[str, str]:
    global OUI_CACHE
    if OUI_CACHE is not None:
        return OUI_CACHE
    with OUI_LOCK:
        if OUI_CACHE is not None:
            return OUI_CACHE
        database: dict[str, str] = {}
        candidates = (Path("/usr/share/ieee-data/oui.txt"), Path("/var/lib/ieee-data/oui.txt"),
                      Path("/usr/share/arp-scan/ieee-oui.txt"))
        source = next((path for path in candidates if path.exists()), None)
        if source:
            for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
                match = re.match(r"^([0-9A-Fa-f]{2})[-:]?([0-9A-Fa-f]{2})[-:]?([0-9A-Fa-f]{2})\s+\(hex\)\s+(.+)$", line)
                if match:
                    database["".join(match.group(i).upper() for i in range(1, 4))] = match.group(4).strip()
        OUI_CACHE = database
        return database


def mac_vendor(mac: str) -> dict[str, str | bool]:
    if not MAC_RE.fullmatch(mac):
        raise ValueError("Invalid MAC address")
    normalized = mac.lower()
    if int(normalized[:2], 16) & 2:
        return {"mac": normalized, "vendor": "Private or randomized address", "private": True}
    prefix = normalized.replace(":", "")[:6].upper()
    vendor = oui_database().get(prefix)
    return {"mac": normalized, "vendor": vendor or "Vendor not found", "private": False}


def leases_with_vendors(path: Path) -> list[dict[str, object]]:
    leases = read_leases(path)
    for lease in leases:
        lease["vendor"] = mac_vendor(str(lease["mac"]))["vendor"]
    return leases


def render_reservations(rows: list[dict[str, object]]) -> str:
    seen_mac, seen_ip = set(), set()
    lines = ["# Managed by dnsmasq-web. Manual changes may be overwritten."]
    for row in rows:
        mac = clean(row.get("mac", "")).lower()
        if not MAC_RE.fullmatch(mac):
            raise ValueError(f"Invalid MAC address: {mac}")
        ip, hostname = valid_ip(row.get("ip", "")), valid_name(row.get("hostname", ""))
        if mac in seen_mac or ip in seen_ip:
            raise ValueError("MAC and IP addresses must be unique")
        seen_mac.add(mac); seen_ip.add(ip)
        lines.append(f"{mac},{hostname},{ip}")
    return "\n".join(lines) + "\n"


def render_dns(rows: list[dict[str, object]]) -> str:
    seen = set()
    lines = ["# Managed by dnsmasq-web. Manual changes may be overwritten."]
    for row in rows:
        hostname, ip = valid_name(row.get("hostname", "")), valid_ip(row.get("ip", ""))
        if hostname in seen:
            raise ValueError("DNS hostnames must be unique")
        seen.add(hostname)
        lines.append(f"{ip}\t{hostname}")
    return "\n".join(lines) + "\n"


def atomic_apply(settings: Settings, reservations: str, dns: str) -> None:
    settings.reservations.parent.mkdir(parents=True, exist_ok=True)
    backups: dict[Path, bytes | None] = {}
    temps: list[Path] = []
    try:
        for target, content in ((settings.reservations, reservations), (settings.dns, dns)):
            backups[target] = target.read_bytes() if target.exists() else None
            fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush(); os.fsync(handle.fileno())
            temp = Path(name); temps.append(temp)
            os.chmod(temp, 0o644)
            os.replace(temp, target)
        result = subprocess.run([settings.dnsmasq, "--test"], text=True, capture_output=True, timeout=10)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "dnsmasq validation failed").strip())
        subprocess.run(settings.restart, check=True, text=True, capture_output=True, timeout=15)
    except Exception:
        for target, content in backups.items():
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(content)
        raise
    finally:
        for temp in temps:
            temp.unlink(missing_ok=True)


class Handler(SimpleHTTPRequestHandler):
    settings: Settings

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def end_headers(self) -> None:
        # The UI is updated in place on the Pi; stale assets can otherwise call
        # an older API after an upgrade.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def reply(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self.reply(200, {"reservations": read_reservations(self.settings.reservations),
                             "dns": read_dns(self.settings.dns),
                             "leases": leases_with_vendors(self.settings.leases),
                             "system": system_stats()})
            return
        super().do_GET()

    def do_PUT(self) -> None:
        if urlparse(self.path).path != "/api/config":
            self.reply(404, {"error": "Not found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000: raise ValueError("Request is too large")
            body = json.loads(self.rfile.read(length))
            reservations = render_reservations(body.get("reservations", []))
            dns = render_dns(body.get("dns", []))
            with LOCK: atomic_apply(self.settings, reservations, dns)
            self.reply(200, {"ok": True})
        except (ValueError, json.JSONDecodeError) as exc:
            self.reply(400, {"error": str(exc)})
        except Exception as exc:
            self.reply(500, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Tiny dnsmasq web UI")
    parser.add_argument("--host", default=os.getenv("DNSMASQ_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DNSMASQ_WEB_PORT", "8080")))
    args = parser.parse_args()
    settings = Settings(Path(os.getenv("DNSMASQ_WEB_RESERVATIONS", "/etc/homelan-reservations")),
                        Path(os.getenv("DNSMASQ_WEB_DNS", "/etc/homelan-hosts")),
                        Path(os.getenv("DNSMASQ_WEB_LEASES", "/var/lib/misc/dnsmasq.leases")),
                        os.getenv("DNSMASQ_WEB_BINARY", shutil.which("dnsmasq") or "/usr/sbin/dnsmasq"),
                        os.getenv("DNSMASQ_WEB_RESTART", "systemctl reload-or-restart dnsmasq").split())
    Handler.settings = settings
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"dnsmasq-web listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
