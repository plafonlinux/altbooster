from __future__ import annotations

import os
import shlex
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from gi.repository import GLib

from core import config
from core import privileges


def get_btrfs_mount_for_home() -> str | None:
    home = os.path.expanduser("~")
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "TARGET", "--target", home, "--types", "btrfs"],
            capture_output=True, text=True, check=True, encoding="utf-8",
        )
        return result.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


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


def btrfs_snapshot_create(on_line, on_done) -> None:
    mount_point = get_btrfs_mount_for_home()
    if not mount_point:
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
