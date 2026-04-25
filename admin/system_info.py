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
        "freq_mhz": round(freq.current) if freq else None,
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
        "temp_c": safe_int(parts[1]),
        "util_percent": safe_int(parts[2]),
        "vram_used_mb": safe_int(parts[3]),
        "vram_total_mb": safe_int(parts[4]),
        "power_w": safe_float(parts[5]),
        "power_limit_w": safe_float(parts[6]),
        "fan_percent": safe_int(parts[7]) if len(parts) > 7 else None,
    }


def get_db_info(db_path: str = "memory.db") -> dict:
    """SQLite file info + scan for .db files in data directories."""
    p = Path(db_path)
    info: dict = {
        "type": "sqlite",
        "path": str(p.resolve()) if p.exists() else db_path,
        "exists": p.exists(),
        "size_kb": round(p.stat().st_size / 1024, 1) if p.exists() else 0,
        "last_modified": (
            datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if p.exists() else None
        ),
        "extra_dbs": [],
    }

    # Scan known data directories for additional .db files
    for scan_dir in ["/opt/ai-assistant/data", "/opt/ai-data"]:
        scan_path = Path(scan_dir)
        if not scan_path.is_dir():
            continue
        try:
            for db_file in scan_path.rglob("*.db"):
                stat = db_file.stat()
                info["extra_dbs"].append({
                    "path": str(db_file),
                    "size_kb": round(stat.st_size / 1024, 1),
                })
        except (PermissionError, OSError):
            pass

    return info


def get_version_info(repo_dir: str = "/opt/ai-assistant/services/assistant-core") -> dict:
    """Git commit hash for version display."""
    result = run_command(
        ["/usr/bin/git", "rev-parse", "--short", "HEAD"],
        timeout=5, mask=False, cwd=repo_dir,
    )
    return {
        "commit": result["output"].strip() if result["success"] else "unknown",
    }
