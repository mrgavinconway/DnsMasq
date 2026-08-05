#!/usr/bin/env python3
"""dnsmasq-web: a dependency-free dnsmasq editor for small Linux hosts."""

from __future__ import annotations

import argparse
import base64
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
from datetime import datetime
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
UPDATE_LOCK = threading.Lock()
UPDATE_STATUS = Path("/run/dnsmasq-web/update.json")
REPOSITORY = os.getenv("DNSMASQ_WEB_REPOSITORY", "https://github.com/mrgavinconway/DnsMasq.git")


@dataclass
class Settings:
    reservations: Path
    dns: Path
    tuning: Path
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
    pending_comment = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("# dnsmasq-web-note: "):
            try:
                encoded = line.removeprefix("# dnsmasq-web-note: ")
                pending_comment = base64.urlsafe_b64decode(encoded.encode()).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                pending_comment = ""
            continue
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
                rows.append({"mac": parts[mac_index].lower(), "ip": parts[ip_index], "hostname": hostname,
                             "comment": pending_comment})
                pending_comment = ""
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


def journal_command(cursor: str = "") -> list[str]:
    journalctl = shutil.which("journalctl") or "/usr/bin/journalctl"
    command = [journalctl, "--unit=dnsmasq.service", "--unit=dnsmasq-web.service",
               "--unit=dnsmasq-web-update*.service",
               "--follow", "--output=json", "--no-pager"]
    command.append(f"--after-cursor={cursor}" if cursor else "--lines=150")
    return command


def format_journal_entry(record: dict[str, object]) -> str:
    raw_time = str(record.get("__REALTIME_TIMESTAMP", "0"))
    try:
        timestamp = datetime.fromtimestamp(int(raw_time) / 1_000_000).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        timestamp = "---- -- --:--:--"
    unit = str(record.get("_SYSTEMD_UNIT") or record.get("SYSLOG_IDENTIFIER") or "system")
    message = str(record.get("MESSAGE", ""))
    return f"{timestamp}  {unit:<22} {message}"


def read_update_status() -> dict[str, object]:
    try:
        return json.loads(UPDATE_STATUS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "idle", "message": "No update has been run yet"}


def update_info(check_remote: bool = True) -> dict[str, object]:
    try:
        current = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        current = "development"
    status = read_update_status()
    if status.get("state") == "updating":
        check_remote = False
    latest = None
    error = None
    if check_remote:
        try:
            result = subprocess.run([shutil.which("git") or "/usr/bin/git", "ls-remote", REPOSITORY,
                                     "refs/heads/main"], capture_output=True, text=True, timeout=12)
            if result.returncode:
                raise RuntimeError((result.stderr or "GitHub check failed").strip())
            latest = result.stdout.split()[0]
        except (OSError, subprocess.TimeoutExpired, RuntimeError, IndexError) as exc:
            error = str(exc)
    return {"current": current, "latest": latest, "updateAvailable": bool(latest and not latest.startswith(current)),
            "error": error, "job": status}


def start_update() -> str:
    with UPDATE_LOCK:
        status = read_update_status()
        if status.get("state") == "updating":
            raise ValueError("An update is already running")
        UPDATE_STATUS.write_text(json.dumps({"state": "updating", "message": "Starting update…"}), encoding="utf-8")
        unit = f"dnsmasq-web-update-{int(time.time())}"
        result = subprocess.run([shutil.which("systemd-run") or "/usr/bin/systemd-run", f"--unit={unit}",
                                 "--collect", "/opt/dnsmasq-web/update.sh"], capture_output=True, text=True, timeout=10)
        if result.returncode:
            UPDATE_STATUS.write_text(json.dumps({"state": "failed", "message": result.stderr.strip()}), encoding="utf-8")
            raise RuntimeError(result.stderr.strip() or "Could not start update")
        return unit


def render_reservations(rows: list[dict[str, object]]) -> str:
    seen_mac, seen_ip = set(), set()
    lines = ["# Managed by dnsmasq-web. Manual changes may be overwritten."]
    for row in rows:
        mac = clean(row.get("mac", "")).lower()
        if not MAC_RE.fullmatch(mac):
            raise ValueError(f"Invalid MAC address: {mac}")
        ip, hostname = valid_ip(row.get("ip", "")), valid_name(row.get("hostname", ""))
        comment = row.get("comment", "")
        if not isinstance(comment, str) or any(c in comment for c in "\r\n") or len(comment) > 500:
            raise ValueError("Reservation comments must be text under 500 characters without newlines")
        comment = comment.strip()
        if mac in seen_mac or ip in seen_ip:
            raise ValueError("MAC and IP addresses must be unique")
        seen_mac.add(mac); seen_ip.add(ip)
        if comment:
            encoded = base64.urlsafe_b64encode(comment.encode("utf-8")).decode()
            lines.append(f"# dnsmasq-web-note: {encoded}")
        lines.append(f"{mac},{hostname},{ip}")
    return "\n".join(lines) + "\n"


def render_dns(rows: list[dict[str, object]]) -> str:
    lines = ["# Managed by dnsmasq-web. Manual changes may be overwritten."]
    for row in rows:
        hostname, ip = valid_name(row.get("hostname", "")), valid_ip(row.get("ip", ""))
        lines.append(f"{ip}\t{hostname}")
    return "\n".join(lines) + "\n"


def read_tuning(path: Path) -> dict[str, object]:
    values: dict[str, object] = {"cacheSize": 150, "clearOnReload": False, "domainNeeded": False,
                                 "bogusPriv": False, "stopDnsRebind": False, "upstreamMode": "automatic",
                                 "upstreamServers": [], "rebindExceptions": []}
    if not path.exists():
        return values
    servers, exceptions = [], []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("cache-size="):
            try: values["cacheSize"] = int(line.split("=", 1)[1])
            except ValueError: pass
        elif line == "clear-on-reload": values["clearOnReload"] = True
        elif line == "domain-needed": values["domainNeeded"] = True
        elif line == "bogus-priv": values["bogusPriv"] = True
        elif line == "stop-dns-rebind": values["stopDnsRebind"] = True
        elif line == "no-resolv": values["upstreamMode"] = "custom"
        elif line.startswith("server="): servers.append(line.split("=", 1)[1])
        elif line.startswith("rebind-domain-ok="):
            exceptions.extend(part for part in line.split("=", 1)[1].strip("/").split("/") if part)
    values["upstreamServers"], values["rebindExceptions"] = servers, exceptions
    return values


def render_tuning(values: dict[str, object]) -> str:
    try: cache_size = int(values.get("cacheSize", 150))
    except (TypeError, ValueError): raise ValueError("Cache size must be a number")
    if not 0 <= cache_size <= 10000:
        raise ValueError("Cache size must be between 0 and 10,000")
    lines = ["# Managed by dnsmasq-web. Manual changes may be overwritten.", f"cache-size={cache_size}"]
    for key, option in (("clearOnReload", "clear-on-reload"), ("domainNeeded", "domain-needed"),
                        ("bogusPriv", "bogus-priv"), ("stopDnsRebind", "stop-dns-rebind")):
        if values.get(key) is True: lines.append(option)
    mode = values.get("upstreamMode", "automatic")
    if mode not in ("automatic", "custom"): raise ValueError("Invalid upstream DNS mode")
    servers = values.get("upstreamServers", [])
    if not isinstance(servers, list): raise ValueError("Upstream servers must be a list")
    if mode == "custom":
        if not servers: raise ValueError("Add at least one upstream DNS server")
        lines.append("no-resolv")
        for server in servers: lines.append(f"server={valid_ip(server)}")
    exceptions = values.get("rebindExceptions", [])
    if not isinstance(exceptions, list): raise ValueError("Rebind exceptions must be a list")
    cleaned = [valid_name(item) for item in exceptions if str(item).strip()]
    if cleaned: lines.append(f"rebind-domain-ok=/{'/'.join(cleaned)}/")
    return "\n".join(lines) + "\n"


def atomic_tuning_apply(settings: Settings, content: str) -> None:
    target = settings.tuning
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.read_bytes() if target.exists() else None
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.chmod(temp, 0o644); os.replace(temp, target)
        result = subprocess.run([settings.dnsmasq, "--test"], text=True, capture_output=True, timeout=10)
        if result.returncode: raise RuntimeError((result.stderr or result.stdout or "dnsmasq validation failed").strip())
        subprocess.run(["systemctl", "restart", "dnsmasq"], check=True, text=True, capture_output=True, timeout=15)
    except Exception:
        if backup is None: target.unlink(missing_ok=True)
        else: target.write_bytes(backup)
        subprocess.run(["systemctl", "restart", "dnsmasq"], text=True, capture_output=True, timeout=15)
        raise
    finally:
        temp.unlink(missing_ok=True)


def dnsmasq_action(settings: Settings, action: str) -> str:
    if action == "validate":
        result = subprocess.run([settings.dnsmasq, "--test"], text=True, capture_output=True, timeout=10)
        if result.returncode: raise RuntimeError((result.stderr or result.stdout).strip())
        return (result.stderr or result.stdout or "Configuration is valid").strip()
    if action in ("clear-cache", "cache-stats"):
        signal = "HUP" if action == "clear-cache" else "USR1"
        subprocess.run(["systemctl", "kill", f"--signal={signal}", "dnsmasq"], check=True,
                       text=True, capture_output=True, timeout=10)
        return "DNS cache cleared and local files reloaded" if action == "clear-cache" else "Cache statistics written to the dnsmasq log"
    if action == "restart":
        subprocess.run(["systemctl", "restart", "dnsmasq"], check=True, text=True, capture_output=True, timeout=15)
        return "dnsmasq restarted successfully"
    raise ValueError("Unknown dnsmasq action")


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
    protocol_version = "HTTP/1.1"

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

    def stream_logs(self) -> None:
        cursor = self.headers.get("Last-Event-ID", "")
        command = journal_command(cursor)
        process = None
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", bufsize=1)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b": connected\n\n"); self.wfile.flush()
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    record = json.loads(line)
                    event_id = str(record.get("__CURSOR", "")).replace("\n", "")
                    payload = f"id: {event_id}\ndata: {json.dumps(format_journal_entry(record))}\n\n".encode("utf-8")
                except json.JSONDecodeError:
                    payload = f"data: {json.dumps(line.rstrip())}\n\n".encode("utf-8")
                self.wfile.write(payload); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError as exc:
            if process is None:
                self.reply(500, {"error": str(exc)})
        finally:
            if process is not None and process.poll() is None:
                process.terminate()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/logs":
            self.stream_logs()
            return
        if parsed.path == "/api/update":
            self.reply(200, update_info())
            return
        if parsed.path == "/api/settings":
            self.reply(200, read_tuning(self.settings.tuning))
            return
        if parsed.path == "/api/state":
            self.reply(200, {"reservations": read_reservations(self.settings.reservations),
                             "dns": read_dns(self.settings.dns),
                             "leases": leases_with_vendors(self.settings.leases),
                             "system": system_stats()})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/action":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(min(length, 10_000)))
                self.reply(200, {"ok": True, "message": dnsmasq_action(self.settings, body.get("action", ""))})
            except (ValueError, json.JSONDecodeError) as exc: self.reply(400, {"error": str(exc)})
            except Exception as exc: self.reply(500, {"error": str(exc)})
            return
        if path != "/api/update":
            self.reply(404, {"error": "Not found"}); return
        try:
            self.reply(202, {"ok": True, "unit": start_update()})
        except ValueError as exc:
            self.reply(409, {"error": str(exc)})
        except Exception as exc:
            self.reply(500, {"error": str(exc)})

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/settings":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 100_000: raise ValueError("Request is too large")
                body = json.loads(self.rfile.read(length)); content = render_tuning(body)
                with LOCK: atomic_tuning_apply(self.settings, content)
                self.reply(200, {"ok": True})
            except (ValueError, json.JSONDecodeError) as exc: self.reply(400, {"error": str(exc)})
            except Exception as exc: self.reply(500, {"error": str(exc)})
            return
        if path != "/api/config":
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
                        Path(os.getenv("DNSMASQ_WEB_SETTINGS", "/etc/dnsmasq.d/web-settings.conf")),
                        Path(os.getenv("DNSMASQ_WEB_LEASES", "/var/lib/misc/dnsmasq.leases")),
                        os.getenv("DNSMASQ_WEB_BINARY", shutil.which("dnsmasq") or "/usr/sbin/dnsmasq"),
                        os.getenv("DNSMASQ_WEB_RESTART", "systemctl reload-or-restart dnsmasq").split())
    Handler.settings = settings
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"dnsmasq-web listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
