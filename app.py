#!/usr/bin/env python3
"""dnsmasq-web: a dependency-free dnsmasq editor for small Linux hosts."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
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

    def reply(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(data)

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/api/state":
            self.reply(200, {"reservations": read_reservations(self.settings.reservations),
                             "dns": read_dns(self.settings.dns),
                             "leases": read_leases(self.settings.leases)})
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
