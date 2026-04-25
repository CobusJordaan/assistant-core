"""System monitoring: CPU, RAM, disk, GPU, database, uptime."""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from admin.security import run_command


def get_cpu_info() -> dict:
    """CPU usage, core count, frequency, load average."""
    freq = psutil.cpu_freq()
    try:
        load = os.getloadavg()
        load_avg = [round(x, 2) for x in load]
    except (OSError, AttributeError):
        load_avg = None
    return {
        "percent": psutil.cpu_percent(interval=0.5),
        "count": psutil.cpu_count(),
        "freq": round(freq.current) if freq else None,
        "load_avg": load_avg,
    }


def get_cpu_temp() -> float | None:
    """CPU temperature via psutil, or None if unavailable."""
    try:
        temps = psutil.sensors_temperatures()
        if not temps:
            return None
        # Try common sensor names
        for name in ("coretemp", "k10temp", "cpu_thermal", "cpu-thermal"):
            if name in temps and temps[name]:
                return round(temps[name][0].current, 1)
        # Fallback: first sensor found
        for entries in temps.values():
            if entries:
                return round(entries[0].current, 1)
    except (AttributeError, Exception):
        pass
    return None


def get_memory_info() -> dict:
    """RAM usage."""
    mem = psutil.virtual_memory()
    return {
        "total_gb": round(mem.total / (1024**3), 1),
        "used_gb": round(mem.used / (1024**3), 1),
        "percent": mem.percent,
    }


def get_disk_info() -> list[dict]:
    """Disk usage for key mount points. Includes warning/critical flags."""
    mounts = ["/"]
    for extra in ["/opt/ai-data", "/opt/ai-assistant"]:
        if os.path.isdir(extra):
            mounts.append(extra)

    disks = []
    seen_devices = set()
    for mount in mounts:
        try:
            usage = psutil.disk_usage(mount)
            # Avoid duplicate entries for same device
            parts = [p for p in psutil.disk_partitions() if p.mountpoint == mount]
            device = parts[0].device if parts else mount
            if device in seen_devices and mount != "/":
                continue
            seen_devices.add(device)
            percent = usage.percent
            disks.append({
                "mount": mount,
                "total_gb": round(usage.total / (1024**3), 1),
                "used_gb": round(usage.used / (1024**3), 1),
                "percent": percent,
                "warning": percent > 85,
                "critical": percent > 95,
            })
        except (OSError, Exception):
            pass
    return disks


def get_uptime() -> str:
    """System uptime as a formatted string."""
    boot = psutil.boot_time()
    delta = time.time() - boot
    days = int(delta // 86400)
    hours = int((delta % 86400) // 3600)
    minutes = int((delta % 3600) // 60)
    return f"{days}d {hours}h {minutes}m"


def get_gpu_info() -> dict | None:
    """Parse nvidia-smi CSV output. Returns None if unavailable."""
    result = run_command([
        "/usr/bin/nvidia-smi",
        "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,power.limit,fan.speed",
        "--format=csv,noheader,nounits",
    ], timeout=5, mask=False)

    if not result["success"]:
        return None

    line = result["output"].strip().split("\n")[0] if result["output"] else ""
    if not line:
        return None

    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 7:
        return None

    def safe_int(s):
        try:
            return int(float(s))
        except (ValueError, TypeError):
            return None

    def safe_float(s):
        try:
            return round(float(s), 1)
        except (ValueError, TypeError):
            return None

    return {
        "name": parts[0],
        "temperature": safe_int(parts[1]),
        "utilization": safe_int(parts[2]),
        "memory_used": safe_int(parts[3]),
        "memory_total": safe_int(parts[4]),
        "power_draw": safe_float(parts[5]),
        "power_limit": safe_float(parts[6]),
        "fan_speed": safe_int(parts[7]) if len(parts) > 7 else None,
    }


def get_system_sensors() -> dict | None:
    """Parse `sensors` command output for thermal overview.

    Returns dict with cpu_package, cpu_core_avg, cpu_core_max,
    nvme_temp, system_temp, board_temp_avg, cores (per-core list).
    Returns None if sensors command fails.
    """
    import re

    result = run_command(["/usr/bin/sensors"], timeout=5, mask=False)
    if not result["success"] or not result["output"]:
        return None

    output = result["output"]
    data = {
        "cpu_package": None,
        "cpu_core_avg": None,
        "cpu_core_max": None,
        "nvme_temp": None,
        "system_temp": None,
        "board_temp_avg": None,
        "cores": [],
    }

    def parse_temp(line: str) -> float | None:
        m = re.search(r'[+\-]?([\d.]+)\s*°?C', line)
        if m:
            val = float(m.group(1))
            if val > 0:
                return round(val, 1)
        return None

    # Split into sections by chip header (non-indented lines after blank lines)
    sections = re.split(r'\n(?=\S)', output)

    core_temps = []
    jc42_temps = []

    for section in sections:
        lines = section.strip().split('\n')
        if not lines:
            continue
        header = lines[0].lower()

        if 'coretemp' in header or 'k10temp' in header:
            for line in lines[1:]:
                lower = line.lower().strip()
                if lower.startswith('package id') or lower.startswith('tctl') or lower.startswith('tdie'):
                    t = parse_temp(line)
                    if t is not None:
                        data["cpu_package"] = t
                elif lower.startswith('core'):
                    t = parse_temp(line)
                    if t is not None:
                        core_match = re.match(r'Core\s+(\d+)', line, re.IGNORECASE)
                        core_num = int(core_match.group(1)) if core_match else len(core_temps)
                        core_temps.append(t)
                        data["cores"].append({"core": core_num, "temp": t})

        elif 'nvme' in header:
            for line in lines[1:]:
                if line.lower().strip().startswith('composite'):
                    t = parse_temp(line)
                    if t is not None:
                        data["nvme_temp"] = t
                        break

        elif 'acpitz' in header or 'acpi' in header:
            for line in lines[1:]:
                lower = line.lower().strip()
                if lower.startswith('temp1') or lower.startswith('temp'):
                    t = parse_temp(line)
                    if t is not None:
                        data["system_temp"] = t
                        break

        elif 'jc42' in header:
            for line in lines[1:]:
                lower = line.lower().strip()
                if lower.startswith('temp1') or lower.startswith('temp'):
                    t = parse_temp(line)
                    if t is not None:
                        jc42_temps.append(t)

    if core_temps:
        data["cpu_core_avg"] = round(sum(core_temps) / len(core_temps), 1)
        data["cpu_core_max"] = round(max(core_temps), 1)

    if jc42_temps:
        data["board_temp_avg"] = round(sum(jc42_temps) / len(jc42_temps), 1)

    return data


def get_db_info(db_path: str = "memory.db") -> list[dict]:
    """SQLite file info as a list. Includes main DB + scanned extras."""
    dbs = []
    p = Path(db_path)
    if p.exists():
        dbs.append({
            "name": p.name,
            "path": str(p.resolve()),
            "size_kb": round(p.stat().st_size / 1024, 1),
            "last_modified": datetime.fromtimestamp(
                p.stat().st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC"),
        })

    # Scan known data directories for additional .db files
    for scan_dir in ["/opt/ai-assistant/data", "/opt/ai-data"]:
        scan_path = Path(scan_dir)
        if not scan_path.is_dir():
            continue
        try:
            for db_file in scan_path.rglob("*.db"):
                if db_file.resolve() == p.resolve():
                    continue
                stat = db_file.stat()
                dbs.append({
                    "name": db_file.name,
                    "path": str(db_file),
                    "size_kb": round(stat.st_size / 1024, 1),
                    "last_modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M:%S UTC"),
                })
        except (PermissionError, OSError):
            pass

    return dbs


def get_version_info(repo_dir: str = "/opt/ai-assistant/services/assistant-core") -> dict:
    """Git commit hash for version display."""
    result = run_command(
        ["/usr/bin/git", "rev-parse", "--short", "HEAD"],
        timeout=5, mask=False, cwd=repo_dir,
    )
    return {
        "commit": result["output"].strip() if result["success"] else "unknown",
    }
