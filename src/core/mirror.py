from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from core import config

_ALWAYS_EXCLUDES = [
    "/proc", "/sys", "/dev", "/run",
    "/tmp", "/var/tmp", "/lost+found",
    "/mnt", "/media",
    "/home/*/.cache", "/home/*/.thumbnails",
    "/root/.cache",
]

OPTIONAL_ITEMS = [
    {"key": "flatpak_sys",   "path": "/var/lib/flatpak",                 "label": "/var/lib/flatpak",                   "default": True},
    {"key": "flatpak_data",  "path": "/home/*/.var/app",                 "label": "/home/*/.var/app",                   "default": True},
    {"key": "downloads",     "path": "/home/*/Downloads",                "label": "/home/*/Downloads",                  "default": True},
    {"key": "docker",        "path": "/var/lib/docker",                  "label": "/var/lib/docker",                    "default": False},
    {"key": "libvirt",       "path": "/var/lib/libvirt/images",          "label": "/var/lib/libvirt/images",            "default": False},
    {"key": "steam",         "path": "/home/*/.steam",                   "label": "/home/*/.steam",                     "default": False},
    {"key": "steam2",        "path": "/home/*/.local/share/Steam",       "label": "/home/*/.local/share/Steam",         "default": False},
    {"key": "lutris",        "path": "/home/*/.local/share/lutris",      "label": "/home/*/.local/share/lutris",        "default": False},
    {"key": "vbox",          "path": "/home/*/VirtualBox VMs",           "label": "/home/*/VirtualBox VMs",             "default": False},
    {"key": "wine",          "path": "/home/*/.wine",                    "label": "/home/*/.wine",                      "default": False},
    {"key": "pkg_cache",     "path": "/var/cache",                       "label": "/var/cache (кэши пакетов)",          "default": False},
    {"key": "pg",            "path": "/var/lib/postgresql",              "label": "/var/lib/postgresql",                "default": False},
    {"key": "mysql",         "path": "/var/lib/mysql",                   "label": "/var/lib/mysql",                     "default": False},
]


def get_root_filesystem() -> str | None:
    try:
        out = subprocess.check_output(
            ["findmnt", "-n", "-o", "FSTYPE", "/"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return out or None
    except Exception:
        return None


def get_dest_filesystem(dest_path: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["findmnt", "-n", "-o", "FSTYPE", "--target", dest_path],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return out or None
    except Exception:
        return None


def get_root_device() -> str | None:
    try:
        out = subprocess.check_output(
            ["findmnt", "-n", "-o", "SOURCE", "/"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return out or None
    except Exception:
        return None


def get_root_partition_disk(device: str) -> str:
    return re.sub(r"p?\d+$", "", device)


def _partition_path(device: str, number: int) -> str:
    suffix = f"p{number}" if re.search(r"(nvme\d+n\d+|mmcblk\d+)$", device) else str(number)
    return f"{device}{suffix}"


def list_btrfs_subvolumes() -> list[dict]:
    try:
        out = subprocess.check_output(
            ["btrfs", "subvolume", "list", "/"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return []
    result = []
    for line in out.splitlines():
        m = re.match(r"ID\s+(\d+).*path\s+(.+)", line)
        if m:
            result.append({"id": int(m.group(1)), "path": m.group(2)})
    return result


def is_uefi() -> bool:
    return os.path.isdir("/sys/firmware/efi")


def list_available_disks(exclude_root: bool = True) -> list[dict]:
    try:
        out = subprocess.check_output(
            ["lsblk", "-d", "-n", "-o", "NAME,SIZE,MODEL,TYPE"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return []
    root_dev = get_root_partition_disk(get_root_device() or "")
    result = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 2:
            continue
        name, size = parts[0], parts[1]
        dtype = parts[3].strip() if len(parts) > 3 else ""
        model = parts[2] if len(parts) > 2 else ""
        if dtype not in ("", "disk"):
            continue
        dev_path = f"/dev/{name}"
        if exclude_root and root_dev and dev_path.startswith(root_dev):
            continue
        result.append({"name": name, "device": dev_path, "size": size, "model": model.strip()})
    return result


def detect_mirror_type(folder: str) -> dict | None:
    p = Path(folder)
    if not p.is_dir():
        return None
    has_pt = (p / "partition_table.sfdisk").exists()
    has_efi = (p / "boot-efi.tar").exists()

    btrfs_prevs = [f for f in p.glob(".snap_*_prev") if f.is_dir()]
    if btrfs_prevs:
        names = [f.name[len(".snap_"):-len("_prev")] for f in btrfs_prevs]
        return {"type": "btrfs_recv", "subvols": names, "has_pt": has_pt, "has_efi": has_efi}

    btrfs_files = list(p.glob("*.btrfs"))
    if btrfs_files:
        return {"type": "btrfs", "subvols": [f.stem for f in btrfs_files], "has_pt": has_pt, "has_efi": has_efi}

    if (p / "rootfs").is_dir():
        return {"type": "rsync", "fs": "ext4", "has_pt": has_pt, "has_efi": has_efi}
    tar_files = list(p.glob("rootfs-*.tar.gz"))
    if tar_files:
        return {"type": "tar", "fs": "ext4", "path": str(tar_files[0]), "has_pt": has_pt, "has_efi": has_efi}
    return None


def _build_rsync_excludes(optional_includes: list[str]) -> list[str]:
    excludes = list(_ALWAYS_EXCLUDES)
    for item in OPTIONAL_ITEMS:
        if item["key"] not in optional_includes:
            excludes.append(item["path"])
    return excludes


def _run_mirror_async(cmd: list[str], on_line, on_done, cwd: str | None = None):
    def _worker():
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=cwd,
            )
            for line in proc.stdout:
                if on_line:
                    import gi
                    gi.require_version("GLib", "2.0")
                    from gi.repository import GLib
                    GLib.idle_add(on_line, line)
            proc.wait()
            ok = proc.returncode == 0
        except Exception as e:
            ok = False
            if on_line:
                import gi
                gi.require_version("GLib", "2.0")
                from gi.repository import GLib
                GLib.idle_add(on_line, f"Ошибка: {e}\n")
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
        GLib.idle_add(on_done, ok)
    threading.Thread(target=_worker, daemon=True).start()


def mirror_ext4_rsync(dest_dir: str, optional_includes: list[str], on_line, on_done, run_fn=None):
    rootfs = str(Path(dest_dir) / "rootfs") + "/"
    excludes = _build_rsync_excludes(optional_includes)
    cmd = ["rsync", "-aAX", "--delete", "--info=progress2", "--no-inc-recursive"]
    for ex in excludes:
        cmd += ["--exclude", ex]
    cmd += ["/", rootfs]
    if run_fn is not None:
        run_fn(cmd, on_line, on_done)
    else:
        _run_mirror_async(cmd, on_line, on_done)


def mirror_ext4_tar(dest_dir: str, optional_includes: list[str], on_line, on_done, run_fn=None):
    date_str = datetime.now().strftime("%Y-%m-%dT%H-%M")
    out_file = str(Path(dest_dir) / f"rootfs-{date_str}.tar.gz")
    excludes = _build_rsync_excludes(optional_includes)
    cmd = ["tar", "-czpf", out_file]
    for ex in excludes:
        cmd += [f"--exclude={ex}"]
    cmd.append("/")
    if run_fn is not None:
        run_fn(cmd, on_line, on_done)
    else:
        _run_mirror_async(cmd, on_line, on_done)


def mirror_btrfs_stream(subvolumes: list[str], dest_dir: str, on_line, on_done, run_fn=None):
    dest = Path(dest_dir)
    toplevel = "/tmp/.btrfs_mirror_toplevel"

    script_lines = [
        "set -e",
        f'trap \'umount {shlex.quote(toplevel)} 2>/dev/null; rmdir {shlex.quote(toplevel)} 2>/dev/null\' EXIT',
        f'BTRFS_DEV=$(findmnt -n -o SOURCE / | sed \'s/\\[.*\\]//\')',
        f'mkdir -p {shlex.quote(toplevel)}',
        f'mount -t btrfs -o subvolid=5 "$BTRFS_DEV" {shlex.quote(toplevel)}',
        '',
    ]

    total = len(subvolumes)
    for idx, subvol in enumerate(subvolumes, 1):
        name = Path(subvol).name or subvol.replace("/", "_")
        snap     = shlex.quote(f"{toplevel}/.snap_{name}")
        sv_path  = shlex.quote(f"{toplevel}/{subvol}")
        out_file = shlex.quote(str(dest / f"{name}.btrfs"))
        script_lines += [
            f'echo ""',
            f'echo "Субволюм {idx}/{total}: {subvol}"',
            f'if [ -d {sv_path} ]; then',
            f'  btrfs subvolume snapshot -r {sv_path} {snap}',
            f'  _SZ=$(du -sh {snap} 2>/dev/null | cut -f1)',
            f'  echo "Объём данных: $_SZ — сохранение в файл {name}.btrfs..."',
            f'  btrfs send {snap} > {out_file}',
            f'  btrfs subvolume delete {snap}',
            f'  echo "✔  Субволюм {idx}/{total} сохранён."',
            f'else',
            f'  echo "⚠  Субволюм не найден на диске, пропуск."',
            f'fi',
            '',
        ]

    script_lines.append('echo "Btrfs send завершён."')
    script = "\n".join(script_lines)

    if run_fn is not None:
        run_fn(["bash", "-c", script], on_line, on_done)
    else:
        _run_mirror_async(["bash", "-c", script], on_line, on_done)


def mirror_btrfs_send(subvolumes: list[str], dest_dir: str, on_line, on_done, run_fn=None):
    dest = Path(dest_dir)
    toplevel = "/tmp/.btrfs_mirror_toplevel"
    dest_q = shlex.quote(str(dest))

    script_lines = [
        "set -e",
        f'DEST_FS=$(findmnt -n -o FSTYPE --target {dest_q} 2>/dev/null || echo unknown)',
        f'if [ "$DEST_FS" != "btrfs" ]; then',
        f'  echo "Ошибка: папка назначения должна быть на Btrfs-разделе (обнаружен FS: $DEST_FS)"',
        f'  exit 1',
        f'fi',
        f'trap \'umount {shlex.quote(toplevel)} 2>/dev/null; rmdir {shlex.quote(toplevel)} 2>/dev/null\' EXIT',
        f'BTRFS_DEV=$(findmnt -n -o SOURCE / | sed \'s/\\[.*\\]//\')',
        f'mkdir -p {shlex.quote(toplevel)}',
        f'mount -t btrfs -o subvolid=5 "$BTRFS_DEV" {shlex.quote(toplevel)}',
        '',
    ]

    total = len(subvolumes)
    for idx, subvol in enumerate(subvolumes, 1):
        name = Path(subvol).name or subvol.replace("/", "_")
        new_snap  = shlex.quote(f"{toplevel}/.snap_{name}_new")
        prev_snap = shlex.quote(f"{toplevel}/.snap_{name}_prev")
        dest_new  = shlex.quote(str(dest / f".snap_{name}_new"))
        dest_prev = shlex.quote(str(dest / f".snap_{name}_prev"))
        sv_path   = shlex.quote(f"{toplevel}/{subvol}")

        script_lines += [
            f'echo ""',
            f'echo "Субволюм {idx}/{total}: {subvol}"',
            f'if [ -d {sv_path} ]; then',
            f'  [ -d {new_snap} ] && btrfs subvolume delete {new_snap} || true',
            f'  [ -d {dest_new} ] && btrfs subvolume delete {dest_new} || true',
            f'  btrfs subvolume snapshot -r {sv_path} {new_snap}',
            f'  _SZ=$(du -sh {new_snap} 2>/dev/null | cut -f1)',
            f'  echo "Объём данных: $_SZ"',
            f'  if [ -d {prev_snap} ] && [ -d {dest_prev} ]; then',
            f'    echo "Передача изменений с прошлого раза..."',
            f'    btrfs send --parent {prev_snap} {new_snap} | btrfs receive {dest_q}/',
            f'  else',
            f'    echo "Первая копия — передача всех данных (это займёт время)..."',
            f'    btrfs send {new_snap} | btrfs receive {dest_q}/',
            f'  fi',
            f'  echo "✔  Субволюм {idx}/{total} скопирован."',
            f'  [ -d {prev_snap} ] && btrfs subvolume delete {prev_snap} || true',
            f'  [ -d {dest_prev} ] && btrfs subvolume delete {dest_prev} || true',
            f'  mv {new_snap} {prev_snap}',
            f'  mv {dest_new} {dest_prev}',
            f'else',
            f'  echo "⚠  Субволюм не найден на диске, пропуск."',
            f'fi',
            '',
        ]

    script_lines.append('echo "Btrfs зеркало обновлено."')
    script = "\n".join(script_lines)

    if run_fn is not None:
        run_fn(["bash", "-c", script], on_line, on_done)
    else:
        _run_mirror_async(["bash", "-c", script], on_line, on_done)


def save_partition_table(device: str, dest_dir: str) -> bool:
    out_file = str(Path(dest_dir) / "partition_table.sfdisk")
    try:
        result = subprocess.run(
            ["sfdisk", "--dump", device],
            capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode != 0:
            return False
        Path(out_file).write_text(result.stdout, encoding="utf-8")
        return True
    except Exception:
        return False


def save_efi_partition(dest_dir: str, on_line, on_done, run_fn=None):
    out_file = str(Path(dest_dir) / "boot-efi.tar")
    cmd = ["tar", "-czpf", out_file, "/boot/efi"]
    if run_fn is not None:
        run_fn(cmd, on_line, on_done)
    else:
        _run_mirror_async(cmd, on_line, on_done)


def generate_newsync_ext4(dest_dir: str, device: str, uefi: bool, fmt: str) -> bool:
    disk = get_root_partition_disk(device)
    lines = [
        "#!/bin/bash",
        "# newsync.sh — скрипт восстановления EXT4-системы из зеркала ALT Booster",
        "# Запускать с LiveUSB ALT Linux от имени root",
        "set -e",
        "",
        'MIRROR_DIR="$(dirname "$(realpath "$0")")"',
        "",
        "lsblk -d -n -o NAME,SIZE,MODEL",
        'read -rp "Введите имя целевого диска (например sda, nvme0n1): " TARGET_DISK',
        'TARGET="/dev/${TARGET_DISK}"',
        "",
        'echo "ВНИМАНИЕ: диск $TARGET будет перезаписан. Продолжить? (yes/no)"',
        'read -r CONFIRM',
        '[ "$CONFIRM" = "yes" ] || exit 1',
        "",
        "# Восстановление таблицы разделов",
        'sfdisk < "$MIRROR_DIR/partition_table.sfdisk" "$TARGET"',
        "sleep 2",
        "partprobe $TARGET",
        "",
    ]

    if uefi:
        lines += [
            "# Определение разделов (GPT: 1=EFI, 2=root или аналог)",
            'EFI_PART="${TARGET}1"',
            'ROOT_PART="${TARGET}2"',
            "# Форматирование",
            "mkfs.fat -F32 $EFI_PART",
            "mkfs.ext4 -F $ROOT_PART",
            "# Монтирование",
            "mkdir -p /mnt/target",
            "mount $ROOT_PART /mnt/target",
            "mkdir -p /mnt/target/boot/efi",
            "mount $EFI_PART /mnt/target/boot/efi",
            "",
        ]
    else:
        lines += [
            'ROOT_PART="${TARGET}1"',
            "mkfs.ext4 -F $ROOT_PART",
            "mkdir -p /mnt/target",
            "mount $ROOT_PART /mnt/target",
            "",
        ]

    if fmt == "tar":
        lines += [
            "# Распаковка архива",
            'tar -xzpf "$MIRROR_DIR"/rootfs-*.tar.gz -C /mnt/target/',
        ]
    else:
        lines += [
            "# Синхронизация файловой системы",
            'rsync -aAX "$MIRROR_DIR/rootfs/" /mnt/target/',
        ]

    lines += [
        "",
        "# Монтирование виртуальных ФС для chroot",
        "mount --bind /dev /mnt/target/dev",
        "mount -t proc proc /mnt/target/proc",
        "mount -t sysfs sysfs /mnt/target/sys",
        "[ -d /sys/firmware/efi ] && mount --bind /sys/firmware/efi/efivars /mnt/target/sys/firmware/efi/efivars || true",
        "",
        "# Установка загрузчика",
        f'chroot /mnt/target grub-install "{disk}"',
        "chroot /mnt/target update-grub",
        "",
        "# Размонтирование",
        "umount -R /mnt/target",
        'echo "Готово. Перезагрузитесь без LiveUSB."',
    ]
    try:
        script = Path(dest_dir) / "newsync.sh"
        script.write_text("\n".join(lines) + "\n", encoding="utf-8")
        script.chmod(0o755)
        return True
    except Exception:
        return False


def generate_newsync_btrfs(dest_dir: str, device: str, subvolumes: list[str], uefi: bool, mirror_type: str = "stream") -> bool:
    disk = get_root_partition_disk(device)
    names = [Path(sv).name or sv.replace("/", "_") for sv in subvolumes]

    lines = [
        "#!/bin/bash",
        "# newsync.sh — скрипт восстановления Btrfs-системы из зеркала ALT Booster",
        "# Запускать с LiveUSB ALT Linux от имени root",
        "set -e",
        "",
        'MIRROR_DIR="$(dirname "$(realpath "$0")")"',
        "",
        "lsblk -d -n -o NAME,SIZE,MODEL",
        'read -rp "Введите имя целевого диска (например sda, nvme0n1): " TARGET_DISK',
        'TARGET="/dev/${TARGET_DISK}"',
        "",
        'echo "ВНИМАНИЕ: диск $TARGET будет перезаписан. Продолжить? (yes/no)"',
        'read -r CONFIRM',
        '[ "$CONFIRM" = "yes" ] || exit 1',
        "",
        'sfdisk < "$MIRROR_DIR/partition_table.sfdisk" "$TARGET"',
        "sleep 2",
        "partprobe $TARGET",
        "",
    ]

    if uefi:
        lines += [
            'EFI_PART="${TARGET}1"',
            'ROOT_PART="${TARGET}2"',
            "mkfs.fat -F32 $EFI_PART",
            "mkfs.btrfs -f $ROOT_PART",
            "",
        ]
    else:
        lines += [
            'ROOT_PART="${TARGET}1"',
            "mkfs.btrfs -f $ROOT_PART",
            "",
        ]

    if mirror_type == "recv":
        lines += [
            "mkdir -p /mnt/btrfs_root",
            "mount -o subvolid=5 $ROOT_PART /mnt/btrfs_root",
            "",
        ]
        for name in names:
            lines += [
                f'echo "Восстановление: {name}"',
                f'btrfs send "$MIRROR_DIR/.snap_{name}_prev" | btrfs receive /mnt/btrfs_root/',
                f'mv /mnt/btrfs_root/.snap_{name}_prev /mnt/btrfs_root/{name}',
            ]
        lines += ["", "umount /mnt/btrfs_root", "rmdir /mnt/btrfs_root", ""]
        root_name = next((n for n in names if n in ("@", "root")), names[0] if names else "@")
        home_name = next((n for n in names if n in ("@home", "home")), None)
        lines += ["mkdir -p /mnt/target", f"mount -o subvol={root_name} $ROOT_PART /mnt/target"]
        if home_name:
            lines += ["mkdir -p /mnt/target/home", f"mount -o subvol={home_name} $ROOT_PART /mnt/target/home"]
        if uefi:
            lines += ["mkdir -p /mnt/target/boot/efi", "mount $EFI_PART /mnt/target/boot/efi"]
    else:
        lines += ["mkdir -p /mnt/target", "mount $ROOT_PART /mnt/target"]
        if uefi:
            lines += ["mkdir -p /mnt/target/boot/efi", "mount $EFI_PART /mnt/target/boot/efi"]
        lines.append("")
        for name in names:
            lines.append(f'btrfs receive /mnt/target < "$MIRROR_DIR/{name}.btrfs"')

    lines += [
        "",
        "mount --bind /dev /mnt/target/dev",
        "mount -t proc proc /mnt/target/proc",
        "mount -t sysfs sysfs /mnt/target/sys",
        "[ -d /sys/firmware/efi ] && mount --bind /sys/firmware/efi/efivars /mnt/target/sys/firmware/efi/efivars || true",
        "",
        f'chroot /mnt/target grub-install "{disk}"',
        "chroot /mnt/target update-grub",
        "",
        "umount -R /mnt/target",
        'echo "Готово. Перезагрузитесь без LiveUSB."',
    ]
    try:
        script = Path(dest_dir) / "newsync.sh"
        script.write_text("\n".join(lines) + "\n", encoding="utf-8")
        script.chmod(0o755)
        return True
    except Exception:
        return False


def restore_to_disk(mirror_dir: str, target_device: str, on_line, on_done):
    info = detect_mirror_type(mirror_dir)
    if not info:
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
        GLib.idle_add(on_line, "Ошибка: не удалось определить тип зеркала\n")
        GLib.idle_add(on_done, False)
        return

    script = Path(mirror_dir) / "newsync.sh"
    if not script.exists():
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
        GLib.idle_add(on_line, "Ошибка: newsync.sh не найден в папке зеркала\n")
        GLib.idle_add(on_done, False)
        return

    def _worker():
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        env = os.environ.copy()
        env["TARGET_DISK"] = target_device.lstrip("/dev/") if target_device.startswith("/dev/") else target_device
        env["NEWSYNC_AUTO"] = "1"

        auto_script = _build_auto_restore_script(mirror_dir, target_device, info)

        GLib.idle_add(on_line, f"Восстановление на {target_device}...\n")
        try:
            proc = subprocess.Popen(
                ["sudo", "-n", "bash", "-c", auto_script],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            for line in proc.stdout:
                GLib.idle_add(on_line, line)
            proc.wait()
            ok = proc.returncode == 0
        except Exception as e:
            GLib.idle_add(on_line, f"Ошибка: {e}\n")
            ok = False
        GLib.idle_add(on_done, ok)

    threading.Thread(target=_worker, daemon=True).start()


def _build_auto_restore_script(mirror_dir: str, target_device: str, info: dict) -> str:
    disk = get_root_partition_disk(target_device)
    uefi = is_uefi()
    is_btrfs = info["type"] in ("btrfs", "btrfs_recv")

    lines = [
        "set -e",
        f'MIRROR_DIR="{mirror_dir}"',
        f'TARGET="{target_device}"',
        "",
        f'sfdisk < "$MIRROR_DIR/partition_table.sfdisk" "{disk}"',
        "sleep 2",
        f"partprobe {disk}",
        "",
    ]

    if uefi:
        root_part = _partition_path(target_device, 2)
        efi_part = _partition_path(target_device, 1)
        lines += [
            f"mkfs.fat -F32 {efi_part}",
            f"mkfs.{'btrfs -f' if is_btrfs else 'ext4 -F'} {root_part}",
            "",
        ]
    else:
        root_part = _partition_path(target_device, 1)
        lines += [
            f"mkfs.{'btrfs -f' if is_btrfs else 'ext4 -F'} {root_part}",
            "",
        ]

    if info["type"] == "rsync":
        lines += ["mkdir -p /mnt/target", f"mount {root_part} /mnt/target"]
        if uefi:
            lines += [f"mkdir -p /mnt/target/boot/efi", f"mount {efi_part} /mnt/target/boot/efi"]
        lines.append(f'rsync -aAX "$MIRROR_DIR/rootfs/" /mnt/target/')

    elif info["type"] == "tar":
        lines += ["mkdir -p /mnt/target", f"mount {root_part} /mnt/target"]
        if uefi:
            lines += [f"mkdir -p /mnt/target/boot/efi", f"mount {efi_part} /mnt/target/boot/efi"]
        lines.append(f'tar -xzpf "$MIRROR_DIR"/rootfs-*.tar.gz -C /mnt/target/')

    elif info["type"] == "btrfs":
        lines += ["mkdir -p /mnt/target", f"mount {root_part} /mnt/target"]
        if uefi:
            lines += [f"mkdir -p /mnt/target/boot/efi", f"mount {efi_part} /mnt/target/boot/efi"]
        for subvol in info.get("subvols", []):
            lines.append(f'btrfs receive /mnt/target < "$MIRROR_DIR/{subvol}.btrfs"')

    elif info["type"] == "btrfs_recv":
        names = info.get("subvols", [])
        lines += ["mkdir -p /mnt/btrfs_root", f"mount -o subvolid=5 {root_part} /mnt/btrfs_root", ""]
        for name in names:
            lines += [
                f'btrfs send "$MIRROR_DIR/.snap_{name}_prev" | btrfs receive /mnt/btrfs_root/',
                f'mv /mnt/btrfs_root/.snap_{name}_prev /mnt/btrfs_root/{name}',
            ]
        lines += ["", "umount /mnt/btrfs_root", "rmdir /mnt/btrfs_root", ""]
        root_name = next((n for n in names if n in ("@", "root")), names[0] if names else "@")
        home_name = next((n for n in names if n in ("@home", "home")), None)
        lines += ["mkdir -p /mnt/target", f"mount -o subvol={root_name} {root_part} /mnt/target"]
        if home_name:
            lines += ["mkdir -p /mnt/target/home", f"mount -o subvol={home_name} {root_part} /mnt/target/home"]
        if uefi:
            lines += [f"mkdir -p /mnt/target/boot/efi", f"mount {efi_part} /mnt/target/boot/efi"]

    lines += [
        "",
        "mount --bind /dev /mnt/target/dev",
        "mount -t proc proc /mnt/target/proc",
        "mount -t sysfs sysfs /mnt/target/sys",
        "[ -d /sys/firmware/efi ] && mount --bind /sys/firmware/efi/efivars /mnt/target/sys/firmware/efi/efivars || true",
        "",
        f'chroot /mnt/target grub-install "{disk}"',
        "chroot /mnt/target update-grub",
        "umount -R /mnt/target",
        'echo "Восстановление завершено."',
    ]
    return "\n".join(lines)
