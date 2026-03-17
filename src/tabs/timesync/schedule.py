from __future__ import annotations

import datetime
import os
import shlex
import subprocess
from pathlib import Path

from core import config
from .borg import _borg_exe, borg_ssh_key_path, DEFAULT_EXCLUDES
from .snapshots import get_btrfs_mount_for_home, get_snapshots_dir


def _run_systemctl(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )


def write_systemd_units(repo_path: str, paths: list[str], calendar_expr: str) -> bool:
    d = config.SYSTEMD_USER_DIR
    d.mkdir(parents=True, exist_ok=True)
    passphrase = config.state_get("borg_passphrase", "") or ""
    ssh_key = borg_ssh_key_path()
    paths_str = " ".join(f'"{p}"' for p in paths)
    excludes_str = " ".join(
        f'--exclude "{os.path.expanduser(e)}"' for e in DEFAULT_EXCLUDES
    )
    borg_exe = _borg_exe()

    service_content = (
        "[Unit]\n"
        "Description=ALT Booster — резервное копирование\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"Environment=BORG_PASSPHRASE={passphrase}\n"
        f"Environment=BORG_RSH=ssh -i {ssh_key} -o StrictHostKeyChecking=accept-new\n"
        "ExecStart=/bin/bash -c '"
        "mkdir -p /tmp/altbooster-backup-meta && "
        "flatpak list --app --columns=application > /tmp/altbooster-backup-meta/flatpak-apps.txt 2>/dev/null; "
        "flatpak remotes --columns=name,url > /tmp/altbooster-backup-meta/flatpak-remotes.txt 2>/dev/null; "
        "rpm -qa --queryformat \"%%{NAME}\\n\" | sort -u > /tmp/altbooster-backup-meta/packages.txt 2>/dev/null; "
        "dconf dump / > /tmp/altbooster-backup-meta/dconf-full.ini 2>/dev/null; "
        f'{borg_exe} create --stats "{repo_path}::$(hostname)-$(date +%%Y-%%m-%%dT%%H-%%M)" '
        f"{paths_str} /tmp/altbooster-backup-meta {excludes_str}'\n"
    )

    timer_content = (
        "[Unit]\n"
        "Description=ALT Booster — таймер резервного копирования\n\n"
        "[Timer]\n"
        f"OnCalendar={calendar_expr}\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )

    service_file = d / "altbooster-backup.service"
    timer_file = d / "altbooster-backup.timer"
    try:
        service_file.write_text(service_content, encoding="utf-8")
        service_file.chmod(0o600)
        timer_file.write_text(timer_content, encoding="utf-8")
        return True
    except Exception:
        return False


def enable_systemd_timer() -> bool:
    try:
        _run_systemctl(["daemon-reload"])
        r = _run_systemctl(["enable", "--now", "altbooster-backup.timer"])
        return r.returncode == 0
    except Exception:
        return False


def disable_systemd_timer() -> bool:
    try:
        r = _run_systemctl(["disable", "--now", "altbooster-backup.timer"])
        return r.returncode == 0
    except Exception:
        return False


def is_timer_active() -> bool:
    try:
        r = _run_systemctl(["is-active", "altbooster-backup.timer"])
        return r.returncode == 0
    except Exception:
        return False


def get_timer_next_run() -> str | None:
    try:
        r = _run_systemctl(["show", "altbooster-backup.timer", "--property=NextElapseUSecRealtime"])
        for line in r.stdout.splitlines():
            if "=" in line:
                val = line.split("=", 1)[1].strip()
                if val and val != "0":
                    usec = int(val)
                    dt = datetime.datetime.fromtimestamp(usec / 1_000_000)
                    return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        pass
    return None


def write_btrfs_systemd_units(interval_hours: int, keep_count: int) -> bool:
    d = config.SYSTEMD_USER_DIR
    d.mkdir(parents=True, exist_ok=True)

    mount_point = get_btrfs_mount_for_home()
    snapshots_dir = get_snapshots_dir()
    if not mount_point:
        return False

    prune_n = max(1, keep_count)
    prune_cmd = (
        f"ls -1dt {shlex.quote(str(snapshots_dir))}/home-* 2>/dev/null | "
        f"tail -n +{prune_n + 1} | "
        f"xargs -r btrfs subvolume delete"
    )

    timestamp_format = "$(date +'%Y-%m-%dT%H-%M-%S')"
    snapshot_path = str(snapshots_dir / f"home-{timestamp_format}")
    snapshot_cmd = (
        f"mkdir -p {shlex.quote(str(snapshots_dir))} && "
        f"btrfs subvolume snapshot -r {shlex.quote(mount_point)} {snapshot_path}"
    )

    service_content = (
        "[Unit]\n"
        "Description=ALT Booster - Btrfs Snapshot\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart=pkexec bash -c '{snapshot_cmd}'\n"
        f"ExecStartPost=-pkexec bash -c '{prune_cmd}'\n"
    )

    calendar_map = {1: "hourly", 6: "*-*-* 0/6:00:00", 24: "daily"}
    calendar_expr = calendar_map.get(interval_hours, "hourly")

    timer_content = (
        "[Unit]\n"
        "Description=ALT Booster - Btrfs Snapshot Timer\n\n"
        "[Timer]\n"
        f"OnCalendar={calendar_expr}\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )

    try:
        (d / "altbooster-btrfs.service").write_text(service_content, encoding="utf-8")
        (d / "altbooster-btrfs.timer").write_text(timer_content, encoding="utf-8")
        return True
    except Exception:
        return False


def enable_btrfs_timer() -> bool:
    _run_systemctl(["daemon-reload"])
    r = _run_systemctl(["enable", "--now", "altbooster-btrfs.timer"])
    return r.returncode == 0


def disable_btrfs_timer() -> bool:
    r = _run_systemctl(["disable", "--now", "altbooster-btrfs.timer"])
    return r.returncode == 0


def is_btrfs_timer_active() -> bool:
    r = _run_systemctl(["is-active", "altbooster-btrfs.timer"])
    return r.returncode == 0


def get_btrfs_timer_next_run() -> str | None:
    try:
        r = _run_systemctl(["show", "altbooster-btrfs.timer", "--property=NextElapseUSecRealtime"])
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if "=" in line:
                    val = line.split("=", 1)[1].strip()
                    if val and val != "0":
                        usec = int(val)
                        dt = datetime.datetime.fromtimestamp(usec / 1_000_000)
                        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        pass
    return None
