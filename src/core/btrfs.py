from __future__ import annotations

import os
import pwd
import shlex
import subprocess
import threading
import traceback
from datetime import datetime
from pathlib import Path


from gi.repository import GLib

from core import config
from core import privileges


def _real_home() -> Path:
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except Exception:
        return Path(os.path.expanduser("~")).resolve()


def get_btrfs_mount_for_home() -> str | None:
    home = _real_home()
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None

    best: Path | None = None
    for line in lines:
        fields = line.split()
        try:
            sep = fields.index("-")
            fstype = fields[sep + 1]
            mountpoint_str = fields[4]
        except (ValueError, IndexError):
            continue
        if fstype != "btrfs":
            continue
        try:
            mp = Path(mountpoint_str).resolve()
        except Exception:
            continue
        if not mp.is_absolute() or not mp.is_dir():
            continue
        try:
            home.relative_to(mp)
        except ValueError:
            continue
        if best is None or len(mp.parts) > len(best.parts):
            best = mp

    return str(best) if best else None


def is_home_on_btrfs() -> bool:
    return get_btrfs_mount_for_home() is not None


def get_snapshots_dir() -> Path:
    conf_dir = config.state_get("btrfs_snapshots_dir")
    if conf_dir:
        return Path(conf_dir)
    mount_point = get_btrfs_mount_for_home()
    if mount_point:
        return Path(mount_point) / ".snapshots" / "altbooster"
    return Path.home() / ".local" / "share" / "altbooster" / "btrfs-snapshots"


def _validate_mount_point(mp: str) -> bool:
    return Path(mp).is_absolute() and "'" not in mp and "\n" not in mp


def btrfs_snapshot_create(on_line, on_done) -> None:
    mount_point = get_btrfs_mount_for_home()
    if not mount_point or not _validate_mount_point(mount_point):
        GLib.idle_add(on_line, "✘ $HOME не находится на Btrfs.\n")
        GLib.idle_add(on_done, False)
        return

    snapshots_dir = get_snapshots_dir()
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    snapshot_path = snapshots_dir / f"home-{timestamp}"

    cmd_str = (
        f"mkdir -p {shlex.quote(str(snapshots_dir))} && "
        f"btrfs subvolume snapshot -r {shlex.quote(mount_point)} {shlex.quote(str(snapshot_path))}"
    )

    privileges.run_privileged(["bash", "-c", cmd_str], on_line, on_done)


def btrfs_snapshot_list(on_done) -> None:
    mount_point = get_btrfs_mount_for_home()
    snapshots_dir = get_snapshots_dir()

    if not mount_point:
        GLib.idle_add(on_done, [])
        return

    output_lines: list[str] = []

    def _on_line(line: str) -> None:
        output_lines.append(line)

    def _on_list_done(success: bool) -> None:
        if not success:
            GLib.idle_add(on_done, [])
            return

        snapshots = []
        for line in output_lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 14:
                continue

            try:
                path_idx = parts.index("path")
                snapshot_path_str = " ".join(parts[path_idx + 1:])
            except ValueError:
                continue

            try:
                full_path = Path(mount_point) / snapshot_path_str
                if not full_path.name.startswith("home-"):
                    continue
                if full_path.parent != snapshots_dir:
                    continue
            except Exception:
                continue

            name = full_path.name
            try:
                date_str = name[len("home-"):]
                dt_obj = datetime.strptime(date_str, "%Y-%m-%dT%H-%M-%S")
                date_formatted = dt_obj.strftime("%d %B %Y, %H:%M")
            except ValueError:
                date_formatted = name

            snapshots.append({
                "name": name,
                "path": str(full_path),
                "date_str": date_formatted,
            })

        snapshots.sort(key=lambda x: x["name"], reverse=True)
        GLib.idle_add(on_done, snapshots)

    privileges.run_privileged(["btrfs", "subvolume", "list", "-s", mount_point], _on_line, _on_list_done)


def btrfs_snapshot_delete(snapshot_path: str, on_line, on_done) -> None:
    privileges.run_privileged(["btrfs", "subvolume", "delete", snapshot_path], on_line, on_done)


def btrfs_snapshot_restore(snapshot_path: str, target_dir: str, on_line, on_done) -> None:
    def _worker() -> None:
        try:
            Path(target_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            GLib.idle_add(on_line, f"✘ Ошибка подготовки: {e}\n")
            GLib.idle_add(on_done, False)
            return

        import getpass
        username = getpass.getuser()
        user_dir = Path(snapshot_path) / username
        source = str(user_dir if user_dir.exists() else Path(snapshot_path)) + os.sep
        target = str(Path(target_dir)) + os.sep
        privileges.run_privileged(["rsync", "-aAX", "--delete", source, target], on_line, on_done)

    threading.Thread(target=_worker, daemon=True).start()


def _parse_btrfs_size(s: str) -> int | None:
    s = s.strip()
    for unit, mult in (("TiB", 1 << 40), ("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10), ("B", 1)):
        if s.endswith(unit):
            try:
                return int(float(s[: -len(unit)].strip()) * mult)
            except ValueError:
                return None
    return None


def btrfs_snapshot_size(snapshot_path: str, on_done) -> None:
    output_lines: list[str] = []

    def _on_line(line: str) -> None:
        output_lines.append(line)

    def _on_size_done(success: bool) -> None:
        size = None
        if success:
            for line in output_lines:
                parts = line.split()
                if not parts or parts[0] == "Total":
                    continue
                if len(parts) >= 2:
                    size = _parse_btrfs_size(parts[1])
                    break
        GLib.idle_add(on_done, size)

    privileges.run_privileged(
        ["btrfs", "filesystem", "du", "--summarize", snapshot_path],
        _on_line, _on_size_done,
    )


def write_btrfs_systemd_units(interval_hours: int, keep_count: int) -> bool:
    d = config.SYSTEMD_USER_DIR
    d.mkdir(parents=True, exist_ok=True)

    mount_point = get_btrfs_mount_for_home()
    snapshots_dir = get_snapshots_dir()
    if not mount_point or not _validate_mount_point(mount_point):
        return False

    prune_n = max(1, keep_count)

    def _esc_systemd(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$")

    snaps_q = shlex.quote(str(snapshots_dir))
    mount_q = shlex.quote(mount_point)

    snapshot_cmd = (
        f"SNAPSDIR={snaps_q}; "
        f"mkdir -p \"$SNAPSDIR\" && "
        f"btrfs subvolume snapshot -r {mount_q} "
        f"\"$SNAPSDIR/home-$(date +'%%Y-%%m-%%dT%%H-%%M-%%S')\""
    )

    prune_cmd = (
        f"ls -1dt {snaps_q}/home-* 2>/dev/null | "
        f"tail -n +{prune_n + 1} | "
        f"xargs -r btrfs subvolume delete"
    )

    service_content = (
        "[Unit]\n"
        "Description=ALT Booster - Btrfs Snapshot\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f'ExecStart=pkexec bash -c "{_esc_systemd(snapshot_cmd)}"\n'
        f'ExecStartPost=-pkexec bash -c "{_esc_systemd(prune_cmd)}"\n'
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


def _run_systemctl(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )


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
    import time
    try:
        r = _run_systemctl(["show", "altbooster-btrfs.timer",
                            "--property=NextElapseUSecRealtime",
                            "--property=NextElapseUSecMonotonic"])
        if r.returncode != 0:
            return None
        props: dict[str, str] = {}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
        realtime = int(props.get("NextElapseUSecRealtime", "0") or "0")
        if realtime:
            return datetime.fromtimestamp(realtime / 1_000_000).strftime("%d.%m.%Y %H:%M")
        monotonic = int(props.get("NextElapseUSecMonotonic", "0") or "0")
        if monotonic:
            realtime_usec = int((time.time() - time.monotonic()) * 1_000_000) + monotonic
            return datetime.fromtimestamp(realtime_usec / 1_000_000).strftime("%d.%m.%Y %H:%M")
    except Exception:
        config.log_exception("get_timer_next_run")
    return None
